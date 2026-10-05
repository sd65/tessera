"""Cue offsets and transcript-grounded summary passage resolution."""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.request import urlopen

from support import Suite, TempCorpus, patch_post, seed
from tessera import config, normalize, store, summarize, transcript, web

suite = Suite()
case = suite.case


@case
def cleaned_text_and_unicode_offsets_match_the_legacy_flattening():
    snippets = [
        SimpleNamespace(text="Été 😊", start=1.2346, duration=1.0054),
        SimpleNamespace(text="ça va", start=3.5, duration=0.75),
    ]
    text, cues = transcript.clean_captions_with_cues(snippets)

    assert text == transcript.clean_captions("\n".join(item.text for item in snippets))
    assert text == "Été 😊 ça va"
    assert [text[cue.char_start:cue.char_end] for cue in cues] == ["Été 😊", "ça va"]
    assert [(cue.start_ms, cue.duration_ms) for cue in cues] == [(1235, 1005), (3500, 750)]
    assert [(cue.char_start, cue.char_end) for cue in cues] == [(0, 5), (6, 11)]


@case
def video_duration_is_stored_and_included_in_the_detail_payload():
    with TempCorpus() as conn:
        video_id = seed(conn)
        store.record(conn, video_id, transcript.TranscriptResult(
            transcript.OK,
            lang="fr",
            kind="manual",
            text="Un résumé.",
            duration_seconds=3725,
        ))

        detail = store.get_video(conn, video_id)
        assert detail["duration_seconds"] == 3725


@case
def ambiguous_cleanup_keeps_text_but_refuses_to_store_bad_offsets():
    text, cues = transcript.clean_captions_with_cues([
        SimpleNamespace(text="[", start=0, duration=1),
        SimpleNamespace(text="Music]", start=1, duration=1),
    ])
    assert text == ""
    assert cues == ()


@case
def failed_fetch_preserves_stored_transcript_and_cues():
    with TempCorpus() as conn:
        video_id = seed(conn, text="old transcript")
        cues = (
            transcript.TranscriptCue(0, 1200, 2000, 0, 3),
            transcript.TranscriptCue(1, 4000, 1000, 4, 14),
        )
        store.record(conn, video_id, transcript.TranscriptResult(
            transcript.OK, lang="en", kind="auto", text="new transcript", cues=cues,
        ))
        store.record(conn, video_id, transcript.TranscriptResult(
            transcript.ERROR, error="temporary failure",
        ))

        assert conn.execute(
            "SELECT text FROM transcripts WHERE video_id = ?", (video_id,)
        ).fetchone()["text"] == "new transcript"
        assert [row["start_ms"] for row in store.transcript_cues(conn, video_id)] == [1200, 4000]


@case
def transcript_and_cues_roll_back_together_if_cue_persistence_fails():
    with TempCorpus() as conn:
        video_id = seed(conn, text="old transcript")
        original = transcript.TranscriptCue(0, 1000, 1000, 0, 3)
        store.record(conn, video_id, transcript.TranscriptResult(
            transcript.OK, text="old transcript", cues=(original,),
        ))
        duplicate_index = (
            transcript.TranscriptCue(0, 2000, 1000, 0, 3),
            transcript.TranscriptCue(0, 3000, 1000, 4, 7),
        )
        try:
            store.record(conn, video_id, transcript.TranscriptResult(
                transcript.OK, text="new text", cues=duplicate_index,
            ))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("duplicate cue indexes should violate the primary key")

        assert conn.execute(
            "SELECT text FROM transcripts WHERE video_id = ?", (video_id,)
        ).fetchone()["text"] == "old transcript"
        assert store.transcript_cues(conn, video_id)[0]["start_ms"] == 1000


