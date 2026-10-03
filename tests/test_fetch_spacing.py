"""Checks that a failed summary restores the spacing before the next fetch.

Plain script, no pytest. The RSS path is stubbed away, the transcript fetch is
faked, the model call is made to fail, and every ``time.sleep`` is recorded — so
the pacing of the fetch loop is asserted without waiting and without touching
YouTube or Ollama.

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


def boom(body):
    raise summarize.TransportError("Connection refused")


@contextlib.contextmanager
def fake_fetch(status=transcript.OK):
    """Stub the transcript fetch and record sleep durations instead of sleeping."""
    sleeps: list[float] = []
    real_fetch = transcript.fetch_transcript
    real_sleep = cli.time.sleep

    def fake_transcript(video_id):
        return transcript.TranscriptResult(status, lang="en", kind="auto", text="Elmeno arrived.")

    transcript.fetch_transcript = fake_transcript
    cli.time.sleep = sleeps.append
    try:
        yield sleeps
    finally:
        transcript.fetch_transcript = real_fetch
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
def a_failed_summary_pauses_before_the_next_fetch():
    with TempCorpus() as conn:
        three_pending(conn)
        saved = patch_post(boom)
        try:
            with fake_fetch() as sleeps:
                assert run_fetch() == 0
        finally:
            summarize._post = saved
        # Three fetch items, no sleep before the first: one pause per remaining
        # item, each the fallback delay rather than POLITE_DELAY.
        assert sleeps == [config.SUMMARY_FAILURE_DELAY] * 2, sleeps


@case
def a_successful_summary_keeps_the_polite_delay():
    with TempCorpus() as conn:
        three_pending(conn)
        saved = patch_post(lambda body: {"response": "ok", "eval_count": 1})
        try:
            with fake_fetch() as sleeps:
                run_fetch()
        finally:
            summarize._post = saved
        assert sleeps == [config.POLITE_DELAY] * 2, sleeps


@case
def the_pause_only_follows_a_failure():
    with TempCorpus() as conn:
        three_pending(conn)

        calls = {"n": 0}

        def flaky(body):
            calls["n"] += 1
            if calls["n"] == 1:
                raise summarize.TransportError("Connection refused")
            return {"response": "ok", "eval_count": 1}

        saved = patch_post(flaky)
        try:
            with fake_fetch() as sleeps:
                run_fetch()
        finally:
            summarize._post = saved
        # First item's summary fails -> fallback pause before the second; the
        # second succeeds -> the ordinary polite delay before the third.
        assert sleeps == [config.SUMMARY_FAILURE_DELAY, config.POLITE_DELAY], sleeps


if __name__ == "__main__":
    sys.exit(suite.run())
