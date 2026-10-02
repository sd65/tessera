"""SQLite schema, upserts, the two selection queries, and everything `status`
prints.

The status/attempts split is not documentation — see the WHERE clause in
`select_retryable`, which is the whole reason the permanent/retryable distinction
in transcript.py exists.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config
from .transcript import BLOCKED, OK, RETRYABLE_STATUSES

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
  chars       INTEGER,
  attempts    INTEGER DEFAULT 0,
  error       TEXT,
  fetched_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_published   ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_channel     ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_status ON transcripts(status, attempts);
"""

# Marks a video as ingested before any transcript attempt, so a crash mid-run
# cannot cause a re-ingest on the next pass.
PENDING = "pending"

UPSERT_TRANSCRIPT = f"""
INSERT INTO transcripts (video_id, lang, kind, status, text, chars, attempts, error, fetched_at)
VALUES (?, NULL, NULL, '{PENDING}', NULL, NULL, 0, NULL, ?)
ON CONFLICT(video_id) DO NOTHING
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


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


def select_retryable(conn: sqlite3.Connection, max_attempts: int = config.MAX_ATTEMPTS) -> list[sqlite3.Row]:
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
    """
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


def record(conn: sqlite3.Connection, video_id: str, result, fetched_at: str | None = None) -> None:
    """Store an attempt.

    `attempts` counts real tries, and a block is not one. A `blocked` row means
    WeTube refused to answer, so the video was never actually asked — counting it
    lets an IP ban drive a perfectly good video to MAX_ATTEMPTS and retire it
    permanently. So blocked leaves the counter alone (and leaves any transcript
    text untouched), while every other outcome counts as an attempt: a row that
    only ever comes back `error` still reaches MAX_ATTEMPTS eventually and stops
    being retried.
    """
    fetched_at = fetched_at or _now()
    conn.execute(
        """UPDATE transcripts
           SET lang = ?, kind = ?, status = ?, text = ?, chars = ?, error = ?, fetched_at = ?,
               attempts = attempts + ?
           WHERE video_id = ?""",
        (
            result.lang,
            result.kind,
            result.status,
            result.text,
            len(result.text) if result.text else 0,
            result.error,
            fetched_at,
            0 if result.status == BLOCKED else 1,
            video_id,
        ),
    )
    conn.commit()


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
    """
    by_id = {c["id"]: ChannelStatus(name=c["name"], channel_id=c["id"]) for c in channels}
    reports = list(by_id.values())

    rows = conn.execute(
        """SELECT v.channel_id, v.channel_name, v.video_id, v.title, v.url, v.published_at,
                  t.status, t.attempts, t.error, t.lang, t.kind, t.chars
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