@case
def normalization_maps_length_changes_and_boundary_crossings_to_first_cue():
    cues = [
        {"cue_index": 0, "start_ms": 1000, "char_start": 0, "char_end": 3},
        {"cue_index": 1, "start_ms": 4000, "char_start": 4, "char_end": 7},
    ]
    within = [normalize.Edit("b", "BBBB", "test", 1.0, 1, 2)]
    mapped = summarize.map_cue_offsets(cues, within)
    assert [(cue["char_start"], cue["char_end"]) for cue in mapped] == [(0, 6), (7, 10)]

    at_boundary = [normalize.Edit("bc", "WXYZ", "test", 1.0, 1, 3)]
    mapped = summarize.map_cue_offsets(cues, at_boundary)
    assert [(cue["char_start"], cue["char_end"]) for cue in mapped] == [(0, 5), (6, 9)]

    crossing = [
        {"cue_index": 0, "start_ms": 1000, "char_start": 0, "char_end": 6},
        {"cue_index": 1, "start_ms": 4000, "char_start": 7, "char_end": 10},
    ]
    edit = [normalize.Edit("cDEF g", "XYZ", "test", 1.0, 2, 8)]
    mapped = summarize.map_cue_offsets(crossing, edit)
    assert [(cue["char_start"], cue["char_end"]) for cue in mapped] == [(0, 5), (5, 7)]

    begins_in_separator = [normalize.Edit(" D", "XYZ", "test", 1.0, 3, 5)]
    mapped = summarize.map_cue_offsets(cues, begins_in_separator)
    assert [(cue["char_start"], cue["char_end"]) for cue in mapped] == [(0, 3), (3, 8)]


@case
def exact_quotes_link_once_to_the_first_matching_cue_only():
    text = (
        "alpha beta gamma delta epsilon zeta eta theta iota one two three four five six seven eight "
        "one two three four five six seven eight"
    )
    iota_start = text.index("iota")
    quote = "beta gamma delta epsilon zeta eta theta iota"
    cues = [
        {"cue_index": 0, "start_ms": 1000, "duration_ms": 2000, "char_start": 0, "char_end": 5},
        {"cue_index": 1, "start_ms": 4000, "duration_ms": 2000, "char_start": 6, "char_end": iota_start},
        {"cue_index": 2, "start_ms": 8000, "duration_ms": 1000, "char_start": iota_start, "char_end": iota_start + 4},
    ]
    passages = summarize.resolve_passages(text, cues, [], [
        {"label": "Useful moment", "quote": quote},
        {"label": "Repeated quote", "quote": "one two three four five six seven eight"},
        {"label": "Missing", "quote": "not in transcript"},
        {"label": "Malformed", "quote": 42},
    ])
    assert passages == ({
        "label": "Useful moment", "quote": quote, "start_ms": 4000,
        "duration_ms": 5000,
    },)


@case
def structured_model_response_is_resolved_and_stored_separately():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Alpha beta gamma are the key details that matter.")
        entry = store.get_video(conn, video_id)
        entry["_cues"] = [
            {"cue_index": 0, "start_ms": 2300, "duration_ms": 1000, "char_start": 0, "char_end": 5},
            {"cue_index": 1, "start_ms": 6800, "duration_ms": 2500, "char_start": 6, "char_end": 48},
        ]
        answer = {
            "summary": {
                "1. SUBJECT": "The video explains an exact key point.",
                "2. CONCLUSION": "No clear conclusion is stated.",
                "3. SUMMARY": " ".join(f"context{index}" for index in range(80)),
                "4. KEY TAKEAWAYS": ["First concrete lesson.", "Second concrete lesson."],
                "5. WATCHING DECISION": {
                    "visual_value": {
                        "choice": "undetermined",
                        "justification": "No explicit visual cue appears.",
                    },
                    "watch_recommendation": {
                        "choice": "watch selected passages",
                        "justification": "The linked quote contains the key point.",
                    },
                },
                "6. CAVEATS": [],
            },
            "passages": [{
                "label": "The key point",
                "quote": "beta gamma are the key details that matter.",
            }],
        }
        saved = patch_post(lambda body: {"response": json.dumps(answer)})
        try:
            result = summarize.summarize(entry)
        finally:
            summarize._post = saved

        assert result.status == summarize.OK
        assert result.watch_recommendation == "selected_passages"
        assert result.text.startswith("1. SUBJECT\nThe video explains an exact key point.")
        assert result.passages == ({
            "label": "The key point",
            "quote": "beta gamma are the key details that matter.",
            "start_ms": 6800,
            "duration_ms": 2500,
        },)
        store.record_summary(conn, video_id, result)
        detail = store.get_video(conn, video_id)
        assert detail["passages"] == [result.passages[0]]
        assert detail["watch_recommendation"] == "selected_passages"
        assert "transcript_cues" not in detail


