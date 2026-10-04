"""Stable watch recommendations and their one-time summary backfill."""

from __future__ import annotations

import os
import sqlite3
import tempfile

from support import Suite, TempCorpus, seed
from tessera import recommendation, store, summarize

suite = Suite()
case = suite.case


@case
def localized_section_5b_choices_map_to_stable_codes():
    assert recommendation.from_choice("summary sufficient") == "summary_sufficient"
    assert recommendation.from_choice("Résumé suffisant") == "summary_sufficient"
    assert recommendation.from_choice("watch selected passages") == "selected_passages"
    assert recommendation.from_choice(
        "regarder les passages sélectionnés"
    ) == "selected_passages"
    assert recommendation.from_choice("full video recommended") == "full_video_recommended"
    assert recommendation.from_choice(
        "visionnage complet recommandé"
    ) == "full_video_recommended"
    assert recommendation.from_choice("new untranslated choice") == "undetermined"


@case
def legacy_summary_parser_reads_only_the_section_5b_choice():
    text = """1. SUBJECT
Video about a topic.

5. WATCHING DECISION
a) likely substantial — Visuals add information.
b) full video recommended — The demonstration is essential.

6. CAVEATS
"""
    assert recommendation.from_summary(text) == "full_video_recommended"
    assert recommendation.from_summary("1. SUBJECT\nb) full video recommended — no") == "undetermined"


@case
def generated_recommendation_is_persisted_and_included_in_video_lists():
    with TempCorpus() as conn:
        video_id = seed(conn)
        result = summarize.SummaryResult(
            summarize.OK,
            "normalized transcript",
            text="1. SUBJECT\nExample.\n\n5. WATCHING DECISION\nb) summary sufficient — Clear.",
            watch_recommendation="summary_sufficient",
        )
        store.record_summary(conn, video_id, result)

        assert store.get_video(conn, video_id)["watch_recommendation"] == "summary_sufficient"
        assert store.list_videos(conn)["rows"][0]["watch_recommendation"] == "summary_sufficient"


@case
def init_adds_the_recommendation_column_and_backfills_existing_summaries_idempotently():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # This is the pre-migration schema: no recommendation column exists.
        conn.execute(
            """CREATE TABLE videos (
                 video_id TEXT PRIMARY KEY,
                 channel_id TEXT NOT NULL,
                 channel_name TEXT,
                 title TEXT NOT NULL,
                 published_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE summaries (
                 video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
                 status TEXT NOT NULL,
                 text TEXT,
                 normalized_text TEXT,
                 model TEXT,
                 attempts INTEGER DEFAULT 0,
                 error TEXT,
                 generated_at TEXT NOT NULL
               )"""
        )
        examples = {
            "old-en": "5. WATCHING DECISION\nb) summary sufficient — Main points are explained.",
            "old-fr": "5. DÉCISION DE VISIONNAGE\nb) regarder les passages sélectionnés — Two passages help.",
            "old-unknown": "5. WATCHING DECISION\nb) maybe a few parts — Not a recognized choice.",
            "old-missing": "1. SUBJECT\nNo decision stored.",
        }
        for video_id, text in examples.items():
            conn.execute(
                "INSERT INTO videos (video_id, channel_id, title, published_at) VALUES (?, 'c', ?, '2026-01-01')",
                (video_id, video_id),
            )
            conn.execute(
                "INSERT INTO summaries (video_id, status, text, generated_at) VALUES (?, 'ok', ?, '2026-01-01')",
                (video_id, text),
            )
        conn.commit()

        store.init(conn)
        expected = {
            "old-en": "summary_sufficient",
            "old-fr": "selected_passages",
            "old-unknown": "undetermined",
            "old-missing": "undetermined",
        }
        actual = {
            row["video_id"]: row["watch_recommendation"]
            for row in conn.execute(
                "SELECT video_id, watch_recommendation FROM summaries"
            )
        }
        assert actual == expected

        store.init(conn)
        repeated = {
            row["video_id"]: row["watch_recommendation"]
            for row in conn.execute(
                "SELECT video_id, watch_recommendation FROM summaries"
            )
        }
        assert repeated == expected
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(suite.run())
