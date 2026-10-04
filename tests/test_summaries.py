"""Checks for the summaries table in ``tessera/store.py`` and the call path in
``tessera/summarize.py``.

Plain script, no pytest. Each case runs against a throwaway database and a
stubbed transport, so it never touches ``tessera.db`` and never runs Ollama.
The one thing it does need is the ``prompt`` file, because the real prompt is
part of what gets sent.

    uv run python tests/test_summaries.py
"""

from __future__ import annotations

import json
import sys

from support import Suite, TempCorpus, patch_post, seed
from tessera import cli, config, store, summarize

suite = Suite()
case = suite.case


@case
def larger_model_is_reserved_for_summaries():
    assert config.SUMMARY_MODEL == "gemma4:12b-mlx"
    assert config.TITLE_CASE_MODEL == "gemma4:e4b-mlx"
    assert config.CHAT_MODEL == "gemma4:e4b-mlx"


FAILED = summarize.SummaryResult(summarize.ERROR, "clean text", error="the model returned an empty answer")
DOWN = summarize.SummaryResult(summarize.ERROR, "clean text", error="Connection refused", transport=True)


def model_summary(subject: str, *, french: bool = False) -> str:
    headings = (
        ["1. SUJET", "2. CONCLUSION", "3. RÉSUMÉ", "4. POINTS CLÉS",
         "5. DÉCISION DE VISIONNAGE", "6. RÉSERVES"]
        if french else
        ["1. SUBJECT", "2. CONCLUSION", "3. SUMMARY", "4. KEY TAKEAWAYS",
         "5. WATCHING DECISION", "6. CAVEATS"]
    )
    section3 = " ".join(f"detail{index}" for index in range(80))
    sections = {
        headings[0]: subject,
        headings[1]: "Aucune conclusion claire." if french else "No clear conclusion is stated.",
        headings[2]: section3,
        headings[3]: ["Leçon concrète une.", "Leçon concrète deux."] if french else [
            "One concrete lesson.", "A second concrete lesson."
        ],
        headings[4]: {
            "visual_value": {
                "choice": "indéterminé" if french else "undetermined",
                "justification": "Aucun indice visuel explicite." if french else "No explicit visual cue appears.",
            },
            "watch_recommendation": {
                "choice": "résumé suffisant" if french else "summary sufficient",
                "justification": "Les éléments principaux sont expliqués." if french else "The main points are explained.",
            },
        },
        headings[5]: [],
    }
    return json.dumps({"passages": [], "summary": sections}, ensure_ascii=False)


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
                "response": model_summary("El Mencho arrived."),
                "total_duration": 2_000_000_000,
                "eval_count": 20,
                "eval_duration": 500_000_000,
            }

        saved = patch_post(fake_post)
        try:
            result = summarize.summarize(entry, seed=config.SUMMARY_SEED + 1)
        finally:
            summarize._post = saved

        assert result.status == summarize.OK
        assert result.text.startswith("1. SUBJECT\nEl Mencho arrived.")
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
        assert body["options"]["seed"] == config.SUMMARY_SEED + 1
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
            return {"response": model_summary("La vidéo présente son sujet.", french=True)}

        saved = patch_post(fake_post)
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved

        assert result.status == summarize.OK
        prompt_text = sent["body"]["prompt"]
        assert "LANGUE DE SORTIE OBLIGATOIRE : français (code `fr`)." in prompt_text
        assert "Dans la valeur JSON `summary`" in prompt_text
        assert "`1. SUJET`" in prompt_text and "`6. RÉSERVES`" in prompt_text
        assert "Utilise exactement ces titres, dans cet ordre" in prompt_text
        assert "`résumé suffisant`, `regarder les passages sélectionnés`" in prompt_text
        assert "target ≤ 25 words, hard ceiling 30 words" in prompt_text
        assert "target ≤ 20 words per item, hard ceiling 24" in prompt_text
        assert "target 80–120 words, acceptable range 80–144 words" in prompt_text
        assert "target ≤ 25 words total, hard ceiling 30" in prompt_text
        assert 'Return only one valid JSON object, with exactly these keys in this order: "passages", then "summary".' in prompt_text
        assert "No timed transcript cues are available" in prompt_text
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
def a_non_json_model_response_is_rejected():
    with TempCorpus() as conn:
        entry = store.get_video(conn, seed(conn))
        saved = patch_post(lambda body: {"response": "summary without the required JSON structure"})
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved
        assert result.status == summarize.ERROR
        assert result.transport is False
        assert "response must be a JSON object" in (result.error or "")


