"""argparse, dispatch, printing. No logic beyond wiring."""

from __future__ import annotations

import argparse
import random
import sqlite3
import sys
import time
import webbrowser

from . import config, rss, store, summarize, title_case, transcript, web

# Draw the eye to rows that have stopped retrying.
_EXHAUSTED = "!!"

# How much of a stored reason to show. Kept short by transcript.describe().
_ERROR_WIDTH = 70


def _one_line(text: str | None, width: int = _ERROR_WIDTH) -> str:
    """Collapse a stored reason to a single truncated line."""
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _fmt_published(value: str | None) -> str:
    return (value or "")[:10]


def _summary_note(result) -> str:
    """The trailing note on a summary line: result, time, and generation rate."""
    if result.status == summarize.OK:
        note = f"{len(result.text or '')} chars"
    else:
        note = _one_line(result.error)

    metrics = []
    if result.processing_seconds is not None:
        metrics.append(f"{result.processing_seconds:.1f}s")
    if result.tokens_per_second is not None:
        metrics.append(f"{result.tokens_per_second:.1f} tok/s")
    return " · ".join([note, *metrics])


def _summarize(conn: sqlite3.Connection, video_id: str, *, force: bool = False):
    """Normalise and summarise one stored transcript.

    Returns the SummaryResult, or None if the transcript has no text. The model
    call is never allowed to end a run: `summarize.summarize` returns a failed
    result rather than raising, and it is stored like any other outcome. Invalid
    model outputs are retried immediately, but never beyond the persisted total
    attempt cap. A targeted forced run gets one fresh retry budget without erasing
    the historical attempt count.
    """
    entry = store.get_video(conn, video_id)
    if not entry or not entry.get("text"):
        return None
    entry["_cues"] = store.transcript_cues(conn, video_id)
    row = conn.execute(
        "SELECT attempts FROM summaries WHERE video_id = ?", (video_id,)
    ).fetchone()
    previous_attempts = int(row["attempts"]) if row else 0
    remaining_attempts = (
        config.SUMMARY_MAX_ATTEMPTS
        if force
        else config.SUMMARY_MAX_ATTEMPTS - previous_attempts
    )
    if remaining_attempts <= 0:
        return None
    attempt_ceiling = previous_attempts + remaining_attempts

    result = None
    for attempt in range(remaining_attempts):
        seed = config.SUMMARY_SEED + previous_attempts + attempt
        result = summarize.summarize(entry, seed=seed)
        store.record_summary(conn, video_id, result)

        # Transport errors do not count against the cap; retrying immediately
        # cannot help while Ollama is unreachable. An empty normalized transcript
        # is also a pre-model failure, not a malformed model answer.
        if result.status == summarize.OK or result.transport or not result.normalized_text:
            break
        if attempt + 1 < remaining_attempts:
            completed = previous_attempts + attempt + 1
            print(
                f"            summary output invalid; retrying "
                f"({completed + 1}/{attempt_ceiling})"
            )
    return result


def _title_case_rows(
    conn: sqlite3.Connection,
    rows,
    replace_existing: bool = False,
) -> dict[str, int]:
    """Generate and cache display titles; a model failure never blocks fetch."""
    counts = {"changed": 0, "unchanged": 0, "failed": 0}
    for row in rows:
        video_id, source_title = row["video_id"], row["title"]
        result = title_case.case_title(source_title)
        if result.display_title is None:
            counts["failed"] += 1
            print(f"  ! {video_id}  {_one_line(result.error)}", file=sys.stderr)
            continue
        if not store.record_display_title(
            conn, video_id, source_title, result.display_title,
            replace_existing=replace_existing,
        ):
            counts["failed"] += 1
            print(f"  ! {video_id}  source title changed; result not cached", file=sys.stderr)
            continue
        conn.commit()
        if result.display_title == source_title:
            counts["unchanged"] += 1
            print(f"  = {video_id}  {source_title}")
        else:
            counts["changed"] += 1
            print(f"  ~ {video_id}  {source_title}  ->  {result.display_title}")
    return counts


