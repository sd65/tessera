"""SQLite schema, upserts, the two selection queries, and everything `status`
prints.

The status/attempts split is not documentation — see the WHERE clause in
`select_retryable`, which is the whole reason the permanent/retryable distinction
in transcript.py exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import config
from .transcript import BLOCKED, OK, RATE_LIMITED, RETRYABLE_STATUSES

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  video_id      TEXT PRIMARY KEY,
  channel_id    TEXT NOT NULL,
  channel_name  TEXT,
  title         TEXT NOT NULL,
  description   TEXT,
  published_at  TEXT NOT NULL,
  url           TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcripts (
  video_id    TEXT PRIMARY KEY REFERENCES videos(video_id),
  lang        TEXT,
  kind        TEXT,
  status      TEXT NOT NULL,
  text        TEXT,
  attempts    INTEGER DEFAULT 0,
  error       TEXT,
  fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
  video_id        TEXT PRIMARY KEY REFERENCES videos(video_id),
  status          TEXT NOT NULL,
  text            TEXT,
  normalized_text TEXT,
  model           TEXT,
  attempts        INTEGER DEFAULT 0,
  error           TEXT,
  generated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_published   ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_channel     ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_status ON transcripts(status, attempts);
CREATE INDEX IF NOT EXISTS idx_summaries_status   ON summaries(status, attempts);

-- One row per real transcript request, for the hourly rate-limit ceiling.
-- Deliberately not derived from transcripts.fetched_at: that column is stamped
-- when a video is merely *claimed*, and a row cannot hold two attempts inside
-- one window, so counting rows there would both over- and under-count.
CREATE TABLE IF NOT EXISTS fetch_attempts (
  attempted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fetch_attempts ON fetch_attempts(attempted_at);
"""

# Marks a video as ingested before any transcript attempt, so a crash mid-run
# cannot cause a re-ingest on the next pass.
PENDING = "pending"

UPSERT_TRANSCRIPT = f"""
INSERT INTO transcripts (video_id, lang, kind, status, text, attempts, error, fetched_at)
VALUES (?, NULL, NULL, '{PENDING}', NULL, 0, NULL, ?)
ON CONFLICT(video_id) DO NOTHING
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_ago(seconds: float) -> str:
    """Same shape as `_now()`, so timestamps compare correctly as strings."""
    when = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    """Open the corpus with the pragmas it needs to be safe under two processes.

    WAL is the important one: it lets a reader (per-video AI processing, an
    ad-hoc `sqlite3` session) work while `fetch` is writing, instead of blocking
    on a rollback journal. `journal_mode` is a persistent property of the file,
    so it takes effect once and is harmless to reassert. `foreign_keys` is
    per-connection and so must be set every time.
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=config.BUSY_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def connect_readonly() -> sqlite3.Connection:
    """Open the corpus so the engine itself refuses writes.

    `mode=ro` is SQLite's URI flag, so read-only here is not a promise the code
    makes — it is a property of the connection. A bug in a request handler
    cannot mutate `tessera.db`, because the write fails regardless of what the
    handler intended.

    Deliberately does *not* reassert `journal_mode`: WAL is a persistent
    property of the file and is already set, and asking for it on a read-only
    connection is at best a no-op. `BUSY_TIMEOUT` still applies, so a `fetch`
    holding the write lock shows up as a wait rather than a crash.
    """
    conn = sqlite3.connect(
        f"file:{config.DB_PATH}?mode=ro",
        uri=True,
        timeout=config.BUSY_TIMEOUT,
    )
    conn.row_factory = sqlite3.Row
    return conn


def upsert_video(conn: sqlite3.Connection, video: dict) -> bool:
    """Insert a video. Returns True if it was new."""
    cursor = conn.execute(
        """INSERT OR IGNORE INTO videos
           (video_id, channel_id, channel_name, title, description, published_at, url, first_seen_at)
           VALUES (:video_id, :channel_id, :channel_name, :title, :description, :published_at, :url, :seen)""",
        {**video, "seen": _now()},
    )
    return cursor.rowcount > 0


def select_unseen(conn: sqlite3.Connection, videos: list[dict]) -> list[dict]:
    """Path 1's filter: feed entries we have not stored yet.

    Done as one query rather than one-per-video so a channel's whole feed costs
    a single round trip.
    """
    if not videos:
        return []
    ids = [v["video_id"] for v in videos]
    placeholders = ",".join("?" * len(ids))
    seen = {
        row["video_id"]
        for row in conn.execute(f"SELECT video_id FROM videos WHERE video_id IN ({placeholders})", ids)
    }
    return [v for v in videos if v["video_id"] not in seen]


def reserve(conn: sqlite3.Connection, video_id: str) -> None:
    """Claim a video_id for transcription before attempting it."""
    conn.execute(UPSERT_TRANSCRIPT, (video_id, _now()))


