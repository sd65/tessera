"""argparse, dispatch, printing. No logic beyond wiring."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import webbrowser

from . import config, rss, store, summarize, transcript, web

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
    """The trailing note on a summary line: size, or the stored reason."""
    if result.status == summarize.OK:
        return f"{len(result.text or '')} chars"
    return _one_line(result.error)


def _summarize(conn: sqlite3.Connection, video_id: str):
    """Normalise and summarise one stored transcript.

    Returns the SummaryResult, or None if the transcript has no text. The model
    call is never allowed to end a run: `summarize.summarize` returns a failed
    result rather than raising, and it is stored like any other outcome.
    """
    entry = store.get_video(conn, video_id)
    if not entry or not entry.get("text"):
        return None
    result = summarize.summarize(entry)
    store.record_summary(conn, video_id, result)
    return result


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

    if not fetch_work and not summary_work:
        print("\nnothing to do — corpus is up to date")
        conn.close()
        return 0

    tally: dict[str, int] = {}
    summary_tally: dict[str, int] = {}
    summary_failed = False

    if fetch_work:
        print(f"\nfetching {len(fetch_work)} transcript(s) at {config.POLITE_DELAY}s apart")

    for i, (video_id, why) in enumerate(fetch_work):
        if i:
            # The model call is the main spacing between two YouTube requests —
            # tens of seconds per video on top of POLITE_DELAY. When it failed
            # (Ollama down) it returned in milliseconds, so fall back to a fixed,
            # longer pause instead of hitting YouTube at POLITE_DELAY pace.
            if summary_failed:
                print(f"  summary failed — pausing {config.SUMMARY_FAILURE_DELAY:.0f}s "
                      f"before the next transcript")
                time.sleep(config.SUMMARY_FAILURE_DELAY)
            else:
                time.sleep(config.POLITE_DELAY)
        summary_failed = False

        result = transcript.fetch_transcript(video_id)
        store.record(conn, video_id, result)
        tally[result.status] = tally.get(result.status, 0) + 1

        note = f"{len(result.text or '')} chars" if result.status == transcript.OK else _one_line(result.error)
        print(f"  [{i + 1}/{len(fetch_work)}] {video_id}  {why:<14} {result.status:<12} {note}")

        # Summarise inline, video by video: the model call is slow, and the time
        # it spends is also extra spacing between two YouTube requests.
        if result.status == transcript.OK:
            summary = _summarize(conn, video_id)
            if summary is not None:
                summary_tally[summary.status] = summary_tally.get(summary.status, 0) + 1
                print(f"            summary  {summary.status:<8} {_summary_note(summary)}")
                # Any non-ok summary means the expected spacing is gone, so the
                # next fetch gets the fallback delay.
                summary_failed = summary.status != summarize.OK

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

    # The summary backlog has nothing to fetch, so it still runs after a block.
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

    # Opened after the socket is bound, so the browser never loads a dead port.
    # The one exception to "nothing leaves the machine": the page itself is local
    # and the nocookie embed is what YouTube is for.
    webbrowser.open(url)
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
    sub.add_parser("status", help="per-channel counts and the failure breakdown")
    sub.add_parser("ui", help="browse the corpus in a browser, read-only")

    args = parser.parse_args(argv)

    try:
        if args.command == "fetch":
            return cmd_fetch()
        if args.command == "status":
            return cmd_status()
        if args.command == "ui":
            return cmd_ui()
    except (config.ConfigError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())