def cmd_fetch() -> int:
    channels = config.load_channels()
    conn = store.connect()
    store.init(conn)

    # Path 1 — ingest. One pass per channel, inserting only what we lack.
    new_videos: list[dict] = []
    for channel in channels:
        try:
            feed = rss.fetch_recent(channel["id"])
        except rss.FeedError as exc:
            print(f"  ! {channel['name']}: {exc}", file=sys.stderr)
            continue
        unseen = store.select_unseen(conn, feed)
        for video in unseen:
            if store.upsert_video(conn, video):
                new_videos.append(video)
        print(f"  {channel['name']}: {len(feed)} in window, {len(unseen)} new")

    # Path 2 — retry, regardless of publish date. See store.select_retryable.
    retries = store.select_retryable(conn)

    # Path 3 — summary-only. Already-`ok` transcripts with no usable summary:
    # the backfill for a corpus that predates V2, and the repair path for a
    # summary an earlier run could not produce because Ollama was down. These
    # need no transcript fetch, so they are kept apart from the fetch queue.
    unsummarized = [row["video_id"] for row in store.select_unsummarized(conn)]

    # Paths 1 and 2 normally select disjoint sets — path 1 takes videos with no
    # row at all, path 2 takes rows that already exist — but dedupe on video_id
    # anyway so a video is never fetched twice in one run.
    fetch_work: list[tuple[str, str]] = []
    queued: set[str] = set()
    for video in new_videos:
        if video["video_id"] not in queued:
            queued.add(video["video_id"])
            fetch_work.append((video["video_id"], "new"))
    for row in retries:
        if row["video_id"] not in queued:
            queued.add(row["video_id"])
            fetch_work.append((row["video_id"], f"retry (x{row['attempts']})"))

    # A transcript fetched this run is summarised inline, so keep it out of the
    # summary-only queue.
    summary_work = [video_id for video_id in unsummarized if video_id not in queued]

    # Claim every video up front, before the first request, and commit the
    # ingest together with the claims.
    #
    # Both halves matter. Claiming lazily would, if the run stopped early, leave
    # a video in `videos` with no `transcripts` row — and `select_unseen` filters
    # on `videos` while `select_retryable` needs the row, so neither query would
    # ever return it again. Committing the ingest separately would open a window
    # between the two commits where an interrupt leaves exactly those orphans.
    # One commit, no window.
    #
    # Skipping the rest is safe: unreached rows sit at PENDING with attempts=0,
    # which is what `select_retryable` picks up next run. reserve() is
    # ON CONFLICT DO NOTHING, so re-claiming a retryable row is harmless.
    for video_id, _ in fetch_work:
        store.reserve(conn, video_id)
    conn.commit()

    # Title casing is best-effort local work. It is attempted for newly ingested
    # rows, but a missing or misbehaving Ollama never prevents transcript fetch.
    if new_videos:
        print(f"\ncasing {len(new_videos)} new video title(s)")
        title_counts = _title_case_rows(conn, new_videos)
        print("  " + "  ".join(f"{key} {value}" for key, value in title_counts.items()))

    if not fetch_work and not summary_work:
        print("\nnothing to do — corpus is up to date")
        conn.close()
        return 0

    tally: dict[str, int] = {}
    summary_tally: dict[str, int] = {}
    last_fetch_started: float | None = None
    # True when the run must end outright, as opposed to a break that still lets
    # the local summary backlog run.
    stop_run = False

    if fetch_work:
        print(f"\nfetching {len(fetch_work)} transcript(s), "
              f"{config.FETCH_INTERVAL_MIN:.0f}-{config.FETCH_INTERVAL_MAX:.0f}s "
              f"apart, max {config.FETCH_HOURLY_LIMIT} requests per hour")

    for i, (video_id, why) in enumerate(fetch_work):
        # The ceiling comes first: there is no point sleeping out a paced
        # interval we are not allowed to spend. Counted across runs, so
        # restarting cannot reset the budget.
        recent = store.count_recent_attempts(conn)
        if recent >= config.FETCH_HOURLY_LIMIT:
            print(f"\n  {recent}/{config.FETCH_HOURLY_LIMIT} transcript requests in the "
                  f"last {config.FETCH_HOURLY_WINDOW / 60:.0f} minutes — stopping. "
                  f"{len(fetch_work) - i} left for a later run.")
            break

        if last_fetch_started is not None:
            # Jittered interval between request *starts*. Fetch and summarise
            # time both count toward it, so only the remainder is slept — and
            # that holds whether the inline summary succeeded, failed, or
            # Ollama was never reachable.
            interval = random.uniform(config.FETCH_INTERVAL_MIN, config.FETCH_INTERVAL_MAX)
            remaining = interval - (time.monotonic() - last_fetch_started)
            if remaining > 0:
                print(f"  pacing — pausing {remaining:.1f}s before next transcript")
                time.sleep(remaining)

        # Reserve the hourly slot immediately before the network request. The
        # atomic check closes the race with another fetch process (and still
        # counts an interrupted request against the budget).
        if not store.start_fetch_attempt(conn):
            recent = store.count_recent_attempts(conn)
            print(f"\n  {recent}/{config.FETCH_HOURLY_LIMIT} transcript requests in the "
                  f"last {config.FETCH_HOURLY_WINDOW / 60:.0f} minutes — stopping. "
                  f"{len(fetch_work) - i} left for a later run.")
            break

        last_fetch_started = time.monotonic()

        result = transcript.fetch_transcript(video_id)
        store.record(conn, video_id, result)
        tally[result.status] = tally.get(result.status, 0) + 1

        note = f"{len(result.text or '')} chars" if result.status == transcript.OK else _one_line(result.error)
        print(f"  [{i + 1}/{len(fetch_work)}] {video_id}  {why:<14} {result.status:<12} {note}")

        # Summarise inline, video by video: the model call is slow, and the time
        # it spends also counts toward the next interval.
        if result.status == transcript.OK:
            summary = _summarize(conn, video_id)
            if summary is not None:
                summary_tally[summary.status] = summary_tally.get(summary.status, 0) + 1
                print(f"            summary  {summary.status:<8} {_summary_note(summary)}")

        # A 429 is YouTube counting our requests for us. End the whole run, not
        # just the loop: everything after this point is optional, and the next
        # request would only spend quota to learn the same thing again.
        if result.status == transcript.RATE_LIMITED:
            print("\n  HTTP 429 — YouTube is rate limiting this IP. Stopping the run.")
            stop_run = True
            break

        # A block is about us, not this video: every further request will block
        # too. Stop rather than spend the rest of the queue proving it, and leave
        # the rest as PENDING for next time.
        if result.status == transcript.BLOCKED:
            remaining = len(fetch_work) - i - 1
            if remaining:
                print(f"\n  stopped after {i + 1} — YouTube is blocking us. "
                      f"{remaining} left for the next run.")
            break

    if fetch_work:
        print("  ".join(f"{k} {v}" for k, v in sorted(tally.items())))

    # On a 429 the instruction is to stop, backlog included: the local work is
    # harmless, but a rate-limited IP should stop being a network client for a
    # while rather than spend that time doing something else.
    if stop_run:
        conn.close()
        return 1

    # The summary backlog has nothing to fetch, so it still runs after a block
    # or after the hourly ceiling.
    if summary_work:
        print(f"\nsummarizing {len(summary_work)} stored transcript(s)")
        for i, video_id in enumerate(summary_work, start=1):
            summary = _summarize(conn, video_id)
            if summary is None:
                continue
            summary_tally[summary.status] = summary_tally.get(summary.status, 0) + 1
            print(f"  [{i}/{len(summary_work)}] {video_id}  summary  "
                  f"{summary.status:<8} {_summary_note(summary)}")

    if summary_tally:
        print("  ".join(f"summary {k} {v}" for k, v in sorted(summary_tally.items())))

    conn.close()
    return 0


