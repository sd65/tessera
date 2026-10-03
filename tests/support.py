"""Shared fixtures for the plain-script tests.

``tests/`` is not a package. Each test script is run directly, which puts this
directory on ``sys.path``, so ``import support`` resolves. There is no pytest.

Everything here is deliberately small: a throwaway database, a way to insert a
video with a transcript, the transport seam for the model call, and a tiny
runner that prints one line per case and exits non-zero on failure.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from tessera import config, store, summarize, transcript


class TempCorpus:
    """A fresh database, with ``config.DB_PATH`` pointed at it.

    ``store.connect`` reads ``config.DB_PATH`` at call time, so redirecting the
    module global is enough to isolate a case. The original path is restored and
    the WAL sidecars removed on exit.
    """

    def __enter__(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.path = Path(path)
        self.saved = config.DB_PATH
        config.DB_PATH = self.path
        self.conn = store.connect()
        store.init(self.conn)
        return self.conn

    def __exit__(self, *exc) -> None:
        self.conn.close()
        config.DB_PATH = self.saved
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(str(self.path) + suffix)
            except OSError:
                pass


def seed(
    conn,
    video_id: str = "vid00000001",
    text: str = "Elmeno arrived.",
    title: str = "The Hunt for El Mencho",
    status: str | None = None,
) -> str:
    """Insert one video and one transcript.

    By default the transcript is `ok` with ``text``, the state the summary tests
    need. Pass ``status=store.PENDING`` to leave it as a reserved, never fetched
    row — a retryable fetch target for the spacing test.
    """
    store.upsert_video(conn, {
        "video_id": video_id,
        "channel_id": "chan",
        "channel_name": "chan name",
        "title": title,
        "description": None,
        "published_at": "2026-01-01T00:00:00Z",
        "url": f"https://example.invalid/{video_id}",
    })
    conn.commit()
    store.reserve(conn, video_id)
    conn.commit()
    if status is None:
        store.record(
            conn, video_id,
            transcript.TranscriptResult(transcript.OK, lang="en", kind="auto", text=text),
        )
    return video_id


def patch_post(function):
    """Swap the model transport seam, returning the original for restoration."""
    saved = summarize._post
    summarize._post = function
    return saved


class Suite:
    """A tiny test runner: one list of cases, one PASS/FAIL line each."""

    def __init__(self) -> None:
        self.cases: list = []

    def case(self, function):
        self.cases.append(function)
        return function

    def run(self) -> int:
        failures = 0
        for function in self.cases:
            try:
                function()
            except AssertionError as error:
                failures += 1
                print(f"FAIL  {function.__name__}  {error}")
            else:
                print(f"PASS  {function.__name__}")
        print(f"\n{len(self.cases) - failures}/{len(self.cases)} passed")
        return 1 if failures else 0
