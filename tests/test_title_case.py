"""Unit checks for local-AI title casing and its separate database cache."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from support import Suite, TempCorpus, seed
from tessera import cli, config, store, title_case

suite = Suite()
case = suite.case


@case
def title_prompt_and_model_settings_are_loaded():
    source = "Willpower Expert: The Secret To Effortless Discipline - Angela Duckworth"
    body = title_case.build_body(source)
    assert body["model"] == config.TITLE_CASE_MODEL
    assert body["think"] is False
    assert body["options"]["temperature"] == 0
    assert body["options"]["seed"] == config.SUMMARY_SEED
    assert "Willpower expert: the secret to effortless discipline" in body["prompt"]
    assert "How to buy a VR headset" in body["prompt"]
    assert "How would Alan Turing have played the Traitors?" in body["prompt"]
    assert "The Fermentation Project global tasting" in body["prompt"]
    assert "Les petits secrets du miel industriel" in body["prompt"]
    assert body["prompt"].rstrip().endswith(f"TITLE:\n{source}")


@case
def accepts_only_casefold_identical_title_output():
    source = "Willpower Expert: The Secret To Effortless Discipline - Angela Duckworth"
    saved = title_case._post
    try:
        title_case._post = lambda body: {
            "response": "Willpower expert: the secret to effortless discipline - Angela Duckworth\n"
        }
        result = title_case.case_title(source)
    finally:
        title_case._post = saved
    assert result.display_title == (
        "Willpower expert: the secret to effortless discipline - Angela Duckworth"
    )
    assert result.error is None


@case
def rejects_model_output_that_rewrites_words():
    saved = title_case._post
    try:
        title_case._post = lambda body: {"response": "Willpower experts: the secret"}
        result = title_case.case_title("Willpower Expert: The Secret")
    finally:
        title_case._post = saved
    assert result.display_title is None
    assert "changed more than letter casing" in result.error


@case
def unavailable_model_returns_a_fallback_error():
    saved = title_case._post

    def unavailable(body):
        raise ConnectionError("Connection refused")

    try:
        title_case._post = unavailable
        result = title_case.case_title("Willpower Expert")
    finally:
        title_case._post = saved
    assert result.display_title is None
    assert "model unavailable" in result.error


@case
def cached_title_is_separate_from_raw_title_and_removed_from_pending_queue():
    with TempCorpus() as conn:
        source = "WILLPOWER EXPERT: THE SECRET"
        video_id = seed(conn, title=source)
        pending = store.select_titles_pending_case(conn)
        assert len(pending) == 1 and pending[0]["video_id"] == video_id

        assert store.record_display_title(conn, video_id, source, "Willpower expert: the secret")
        conn.commit()
        assert not store.record_display_title(conn, video_id, source, "incorrect retry")

        video = store.get_video(conn, video_id)
        assert video["title"] == source
        assert video["display_title"] == "Willpower expert: the secret"
        listed = store.list_videos(conn)["rows"][0]
        assert listed["title"] == source
        assert listed["display_title"] == "Willpower expert: the secret"
        assert store.select_titles_pending_case(conn) == []
        assert len(store.select_titles_pending_case(conn, include_cached=True)) == 1
        assert store.record_display_title(
            conn, video_id, source, "Willpower expert: the secret!", replace_existing=True
        )
        conn.commit()
        assert store.get_video(conn, video_id)["display_title"] == "Willpower expert: the secret!"


@case
def init_adds_display_title_to_an_existing_schema_without_changing_titles():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)
    saved_path = config.DB_PATH
    config.DB_PATH = db_path
    conn = None
    try:
        conn = store.connect()
        conn.execute(
            """CREATE TABLE videos (
                 video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                 channel_name TEXT, title TEXT NOT NULL, description TEXT,
                 published_at TEXT NOT NULL, url TEXT NOT NULL, first_seen_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """INSERT INTO videos
               VALUES ('old-video', 'chan', 'Channel', 'Original Title', NULL,
                       '2026-01-01T00:00:00Z', 'https://example.invalid', '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        store.init(conn)
        row = conn.execute(
            "SELECT title, display_title FROM videos WHERE video_id = 'old-video'"
        ).fetchone()
        assert row["title"] == "Original Title"
        assert row["display_title"] is None
    finally:
        if conn is not None:
            conn.close()
        config.DB_PATH = saved_path
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(str(db_path) + suffix)
            except OSError:
                pass


@case
def title_processing_failure_does_not_stop_other_pending_titles():
    with TempCorpus() as conn:
        first = seed(conn, video_id="title-1", title="FIRST TITLE")
        second = seed(conn, video_id="title-2", title="SECOND TITLE")
        saved = title_case.case_title

        def fake_case(source):
            if source == "FIRST TITLE":
                return title_case.TitleCaseResult(None, "Ollama offline")
            return title_case.TitleCaseResult("Second title")

        try:
            title_case.case_title = fake_case
            counts = cli._title_case_rows(conn, store.select_titles_pending_case(conn))
        finally:
            title_case.case_title = saved

        assert counts == {"changed": 1, "unchanged": 0, "failed": 1}
        assert store.get_video(conn, first)["display_title"] is None
        assert store.get_video(conn, second)["title"] == "SECOND TITLE"
        assert store.get_video(conn, second)["display_title"] == "Second title"


if __name__ == "__main__":
    raise SystemExit(suite.run())