def cmd_title_case(force: bool = False) -> int:
    """Backfill AI-cased display titles for all rows still pending."""
    conn = store.connect()
    try:
        store.init(conn)
        rows = store.select_titles_pending_case(conn, include_cached=force)
        if not rows:
            print("no video titles pending casing")
            return 0
        action = "refreshing" if force else "casing"
        print(f"{action} {len(rows)} stored video title(s)")
        counts = _title_case_rows(conn, rows, replace_existing=force)
        print("  " + "  ".join(f"{key} {value}" for key, value in counts.items()))
        return 1 if counts["failed"] else 0
    finally:
        conn.close()


def cmd_status() -> int:
    channels = config.load_channels()
    conn = store.connect()
    store.init(conn)

    reports = store.status_report(conn, channels)
    if not any(r.videos for r in reports):
        print(f"no videos stored yet in {config.DB_PATH} — run `tessera fetch`")
        conn.close()
        return 0

    grand_videos = grand_ok = grand_failed = 0

    print(f"\n{config.DB_PATH.name}  window {config.WINDOW_DAYS}d  "
          f"retries stop at {config.MAX_ATTEMPTS} attempts\n")

    for report in reports:
        if not report.videos:
            continue
        grand_videos += report.videos
        grand_ok += report.ok
        grand_failed += report.failed

        exhausted = {row["video_id"] for row in report.exhausted}
        pct = (100 * report.ok / report.videos) if report.videos else 0
        print(f"{report.name}  ({report.channel_id})")
        print(f"  {report.videos} videos   {report.ok} ok ({pct:.0f}%)   {report.failed} failed")

        for status, rows in sorted(report.failures.items()):
            # Anything not on the permanent list is re-attempted each run —
            # including a `pending` placeholder left by an interrupted run.
            retry = "permanent" if status in transcript.PERMANENT_STATUSES else "retried each run"
            print(f"\n  {status} — {len(rows)} ({retry})")
            for row in rows:
                mark = f" {_EXHAUSTED}" if row["video_id"] in exhausted else ""
                reason = _one_line(row["error"], 64)
                print(f"    {row['video_id']}  {_fmt_published(row['published_at'])}  "
                      f"x{row['attempts']}  {reason}{mark}")

        if exhausted:
            print(f"\n  {_EXHAUSTED} past {config.MAX_ATTEMPTS} attempts, no longer retried:")
            for row in report.exhausted:
                print(f"    {row['video_id']}  {row['title']}")
        print()

    print(f"total  {grand_videos} videos   {grand_ok} ok   {grand_failed} failed")
    conn.close()
    return 0