def select_retryable(conn: sqlite3.Connection, max_attempts: int | None = None) -> list[sqlite3.Row]:
    """Path 2: rows worth another ordinary attempt.

    Driven off the transcripts table and not off publish date, on purpose. A
    video that came back `blocked` nine days ago would never be selected again
    under a date filter, so it becomes permanently unreachable — recovering it
    would require widening WINDOW_DAYS. Only retryable statuses appear here, so
    a permanently captionless video is not re-hammered on every run.

    `pending` rows are included: a run interrupted between reserve and record
    leaves attempts at 0, and picking them up is exactly the self-healing this
    path is for. cmd_fetch reserves the whole queue before its first request, so
    this is also where a run stopped by a ban leaves everything it did not reach.

    `blocked` rows keep attempts at 0 (see `record`), so they are always selected
    and never fall out of reach. That is intended — a block was never the video's
    fault, and exhausting the counter that way would retire a good video.

    `max_attempts` defaults to config.MAX_ATTEMPTS *at call time*. A default
    argument of `config.MAX_ATTEMPTS` would freeze the value at import, which
    would also let this query and `ChannelStatus.exhausted` — which reads the
    live constant — disagree about which rows are exhausted.
    """
    max_attempts = config.MAX_ATTEMPTS if max_attempts is None else max_attempts
    statuses = ",".join(f"'{s}'" for s in sorted(RETRYABLE_STATUSES | {PENDING}))
    return list(
        conn.execute(
            f"""SELECT v.video_id, v.title, v.url, v.channel_name, t.status, t.attempts
                FROM transcripts t JOIN videos v ON v.video_id = t.video_id
                WHERE t.status IN ({statuses}) AND t.attempts < ?
                ORDER BY v.published_at DESC""",
            (max_attempts,),
        )
    )


def start_fetch_attempt(conn: sqlite3.Connection) -> bool:
    """Atomically spend one slot in the rolling transcript-request budget.

    The row is written immediately before the network call, not after it: a
    crash or interrupt during a request must not make that request disappear
    from the hourly count. `BEGIN IMMEDIATE` serializes competing fetch
    processes so they cannot both spend the final available slot.
    """
    conn.execute("BEGIN IMMEDIATE")
    cutoff = _iso_ago(config.FETCH_HOURLY_WINDOW)
    conn.execute("DELETE FROM fetch_attempts WHERE attempted_at < ?", (cutoff,))
    count = int(conn.execute(
        "SELECT COUNT(*) FROM fetch_attempts WHERE attempted_at >= ?", (cutoff,)
    ).fetchone()[0])
    if count >= config.FETCH_HOURLY_LIMIT:
        conn.commit()
        return False
    conn.execute("INSERT INTO fetch_attempts (attempted_at) VALUES (?)", (_now(),))
    conn.commit()
    return True


def record(conn: sqlite3.Connection, video_id: str, result, fetched_at: str | None = None) -> None:
    """Store an attempt.

    `attempts` counts tries attributable to the video's transcript availability.
    A `blocked` or `rate_limited` response says nothing about that availability,
    so those statuses leave the counter alone; otherwise a ban could drive a
    perfectly good video to MAX_ATTEMPTS and retire it permanently. Every other
    outcome counts as an attempt: a row that only ever comes back `error` still
    reaches MAX_ATTEMPTS eventually and stops being retried.

    The hourly request budget is reserved separately, immediately before the
    network call, by `start_fetch_attempt`. `reserve` only claims a video and
    does not spend request quota.

    A failure never clears a transcript that is already stored — COALESCE keeps
    the old `text` when the result carries none. No selection path currently
    re-fetches an `ok` row, so this cannot lose data today; it is here so that
    adding a re-fetch path cannot silently eat the corpus.
    """
    fetched_at = fetched_at or _now()
    conn.execute(
        """UPDATE transcripts
           SET lang = ?, kind = ?, status = ?,
               text = COALESCE(?, text),
               error = ?, fetched_at = ?,
               attempts = attempts + ?
           WHERE video_id = ?""",
        (
            result.lang,
            result.kind,
            result.status,
            result.text,
            result.error,
            fetched_at,
            0 if result.status in (BLOCKED, RATE_LIMITED) else 1,
            video_id,
        ),
    )
    conn.commit()