@case
def structured_summary_sections_are_rendered_and_word_limits_are_enforced():
    summary = {
        "1. SUJET": "The video explains a local farming method.",
        "2. CONCLUSION": "The method improves yield when conditions are suitable.",
        "3. SUMMARY": " ".join(f"detail{index}" for index in range(80)),
        "4. POINTS CLÉS": ["First specific lesson.", "Second specific lesson."],
        "5. DÉCISION DE VISIONNAGE": {
            "visual_value": {
                "choice": "undetermined",
                "justification": "The transcript gives no explicit visual cue.",
            },
            "watch_recommendation": {
                "choice": "summary sufficient",
                "justification": "The transcript explains the main points clearly.",
            },
        },
        "6. RÉSERVES": [],
    }
    rendered = summarize._format_summary_object(summary)
    assert rendered.startswith("1. SUJET\n")
    assert "- First specific lesson." in rendered
    assert "a) undetermined — The transcript gives no explicit visual cue." in rendered
    assert rendered.endswith("6. RÉSERVES")

    # Section 1 accepts a small overshoot over the prompt's ideal maximum, but
    # remains a hard limit after the configured tolerance.
    subject_ceiling = config.tolerated_summary_word_limit(
        config.SUMMARY_SUBJECT_TARGET_MAX_WORDS
    )
    summary["1. SUJET"] = " ".join(f"subject{index}" for index in range(subject_ceiling))
    summarize._format_summary_object(summary)
    summary["1. SUJET"] = " ".join(f"subject{index}" for index in range(subject_ceiling + 1))
    try:
        summarize._format_summary_object(summary)
    except ValueError as exc:
        assert f"section 1 has {subject_ceiling + 1} words" in str(exc)
    else:
        raise AssertionError("the configured subject word ceiling must be enforced")
    summary["1. SUJET"] = "The video explains a local farming method."

    summary["3. SUMMARY"] = " ".join(f"detail{index}" for index in range(79))
    try:
        summarize._format_summary_object(summary)
    except ValueError as exc:
        assert "section 3 has 79 words" in str(exc)
    else:
        raise AssertionError("the 80-word minimum must be enforced")

    summary["3. SUMMARY"] = " ".join(f"detail{index}" for index in range(141))
    summarize._format_summary_object(summary)

    body_ceiling = config.tolerated_summary_word_limit(
        config.SUMMARY_BODY_TARGET_MAX_WORDS
    )
    summary["3. SUMMARY"] = " ".join(f"detail{index}" for index in range(body_ceiling + 1))
    try:
        summarize._format_summary_object(summary)
    except ValueError as exc:
        assert f"section 3 has {body_ceiling + 1} words" in str(exc)
    else:
        raise AssertionError("the configured section 3 ceiling must be enforced")