def _doctor_issues(conn: sqlite3.Connection) -> tuple[list, list, list]:
    """Return recoverable transcript failures, permanent failures, and summaries."""
    return (
        store.select_retryable(conn, include_exhausted=True),
        store.select_permanent_failures(conn),
        store.select_summary_errors(conn),
    )


def _print_doctor_report(issues: tuple[list, list, list]) -> None:
    transcript_errors, permanent_errors, summary_errors = issues
    exhausted = [
        row for row in transcript_errors
        if row["status"] in transcript.RETRYABLE_STATUSES
        and row["attempts"] >= config.MAX_ATTEMPTS
    ]
    print("Tessera doctor")
    print(
        f"  recoverable transcript errors: {len(transcript_errors)}"
        f" ({len(exhausted)} exhausted)"
    )
    print(f"  permanent transcript errors:   {len(permanent_errors)} (not retried)")
    print(f"  failed summaries:              {len(summary_errors)}")
    for row in transcript_errors:
        mark = " — attempts exhausted" if row in exhausted else ""
        print(
            f"    transcript {row['video_id']}  {row['status']}"
            f"  x{row['attempts']}{mark}: {_one_line(row['error'])}"
        )
    for row in permanent_errors:
        print(
            f"    permanent   {row['video_id']}  {row['status']}"
            f": {_one_line(row['error'])}"
        )
    for row in summary_errors:
        print(
            f"    summary     {row['video_id']}  x{row['attempts']}"
            f": {_one_line(row['error'])}"
        )


def _retry_doctor_transcripts(
    conn: sqlite3.Connection, rows: list[sqlite3.Row]
) -> tuple[dict[str, int], bool]:
    """Retry recoverable transcript rows under fetch's pacing and quota rules.

    Returns (status counts, hit_rate_limit). A rate limit stops the entire doctor
    retry; a video-level block stops transcript requests but permits local summary
    retries, matching `fetch` behavior.
    """
    tally: dict[str, int] = {}
    last_fetch_started: float | None = None
    for index, row in enumerate(rows):
        recent = store.count_recent_attempts(conn)
        if recent >= config.FETCH_HOURLY_LIMIT:
            print(
                f"  transcript retries paused: {recent}/{config.FETCH_HOURLY_LIMIT} "
                "requests in the last hour"
            )
            break

        if last_fetch_started is not None:
            interval = random.uniform(
                config.FETCH_INTERVAL_MIN, config.FETCH_INTERVAL_MAX
            )
            remaining = interval - (time.monotonic() - last_fetch_started)
            if remaining > 0:
                time.sleep(remaining)

        if not store.start_fetch_attempt(conn):
            print("  transcript retries paused: hourly request limit reached")
            break

        last_fetch_started = time.monotonic()
        result = transcript.fetch_transcript(row["video_id"])
        store.record(conn, row["video_id"], result)
        tally[result.status] = tally.get(result.status, 0) + 1
        note = (
            f"{len(result.text or '')} chars"
            if result.status == transcript.OK
            else _one_line(result.error)
        )
        print(
            f"  [{index + 1}/{len(rows)}] {row['video_id']}  transcript "
            f"{result.status:<12} {note}"
        )
        if result.status == transcript.RATE_LIMITED:
            print("  HTTP 429 — stopping doctor retries")
            return tally, True
        if result.status == transcript.BLOCKED:
            remaining = len(rows) - index - 1
            if remaining:
                print(f"  YouTube block — {remaining} transcript retry(s) skipped")
            break
    return tally, False


