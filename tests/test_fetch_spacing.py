"""Checks the minimum start-to-start spacing between transcript fetches.

Plain script, no pytest. The feed and transcript fetches are stubbed, Ollama is
made fast or slow, and a fake monotonic clock records sleeps and request starts.
No test waits in real time or touches YouTube/Ollama.

    uv run python tests/test_fetch_spacing.py
"""

from __future__ import annotations

import contextlib
import io
import sys

from support import Suite, TempCorpus, patch_post, seed
from tessera import cli, config, store, summarize, transcript

suite = Suite()
case = suite.case


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
def fake_fetch(fetch_seconds: float = 1.0):
    """Stub transcript fetching and use a fake clock for pacing."""
    clock = FakeClock()
    starts: list[float] = []
    real_fetch = transcript.fetch_transcript
    real_monotonic = cli.time.monotonic
    real_sleep = cli.time.sleep

    def fake_transcript(video_id):
        starts.append(clock.now)
        clock.now += fetch_seconds
        return transcript.TranscriptResult(
            transcript.OK, lang="en", kind="auto", text="Elmeno arrived."
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


def three_pending(conn) -> None:
    for n in range(3):
        seed(conn, video_id=f"vid{n:08d}", status=store.PENDING)


@case
def a_fast_failed_summary_still_respects_the_minimum_interval():
    with TempCorpus() as conn:
        three_pending(conn)

        def boom(body):
            raise summarize.TransportError("Connection refused")

        saved = patch_post(boom)
        try:
            with fake_fetch(fetch_seconds=1.0) as (clock, starts):
                assert run_fetch() == 0
        finally:
            summarize._post = saved

        assert starts == [0.0, 65.0, 130.0], starts
        assert clock.sleeps == [64.0, 64.0], clock.sleeps


@case
def a_fast_successful_summary_still_respects_the_minimum_interval():
    with TempCorpus() as conn:
        three_pending(conn)

        def fast_model(body):
            clock.now += 2.0
            return {"response": "ok", "eval_count": 1}

        saved = patch_post(fast_model)
        try:
            with fake_fetch(fetch_seconds=1.0) as (clock, starts):
                run_fetch()
        finally:
            summarize._post = saved

        assert starts == [0.0, 65.0, 130.0], starts
        assert clock.sleeps == [62.0, 62.0], clock.sleeps


@case
def a_slow_summary_counts_toward_the_minimum_without_extra_wait():
    with TempCorpus() as conn:
        three_pending(conn)

        def slow_model(body):
            clock.now += 70.0
            return {"response": "ok", "eval_count": 1}

        saved = patch_post(slow_model)
        try:
            with fake_fetch(fetch_seconds=1.0) as (clock, starts):
                run_fetch()
        finally:
            summarize._post = saved

        assert starts == [0.0, 71.0, 142.0], starts
        assert clock.sleeps == [], clock.sleeps


if __name__ == "__main__":
    sys.exit(suite.run())