@case
def known_duplicate_root_fields_are_ignored_but_unknown_fields_are_rejected():
    payload = json.loads(model_summary("The video explains a topic."))
    sections = payload["summary"]
    payload["key_takeaways"] = sections["4. KEY TAKEAWAYS"]
    payload["watching_decision"] = sections["5. WATCHING DECISION"]
    payload["caveats"] = sections["6. CAVEATS"]

    rendered, passages = summarize._unpack_response(json.dumps(payload))
    assert rendered.startswith("1. SUBJECT\n")
    assert passages == []

    payload["model_notes"] = "should not be silently ignored"
    try:
        summarize._unpack_response(json.dumps(payload))
    except ValueError as exc:
        assert "unknown top-level keys: model_notes" in str(exc)
    else:
        raise AssertionError("unknown top-level fields must still be rejected")


@case
def known_duplicate_root_fields_must_have_the_expected_type():
    payload = json.loads(model_summary("The video explains a topic."))
    payload["key_takeaways"] = "not an array"
    try:
        summarize._unpack_response(json.dumps(payload))
    except ValueError as exc:
        assert "duplicated top-level field 'key_takeaways' has the wrong type" in str(exc)
    else:
        raise AssertionError("a malformed duplicate must still be rejected")


@case
def invalid_model_output_is_retried_immediately_until_success():
    with TempCorpus() as conn:
        video_id = seed(conn)
        responses = [
            summarize.SummaryResult(
                summarize.ERROR,
                "normalized transcript",
                error="invalid model output: bad JSON shape",
            ),
            summarize.SummaryResult(
                summarize.OK,
                "normalized transcript",
                text="valid summary",
            ),
        ]
        calls = []
        seeds = []
        original = summarize.summarize

        def fake_summarize(entry, *, seed=None):
            calls.append(entry["video_id"])
            seeds.append(seed)
            return responses.pop(0)

        summarize.summarize = fake_summarize
        try:
            result = cli._summarize(conn, video_id)
        finally:
            summarize.summarize = original

        row = conn.execute(
            "SELECT status, attempts FROM summaries WHERE video_id = ?", (video_id,)
        ).fetchone()
        assert len(calls) == 2
        assert seeds == [config.SUMMARY_SEED, config.SUMMARY_SEED + 1]
        assert result.status == summarize.OK
        assert row["status"] == summarize.OK and row["attempts"] == 2


@case
def immediate_summary_retries_never_exceed_the_lifetime_attempt_limit():
    with TempCorpus() as conn:
        video_id = seed(conn)
        failed = summarize.SummaryResult(
            summarize.ERROR,
            "normalized transcript",
            error="invalid model output: bad JSON shape",
        )
        store.record_summary(conn, video_id, failed)
        calls = []
        seeds = []
        original = summarize.summarize

        def fake_summarize(entry, *, seed=None):
            calls.append(entry["video_id"])
            seeds.append(seed)
            return failed

        summarize.summarize = fake_summarize
        try:
            result = cli._summarize(conn, video_id)
        finally:
            summarize.summarize = original

        row = conn.execute(
            "SELECT status, attempts FROM summaries WHERE video_id = ?", (video_id,)
        ).fetchone()
        assert len(calls) == config.SUMMARY_MAX_ATTEMPTS - 1
        assert seeds == [config.SUMMARY_SEED + 1, config.SUMMARY_SEED + 2]
        assert result.status == summarize.ERROR
        assert row["attempts"] == config.SUMMARY_MAX_ATTEMPTS
        assert video_id not in {item["video_id"] for item in store.select_unsummarized(conn)}


@case
def forced_summary_retry_uses_new_seeds_and_keeps_attempt_history():
    with TempCorpus() as conn:
        video_id = seed(conn)
        failed = summarize.SummaryResult(
            summarize.ERROR,
            "normalized transcript",
            error="invalid model output: bad JSON shape",
        )
        for _ in range(config.SUMMARY_MAX_ATTEMPTS):
            store.record_summary(conn, video_id, failed)

        seeds = []
        original = summarize.summarize

        def fake_summarize(entry, *, seed=None):
            seeds.append(seed)
            return summarize.SummaryResult(
                summarize.OK,
                "normalized transcript",
                text="valid summary",
            )

        summarize.summarize = fake_summarize
        try:
            result = cli._summarize(conn, video_id, force=True)
        finally:
            summarize.summarize = original

        attempts = conn.execute(
            "SELECT attempts FROM summaries WHERE video_id = ?", (video_id,)
        ).fetchone()["attempts"]
        assert result.status == summarize.OK
        assert seeds == [config.SUMMARY_SEED + config.SUMMARY_MAX_ATTEMPTS]
        assert attempts == config.SUMMARY_MAX_ATTEMPTS + 1


@case
def storing_a_summary_never_touches_the_transcript():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Elmeno and El Meno arrived.")
        before = conn.execute(
            "SELECT text FROM transcripts WHERE video_id = ?", (video_id,)
        ).fetchone()["text"]

        entry = store.get_video(conn, video_id)
        saved = patch_post(lambda body: {"response": model_summary("The transcript describes the subject.")})
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