def cmd_doctor(retry: bool = False) -> int:
    """Diagnose the corpus; optionally retry recoverable transcript/summary errors."""
    if not retry:
        conn = store.connect_readonly()
        try:
            issues = _doctor_issues(conn)
            _print_doctor_report(issues)
            if any(issues):
                print("Run `tessera doctor --retry` to retry recoverable errors.")
            else:
                print("No errors found.")
            return 0
        finally:
            conn.close()

    conn = store.connect()
    try:
        store.init(conn)
        initial = _doctor_issues(conn)
        _print_doctor_report(initial)
        transcript_errors, _, _ = initial
        if not any(initial):
            print("No errors to retry.")
            return 0

        if transcript_errors:
            print(
                f"\nRetrying {len(transcript_errors)} recoverable transcript(s) "
                "with normal pacing and request limits"
            )
            _, rate_limited = _retry_doctor_transcripts(conn, transcript_errors)
            if rate_limited:
                return 1

        # Re-select after transcript retries: successful re-fetches may now have
        # a usable transcript, and summary retries must never run on stale text.
        summary_errors = store.select_summary_errors(conn)
        pending_summaries = store.select_unsummarized(conn)
        summary_error_ids = {row["video_id"] for row in summary_errors}
        summary_ids = list(dict.fromkeys(
            [row["video_id"] for row in summary_errors]
            + [row["video_id"] for row in pending_summaries]
        ))
        if summary_ids:
            print(
                f"\nProcessing {len(summary_ids)} summary task(s) "
                "from stored transcripts"
            )
            for index, video_id in enumerate(summary_ids, start=1):
                result = _summarize(
                    conn, video_id, force=video_id in summary_error_ids
                )
                if result is not None:
                    print(
                        f"  [{index}/{len(summary_ids)}] {video_id}  "
                        f"summary {result.status:<8} {_summary_note(result)}"
                    )

        remaining = _doctor_issues(conn)
        transcript_left, permanent_left, summaries_left = remaining
        print(
            f"\nDoctor complete: {len(transcript_left)} recoverable transcript error(s), "
            f"{len(summaries_left)} failed summary/summaries remain; "
            f"{len(permanent_left)} permanent error(s) were not retried."
        )
        return 1 if transcript_left or summaries_left else 0
    finally:
        conn.close()


def cmd_ui() -> int:
    url = f"http://{config.UI_HOST}:{config.UI_PORT}"

    # Make sure the schema exists before the read-only server opens the file: an
    # older tessera.db predates the summaries table, and a `mode=ro` connection
    # cannot create it. Idempotent, and the only write this command makes.
    conn = store.connect()
    try:
        store.init(conn)
    finally:
        conn.close()

    try:
        server = web.build_server()
    except OSError as exc:
        print(f"error: could not listen on {url} — {exc}", file=sys.stderr)
        print(f"       {config.UI_PORT} is set in tessera/config.py", file=sys.stderr)
        return 1

    print(f"\n  read-only view of {config.DB_PATH.name}\n  {url}  —  ctrl-c to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tessera",
        description="Local corpus of YouTube videos and transcripts. Nothing leaves the machine "
                    "except the video playback in `tessera ui`.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="ingest new videos and retry failed transcripts")
    title_parser = sub.add_parser("title-case", help="backfill AI-cased display titles")
    title_parser.add_argument(
        "--force", action="store_true", help="regenerate all cached display titles"
    )
    sub.add_parser("status", help="per-channel counts and the failure breakdown")
    doctor_parser = sub.add_parser(
        "doctor", help="diagnose errors; optionally retry recoverable failures"
    )
    doctor_parser.add_argument(
        "--retry",
        action="store_true",
        help="retry recoverable transcript and summary errors",
    )
    sub.add_parser("ui", help="browse the corpus in a browser, read-only")

    args = parser.parse_args(argv)

    try:
        if args.command == "fetch":
            return cmd_fetch()
        if args.command == "title-case":
            return cmd_title_case(force=args.force)
        if args.command == "status":
            return cmd_status()
        if args.command == "doctor":
            return cmd_doctor(retry=args.retry)
        if args.command == "ui":
            return cmd_ui()
    except (config.ConfigError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
