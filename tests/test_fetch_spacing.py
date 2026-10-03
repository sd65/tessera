"""Checks the pacing policy between transcript fetches: jittered interval,
hourly ceiling, and a hard stop on HTTP 429.

Plain script, no pytest. The feed and transcript fetches are stubbed, Ollama is
made fast, slow or failing, and a fake monotonic clock records sleeps and request
starts. No test waits in real time or touches YouTube/Ollama.

    uv run python tests/test_fetch_spacing.py
"""

from __future__ import annotations

import contextlib
import io
import sys

from support import Suite, TempCorpus, patch_post, seed
from tessera import cli, config, store, summarize, transcript
from youtube_transcript_api import IpBlocked, RequestBlocked

suite = Suite()
case = suite.case

#: A fixed interval, so the jitter does not make assertions non-deterministic.
FIXED_INTERVAL = 22.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@contextlib.contextmanager
def interval(minimum: float = FIXED_INTERVAL, maximum: float = FIXED_INTERVAL):
    """Pin the jittered interval for the duration of a case."""
    saved = config.FETCH_INTERVAL_MIN, config.FETCH_INTERVAL_MAX
    config.FETCH_INTERVAL_MIN, config.FETCH_INTERVAL_MAX = minimum, maximum
    try:
        yield
    finally:
        config.FETCH_INTERVAL_MIN, config.FETCH_INTERVAL_MAX = saved


@contextlib.contextmanager
def fake_fetch(fetch_seconds: float = 1.0, statuses: tuple[str, ...] = (transcript.OK,)):
    """Stub transcript fetching, use a fake clock, and script the outcomes.

    ``statuses`` is applied by attempt index and its last value repeats, so a
    case can make one video succeed and the next come back rate-limited.
    """
    clock = FakeClock()
    starts: list[float] = []
    real_fetch = transcript.fetch_transcript
    real_monotonic = cli.time.monotonic
    real_sleep = cli.time.sleep

    def fake_transcript(video_id):
        starts.append(clock.now)
        clock.now += fetch_seconds
        status = statuses[min(len(starts) - 1, len(statuses) - 1)]
        return transcript.TranscriptResult(
            status, lang="en", kind="auto", text="Elmeno arrived."
        )

    transcript.fetch_transcript = fake_transcript
    cli.time.monotonic = clock.monotonic
    cli.time.sleep = clock.sleep
    try:
        yield clock, starts
    finally:
        transcript.fetch_transcript = real_fetch
        cli.time.monotonic = real_monotonic
        cli.time.sleep = real_sleep


def run_fetch() -> int:
    """Run ``cmd_fetch`` with the feed stubbed out and its printing suppressed."""
    real_channels = config.load_channels
    config.load_channels = lambda: []
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return cli.cmd_fetch()
    finally:
        config.load_channels = real_channels


def pending(conn, count: int = 3) -> None:
    for n in range(count):
        seed(conn, video_id=f"vid{n:08d}", status=store.PENDING)


@case
def ip_blocked_is_distinguished_from_a_bot_check():
    assert transcript.classify(IpBlocked("vid00000000")) == transcript.RATE_LIMITED
    assert transcript.classify(RequestBlocked("vid00000000")) == transcript.BLOCKED


@case
def a_failed_summary_still_paces_the_next_request():
    """A fast Ollama failure must not shrink the interval to the fetch time."""
    with TempCorpus() as conn, interval():
        pending(conn)

        def boom(body):
            raise summarize.TransportError("Connection refused")

        saved = patch_post(boom)
        try:
            with fake_fetch(fetch_seconds=1.0) as (clock, starts):
                assert run_fetch() == 0
        finally:
            summarize._post = saved

        # Each fetch takes 1s, so 21s of the 22s interval is left to sleep.
        assert starts == [0.0, 22.0, 44.0], starts
        assert clock.sleeps == [21.0, 21.0], clock.sleeps


@case
def a_slow_summary_counts_toward_the_interval():
    """Time already spent summarising is spent: no extra wait on top."""
    with TempCorpus() as conn, interval():
        pending(conn)

        def slow_model(body):
            clock.now += 70.0
            return {"response": "ok", "eval_count": 1}

        saved = patch_post(slow_model)
        try:
            with fake_fetch(fetch_seconds=1.0) as (clock, starts):
                run_fetch()
        finally:
            summarize._post = saved

        # 1s fetch + 70s summary is already past the 22s interval every time.
        assert starts == [0.0, 71.0, 142.0], starts
        assert clock.sleeps == [], clock.sleeps


@case
def the_hourly_ceiling_stops_the_fetch_loop():
    """Counted across attempts, not just within one process."""
    with TempCorpus() as conn, interval():
        pending(conn, count=5)
        saved_limit = config.FETCH_HOURLY_LIMIT
        config.FETCH_HOURLY_LIMIT = 2
        saved_post = patch_post(lambda body: {"response": "done", "eval_count": 1})
        try:
            with fake_fetch() as (clock, starts):
                assert run_fetch() == 0
        finally:
            config.FETCH_HOURLY_LIMIT = saved_limit
            summarize._post = saved_post

        # The third video is never requested: the budget was already spent.
        assert len(starts) == 2, starts
        assert store.count_recent_attempts(conn) == 2
        # The three unreached videos stay pending, which path 2 picks up later.
        untried = {
            row["video_id"]
            for row in store.select_retryable(conn)
            if row["status"] == store.PENDING
        }
        assert len(untried) == 3, untried


@case
def a_429_stops_the_run_and_skips_the_backlog():
    """YouTube counting our requests means stop everything, including local work."""
    with TempCorpus() as conn, interval():
        pending(conn, count=2)
        # A transcript already stored with no summary: it would be picked up by
        # the summary-only backlog if the run were allowed to reach it.
        already = seed(conn, video_id="vid00000099")
        attempts_before = store.count_recent_attempts(conn)

        saved = patch_post(lambda body: {"response": "done", "eval_count": 1})
        try:
            with fake_fetch(statuses=(transcript.OK, transcript.RATE_LIMITED)) as (clock, starts):
                assert run_fetch() == 1, "a 429 must end the run with a failure"
        finally:
            summarize._post = saved

        # First video fetched and summarised, second refused — then nothing.
        assert len(starts) == 2, starts
        assert store.count_recent_attempts(conn) - attempts_before == 2

        row = conn.execute(
            "SELECT status FROM transcripts WHERE video_id = ?", (already,)
        ).fetchone()
        assert row["status"] == transcript.OK, row["status"]
        assert conn.execute(
            "SELECT COUNT(*) FROM summaries WHERE video_id = ?", (already,)
        ).fetchone()[0] == 0, "the backlog never ran"

        # A 429 is not the video's fault: no attempt burned, still retryable.
        refused = conn.execute(
            "SELECT video_id, attempts FROM transcripts WHERE status = ?",
            (transcript.RATE_LIMITED,),
        ).fetchall()
        assert len(refused) == 1, [dict(r) for r in refused]
        assert refused[0]["attempts"] == 0, refused[0]["attempts"]
        assert refused[0]["video_id"] in {row["video_id"] for row in store.select_retryable(conn)}


if __name__ == "__main__":
    sys.exit(suite.run())
