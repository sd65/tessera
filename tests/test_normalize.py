"""Unit and golden checks for ``tessera/normalize.py``.

No test framework is installed, so this is a plain script: it runs every case,
prints one line each, and exits non-zero on failure. Run from the repo root:

    uv run python tests/test_normalize.py
"""

from __future__ import annotations

import sys

from tessera import normalize, store

CASES: list = []


def case(function):
    CASES.append(function)
    return function


def load(video_id: str) -> tuple[dict, str]:
    conn = store.connect()
    try:
        loaded = normalize._load_video(conn, video_id)
    finally:
        conn.close()
    assert loaded is not None, f"{video_id} missing from the corpus"
    return loaded


@case
def identity_without_metadata():
    text = "El Meno was here."
    assert normalize.normalize(text, {}) == (text, [])
    assert normalize.build_glossary({}) == []


@case
def case_and_accent_only_is_left_alone():
    # Same word, different casing/accents: the transcript is not wrong.
    text = "At Cafe Central today."
    metadata = {"title": "Café Central"}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def plural_is_not_a_misspelling():
    text = "Soldiers ate well."
    metadata = {"title": "Soldier Cooking Marathon"}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def possessive_is_not_a_misspelling():
    text = "London's bridge."
    metadata = {"title": "London"}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def lowercase_word_is_never_a_name():
    text = "meno is not a word here."
    metadata = {"title": "The Hunt for El Mencho"}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def repairs_a_multiword_last_token():
    text = "The man, El Meno, spoke."
    metadata = {"title": "The Hunt for El Mencho"}
    clean, edits = normalize.normalize(text, metadata)
    assert clean == "The man, El Mencho, spoke."
    assert len(edits) == 1
    assert (edits[0].source, edits[0].target) == ("El Meno", "El Mencho")


@case
def repairs_a_fused_token():
    # YouTube fuses the name: "Elmeno" is one token for "El Mencho".
    text = "Elmeno arrived."
    metadata = {"title": "The Hunt for El Mencho"}
    clean, edits = normalize.normalize(text, metadata)
    assert clean == "El Mencho arrived."
    assert len(edits) == 1


@case
def leading_words_must_match():
    text = "Katherine and her team left."
    metadata = {"description": "With Katherine Long."}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def term_may_not_extend_a_complete_word():
    text = "Countless people came."
    metadata = {"description": "A show from Countless AI."}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def acronyms_are_not_fuzzy_matched():
    text = "Fernan spoke."
    metadata = {"description": "FERN"}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def ambiguous_match_is_left_alone():
    # Two equally-plausible names: refuse to guess.
    text = "El Menchu arrived."
    metadata = {"title": "El Mencho", "description": "Also known as El Menchi."}
    assert normalize.normalize(text, metadata) == (text, [])


@case
def idempotent_on_synthetic_input():
    text = "Elmeno and El Meno arrived."
    metadata = {"title": "The Hunt for El Mencho"}
    clean, edits = normalize.normalize(text, metadata)
    assert edits, "expected a correction"
    again, edits2 = normalize.normalize(clean, metadata)
    assert again == clean
    assert edits2 == []


@case
def golden_el_mencho():
    metadata, text = load("d61n1isTfeo")
    clean, edits = normalize.normalize(text, metadata)

    counts = {(item["source"], item["target"]): item["count"] for item in normalize.edit_summary(edits)}
    assert counts.get(("El Meno", "El Mencho")) == 1, counts
    assert edits, "expected corrections"
    assert all(edit.target == "El Mencho" for edit in edits), edits

    # CJNG and Kaliscoco are already correct / not in the metadata: untouched.
    assert clean.count("CJNG") == text.count("CJNG")
    assert "Kaliscoco" in clean

    # Re-running over the result is a no-op.
    again, edits2 = normalize.normalize(clean, metadata)
    assert again == clean
    assert edits2 == []


@case
def townsends_metadata_is_boilerplate_only():
    # Their descriptions are links and Patreon pitches: no glossary, no edits.
    metadata, text = load("eCt3AAx0htM")
    assert normalize.clean_metadata(metadata.get("description")) == ""
    assert normalize.normalize(text, metadata) == (text, [])


@case
def database_transcript_is_not_mutated():
    metadata, text = load("d61n1isTfeo")
    normalize.normalize(text, metadata)
    _, again = load("d61n1isTfeo")
    assert again == text


def main() -> int:
    failures = 0
    for function in CASES:
        try:
            function()
        except AssertionError as error:
            failures += 1
            print(f"FAIL  {function.__name__}  {error}")
        else:
            print(f"PASS  {function.__name__}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