def count_recent_attempts(conn: sqlite3.Connection) -> int:
    """Transcript requests made inside the rolling window — the rate-limit budget.

    Rows outside the window are filtered rather than relied upon being pruned,
    so the count stays correct even if no attempt has been recorded recently to
    do the cleanup. Old rows are pruned as a new slot is reserved.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM fetch_attempts WHERE attempted_at >= ?",
        (_iso_ago(config.FETCH_HOURLY_WINDOW),),
    ).fetchone()
    return int(row[0])


def record_summary(
    conn: sqlite3.Connection,
    video_id: str,
    result,
    generated_at: str | None = None,
) -> None:
    """Store (or replace) one summary.

    `attempts` counts real tries, and a transport failure is not one — the same
    reasoning as `record`'s treatment of `blocked`: Ollama not being reachable
    says nothing about the video. The counter therefore moves by the caller's
    delta, 0 for a transport error and 1 otherwise, and an outage cannot drive a
    good transcript to SUMMARY_MAX_ATTEMPTS.

    An `ok` row is never re-selected (see `select_unsummarized`), so this only
    ever fills in a missing or failed row.
    """
    generated_at = generated_at or _now()
    delta = 0 if getattr(result, "transport", False) else 1
    conn.execute(
        """INSERT INTO summaries
             (video_id, status, text, normalized_text, model, attempts, error, generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(video_id) DO UPDATE SET
             status          = excluded.status,
             text            = excluded.text,
             normalized_text = excluded.normalized_text,
             model           = excluded.model,
             attempts        = summaries.attempts + excluded.attempts,
             error           = excluded.error,
             generated_at    = excluded.generated_at""",
        (
            video_id,
            result.status,
            result.text,
            result.normalized_text,
            config.SUMMARY_MODEL,
            delta,
            result.error,
            generated_at,
        ),
    )
    conn.commit()


def select_unsummarized(
    conn: sqlite3.Connection, max_attempts: int | None = None
) -> list[sqlite3.Row]:
    """Transcripts that are `ok` but have no usable summary yet.

    This is both the initial backfill for a corpus that predates V2 and the
    repair path after an Ollama outage, since a failed row is re-selected until
    its attempts reach the cap. `max_attempts` defaults to the live constant at
    call time, for the same reason `select_retryable` does it.
    """
    max_attempts = config.SUMMARY_MAX_ATTEMPTS if max_attempts is None else max_attempts
    return list(
        conn.execute(
            """SELECT v.video_id
               FROM transcripts t JOIN videos v ON v.video_id = t.video_id
               LEFT JOIN summaries s ON s.video_id = t.video_id
               WHERE t.status = 'ok' AND t.text IS NOT NULL AND t.text <> ''
                 AND (s.video_id IS NULL OR (s.status <> 'ok' AND s.attempts < ?))
               ORDER BY v.published_at DESC""",
            (max_attempts,),
        )
    )


# --------------------------------------------------------------------------
# reads for the browser UI
# --------------------------------------------------------------------------

# The list payload. Deliberately excludes `text` and `description`: `text` runs
# to 134,632 characters on a single video and the stored RSS blurbs are 1-4KB
# each, so carrying either would make a list of metadata a list of megabytes.
LIST_COLUMNS = """
  v.video_id, v.channel_id, v.channel_name, v.title, v.published_at, v.url,
  t.lang, t.kind, t.status, t.attempts
"""

# The single-video payload adds the two heavy columns and everything else the
# detail view shows: first_seen_at, the stored reason, and fetched_at. It also
# carries the summary and the normalised transcript — the detail view is the
# only place that needs either, and the UI shows the normalised text as the
# full transcript.
DETAIL_COLUMNS = """
  v.video_id, v.channel_id, v.channel_name, v.title, v.description,
  v.published_at, v.url, v.first_seen_at,
  t.lang, t.kind, t.status, t.text, t.attempts, t.error, t.fetched_at,
  s.status AS summary_status, s.text AS summary,
  s.normalized_text, s.model AS summary_model,
  s.error AS summary_error, s.generated_at AS summary_generated_at
"""

# LEFT, not INNER: an interrupted run can leave a video in `videos` with no
# `transcripts` row, and an inner join would report those videos as missing
# rather than as not-yet-attempted.
LIST_JOIN = "FROM videos v LEFT JOIN transcripts t ON t.video_id = v.video_id"

# The detail query is the only one that needs the summary; the list payload
# carries no summary and no text. LEFT again, so a transcript that has not been
# summarised yet still returns — with NULL summary columns the UI reads as
# "no summary", which is the honest state.
DETAIL_JOIN = f"{LIST_JOIN} LEFT JOIN summaries s ON s.video_id = v.video_id"


def list_videos(
    conn: sqlite3.Connection,
    channel: str | None = None,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """One page of video metadata, plus the `meta` the client would otherwise
    have to hardcode or guess.

    `total` counts the *filtered* set rather than the table. That distinction is
    the whole reason this returns an envelope: the client only ever holds one
    page, so it cannot derive either the count or whether another page exists,
    and a `total` that ignored the filter would make "showing 21-40 of 63" false
    the moment a channel was selected.

    An unknown channel or status is an empty page with `total: 0`, not an
    error. Both are plausible client mistakes, and neither is worth a 500.

    `video_id` breaks the `published_at` tie. Without a unique tiebreaker the
    ordering within a group of equal timestamps is unspecified, so two pages
    could overlap or skip rows across that boundary. The corpus is small enough
    that this has never bitten, but paging correctness should not depend on it
    staying that way.
    """
    clauses: list[str] = []
    params: list[str] = []
    if channel is not None:
        clauses.append("v.channel_id = ?")
        params.append(channel)
    if status is not None:
        clauses.append("t.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    total = conn.execute(f"SELECT COUNT(*) {LIST_JOIN} {where}", params).fetchone()[0]
    rows = [
        dict(row)
        for row in conn.execute(
            f"SELECT {LIST_COLUMNS} {LIST_JOIN} {where} "
            "ORDER BY v.published_at DESC, v.video_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
    ]

    # The channel picker cannot be built from one page: past UI_DEFAULT_LIMIT a
    # channel whose videos all fell off the back would vanish from the sidebar
    # while still existing. One grouped query, published in `meta`.
    channels = [
        {"id": row["channel_id"], "name": row["channel_name"] or row["channel_id"]}
        for row in conn.execute(
            """SELECT channel_id, channel_name FROM videos
               GROUP BY channel_id ORDER BY COALESCE(channel_name, channel_id)"""
        )
    ]

    return {
        "rows": rows,
        "meta": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
            "channels": channels,
            # `ok`, `max_attempts`, `retryable` and `permanent` used to be
            # published here too, and none of them is read. The reasoning had been
            # that a browser hardcoding its own taxonomy could disagree with the
            # retry path about what gets another attempt. But the retry path is
            # `cli.py`, a different process, and it reads `transcript.py`
            # directly — there was no browser in the loop to disagree with. `ok`
            # was published for a different reason and that one was real: a client
            # cannot apply a filter it has not been told the value of, so
            # publishing it cost a request per page load to fetch a constant.
            # The general rule stands: publish what the client cannot derive, and
            # write down what it can.
        },
    }


def get_video(conn: sqlite3.Connection, video_id: str) -> dict | None:
    """One video including `text`, or None if it is not in the corpus.

    `LEFT JOIN`, like status_report: a video ingested by an interrupted run can
    have no transcripts row, and a 404 would claim the video does not exist.
    """
    row = conn.execute(
        f"SELECT {DETAIL_COLUMNS} {DETAIL_JOIN} WHERE v.video_id = ?", (video_id,)
    ).fetchone()
    return dict(row) if row is not None else None


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


@dataclass
class ChannelStatus:
    name: str
    channel_id: str
    videos: int = 0
    ok: int = 0
    failures: dict[str, list[sqlite3.Row]] = field(default_factory=dict)

    @property
    def failed(self) -> int:
        return sum(len(rows) for rows in self.failures.values())

    @property
    def exhausted(self) -> list[sqlite3.Row]:
        """Failed rows past MAX_ATTEMPTS — no longer retried, needs a human."""
        return [
            row
            for rows in self.failures.values()
            for row in rows
            if row["status"] in RETRYABLE_STATUSES and row["attempts"] >= config.MAX_ATTEMPTS
        ]


def status_report(conn: sqlite3.Connection, channels: list[dict]) -> list[ChannelStatus]:
    """Per-channel counts plus the failure breakdown — the real output of V1.

    The breakdown is the measurement of whether this request pattern is holding
    up against YouTube. If `blocked` dominates, the fix is pacing or a proxy —
    see the "Working around IP bans" section of the youtube-transcript-api
    README — not anything available locally.

    The SELECT carries only what `cmd_status` reads: video_id, title,
    published_at, status, attempts and error. `lang`, `kind` and `url` were on it
    for all of V1.1 and read in no branch of the terminal output.
    """
    by_id = {c["id"]: ChannelStatus(name=c["name"], channel_id=c["id"]) for c in channels}
    reports = list(by_id.values())

    rows = conn.execute(
        """SELECT v.channel_id, v.channel_name, v.video_id, v.title, v.published_at,
                  t.status, t.attempts, t.error
           FROM videos v LEFT JOIN transcripts t ON t.video_id = v.video_id
           ORDER BY v.published_at DESC"""
    )

    for row in rows:
        # A channel dropped from channels.json must not vanish from the report.
        report = by_id.get(row["channel_id"])
        if report is None:
            report = ChannelStatus(name=row["channel_name"] or row["channel_id"], channel_id=row["channel_id"])
            by_id[row["channel_id"]] = report
            reports.append(report)

        report.videos += 1
        if row["status"] == OK:
            report.ok += 1
        elif row["status"] is not None:
            report.failures.setdefault(row["status"], []).append(row)

    return reports