@case
def percentage_tolerance_is_shared_by_every_summary_prose_limit():
    summary = {
        "1. SUBJECT": "A short subject sentence.",
        "2. CONCLUSION": "A concise conclusion.",
        "3. SUMMARY": " ".join(f"detail{index}" for index in range(config.SUMMARY_BODY_MIN_WORDS)),
        "4. KEY TAKEAWAYS": ["First lesson.", "Second lesson."],
        "5. WATCHING DECISION": {
            "visual_value": {"choice": "undetermined", "justification": "No cue."},
            "watch_recommendation": {
                "choice": "summary sufficient", "justification": "The points are clear."
            },
        },
        "6. CAVEATS": [],
    }

    def rejected():
        try:
            summarize._format_summary_object(summary)
        except ValueError:
            return
        raise AssertionError("content beyond a configured word ceiling must be rejected")

    cases = [
        ("2. CONCLUSION", config.SUMMARY_CONCLUSION_TARGET_MAX_WORDS, "A concise conclusion."),
        (
            "3. SUMMARY",
            config.SUMMARY_BODY_TARGET_MAX_WORDS,
            " ".join(f"detail{index}" for index in range(config.SUMMARY_BODY_MIN_WORDS)),
        ),
    ]
    for section, target, original in cases:
        ceiling = config.tolerated_summary_word_limit(target)
        summary[section] = " ".join(f"word{index}" for index in range(ceiling))
        summarize._format_summary_object(summary)
        summary[section] = " ".join(f"word{index}" for index in range(ceiling + 1))
        rejected()
        summary[section] = original

    takeaway_ceiling = config.tolerated_summary_word_limit(
        config.SUMMARY_TAKEAWAY_TARGET_MAX_WORDS
    )
    summary["4. KEY TAKEAWAYS"] = [
        " ".join(f"word{index}" for index in range(takeaway_ceiling)), "Second lesson."
    ]
    summarize._format_summary_object(summary)
    summary["4. KEY TAKEAWAYS"][0] += " extra"
    rejected()
    summary["4. KEY TAKEAWAYS"] = ["First lesson.", "Second lesson."]

    decision_ceiling = config.tolerated_summary_word_limit(
        config.SUMMARY_DECISION_TARGET_MAX_WORDS
    )
    decision = summary["5. WATCHING DECISION"]["visual_value"]
    decision["justification"] = " ".join(f"word{index}" for index in range(decision_ceiling - 1))
    summarize._format_summary_object(summary)
    decision["justification"] += " extra"
    rejected()
    decision["justification"] = "No cue."

    caveat_ceiling = config.tolerated_summary_word_limit(
        config.SUMMARY_CAVEAT_TARGET_MAX_WORDS
    )
    summary["6. CAVEATS"] = [" ".join(f"word{index}" for index in range(caveat_ceiling))]
    summarize._format_summary_object(summary)
    summary["6. CAVEATS"][0] += " extra"
    rejected()


@case
def detail_api_exposes_passages_but_not_internal_cue_offsets():
    with TempCorpus() as conn:
        quote = "A useful line with enough exact words to make this a real passage."
        video_id = seed(conn, text=quote)
        passage = {
            "label": "Useful line",
            "quote": quote,
            "start_ms": 1234, "duration_ms": 2500,
        }
        store.record_summary(conn, video_id, summarize.SummaryResult(
            summarize.OK,
            "A useful line.",
            text="summary",
            passages=(passage,),
        ))

        class QuietHandler(web.Handler):
            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/api/videos/{video_id}",
                timeout=5,
            ) as response:
                payload = json.loads(response.read())
            assert payload["passages"] == [passage]
            assert "transcript_cues" not in payload and "char_start" not in payload
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@case
def init_adds_duration_to_existing_passage_tables():
    with TempCorpus() as conn:
        conn.execute("DROP TABLE summary_passages")
        conn.execute(
            """CREATE TABLE summary_passages (
                 video_id TEXT NOT NULL REFERENCES summaries(video_id) ON DELETE CASCADE,
                 ordinal INTEGER NOT NULL,
                 label TEXT NOT NULL,
                 quote TEXT NOT NULL,
                 start_ms INTEGER NOT NULL,
                 PRIMARY KEY(video_id, ordinal)
               )"""
        )
        conn.commit()
        store.init(conn)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(summary_passages)")
        }
        assert "duration_ms" in columns


@case
def init_adds_video_duration_to_existing_transcript_tables():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
            CREATE TABLE videos (
                video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                channel_name TEXT, title TEXT NOT NULL, description TEXT,
                published_at TEXT NOT NULL, url TEXT NOT NULL, first_seen_at TEXT NOT NULL
            );
            CREATE TABLE transcripts (
                video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
                lang TEXT, kind TEXT, status TEXT NOT NULL, text TEXT,
                attempts INTEGER DEFAULT 0, error TEXT, fetched_at TEXT NOT NULL
            );
        """)
        store.init(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(transcripts)")}
        assert "duration_seconds" in columns
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(suite.run())
