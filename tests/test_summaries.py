"""Checks for the summaries table in ``tessera/store.py`` and the call path in
``tessera/summarize.py``.

Plain script, no pytest. Each case runs against a throwaway database and a
stubbed transport, so it never touches ``tessera.db`` and never runs Ollama.
The one thing it does need is the ``prompt`` file, because the real prompt is
part of what gets sent.

    uv run python tests/test_summaries.py
"""

from __future__ import annotations

import sys

from support import Suite, TempCorpus, patch_post, seed
from tessera import cli, config, store, summarize

suite = Suite()
case = suite.case

FAILED = summarize.SummaryResult(summarize.ERROR, "clean text", error="the model returned an empty answer")
DOWN = summarize.SummaryResult(summarize.ERROR, "clean text", error="Connection refused", transport=True)


@case
def backfill_selects_an_unsummarized_transcript():
    with TempCorpus() as conn:
        video_id = seed(conn)
        row = store.get_video(conn, video_id)
        assert row["summary"] is None and row["summary_status"] is None
        assert [r["video_id"] for r in store.select_unsummarized(conn)] == [video_id]


@case
def a_stored_ok_summary_is_not_reselected():
    with TempCorpus() as conn:
        video_id = seed(conn)
        store.record_summary(
            conn, video_id,
            summarize.SummaryResult(summarize.OK, "El Mencho arrived.", text="1. SUBJECT\nEl Mencho."),
        )
        assert store.select_unsummarized(conn) == []

        row = store.get_video(conn, video_id)
        assert row["summary_status"] == summarize.OK
        assert row["summary"] == "1. SUBJECT\nEl Mencho."
        assert row["normalized_text"] == "El Mencho arrived."
        assert row["summary_model"] == config.SUMMARY_MODEL
        assert row["summary_error"] is None


@case
def a_failed_summary_is_retried_until_max_attempts():
    with TempCorpus() as conn:
        video_id = seed(conn)
        for attempt in range(1, config.SUMMARY_MAX_ATTEMPTS + 1):
            store.record_summary(conn, video_id, FAILED)
            row = conn.execute(
                "SELECT attempts FROM summaries WHERE video_id = ?", (video_id,)
            ).fetchone()
            assert row["attempts"] == attempt, (attempt, row["attempts"])

            selected = {r["video_id"] for r in store.select_unsummarized(conn)}
            if attempt < config.SUMMARY_MAX_ATTEMPTS:
                assert video_id in selected, f"still retryable at attempt {attempt}"
            else:
                assert video_id not in selected, "retired at the cap"


@case
def a_transport_failure_does_not_count_as_an_attempt():
    with TempCorpus() as conn:
        video_id = seed(conn)
        for _ in range(config.SUMMARY_MAX_ATTEMPTS + 2):
            store.record_summary(conn, video_id, DOWN)

        row = conn.execute(
            "SELECT attempts, status, error, normalized_text FROM summaries WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        assert row["attempts"] == 0, row["attempts"]
        assert row["status"] == summarize.ERROR
        # The grounded text is still stored, so the UI can show it while the
        # summary is retried.
        assert row["normalized_text"] == "clean text"
        assert video_id in {r["video_id"] for r in store.select_unsummarized(conn)}


@case
def the_model_is_fed_the_normalised_text():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Elmeno was here.")
        entry = store.get_video(conn, video_id)

        sent: dict = {}

        def fake_post(body):
            sent["body"] = body
            return {
                "response": "1. SUBJECT\nEl Mencho arrived.",
                "total_duration": 2_000_000_000,
                "eval_count": 20,
                "eval_duration": 500_000_000,
            }

        saved = patch_post(fake_post)
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved

        assert result.status == summarize.OK
        assert result.text == "1. SUBJECT\nEl Mencho arrived."
        assert result.processing_seconds == 2.0
        assert result.eval_count == 20
        assert result.tokens_per_second == 40.0
        note = cli._summary_note(result)
        assert "2.0s" in note and "40.0 tok/s" in note, note
        assert "El Mencho" in result.normalized_text
        assert "Elmeno" not in result.normalized_text

        body = sent["body"]
        assert "El Mencho" in body["prompt"]
        assert "MANDATORY OUTPUT LANGUAGE: English" in body["prompt"]
        assert body["model"] == config.SUMMARY_MODEL
        assert body["think"] is False
        assert body["options"]["temperature"] == config.SUMMARY_TEMPERATURE
        assert body["options"]["seed"] == config.SUMMARY_SEED
        assert body["options"]["num_predict"] == config.SUMMARY_NUM_PREDICT
        assert body["options"]["num_ctx"] >= config.SUMMARY_MIN_CTX


@case
def french_transcript_sets_french_output_language_in_prompt():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Bonjour, voici le sujet de la vidéo.")
        conn.execute("UPDATE transcripts SET lang = 'fr' WHERE video_id = ?", (video_id,))
        conn.commit()
        entry = store.get_video(conn, video_id)
        sent: dict = {}

        def fake_post(body):
            sent["body"] = body
            return {"response": "1. SUJET\nLa vidéo présente son sujet."}

        saved = patch_post(fake_post)
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved

        assert result.status == summarize.OK
        prompt_text = sent["body"]["prompt"]
        assert "LANGUE DE SORTIE OBLIGATOIRE : français (code `fr`)." in prompt_text
        assert "titres de sections" in prompt_text
        assert "Commence directement par la section 1." in prompt_text
        assert "Utilise ces intitulés : SUJET, CONCLUSION, RÉSUMÉ" in prompt_text
        assert "Do not add a document title, introduction, preamble or extra section." in prompt_text
        assert prompt_text.index("TRANSCRIPT:\n") < prompt_text.index(
            "FINAL OUTPUT REQUIREMENTS:\n"
        ) < prompt_text.index("LANGUE DE SORTIE OBLIGATOIRE")


@case
def a_transport_error_is_marked_not_an_answer():
    with TempCorpus() as conn:
        entry = store.get_video(conn, seed(conn))

        def boom(body):
            raise summarize.TransportError("Connection refused")

        saved = patch_post(boom)
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved

        assert result.status == summarize.ERROR
        assert result.transport is True
        assert result.normalized_text, "the grounded text is still produced"
        assert result.processing_seconds is not None, "wall time is still reported"


@case
def an_empty_answer_is_an_error_without_transport():
    with TempCorpus() as conn:
        entry = store.get_video(conn, seed(conn))
        saved = patch_post(lambda body: {"response": "", "eval_count": config.SUMMARY_NUM_PREDICT})
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved

        assert result.status == summarize.ERROR
        assert result.transport is False
        assert "budget" in (result.error or "")


@case
def storing_a_summary_never_touches_the_transcript():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Elmeno and El Meno arrived.")
        before = conn.execute(
            "SELECT text FROM transcripts WHERE video_id = ?", (video_id,)
        ).fetchone()["text"]

        entry = store.get_video(conn, video_id)
        saved = patch_post(lambda body: {"response": "done"})
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved
        store.record_summary(conn, video_id, result)

        after = conn.execute(
            "SELECT text FROM transcripts WHERE video_id = ?", (video_id,)
        ).fetchone()["text"]
        assert after == before


if __name__ == "__main__":
    sys.exit(suite.run())
