"""Metadata-grounded ASR entity normalization.

A pure, deterministic pass over a transcript. It rewrites a name only when a
near-match already appears in the video's *own* title, description or channel
name — so every replacement is traceable to a string a human can check, and a
bad edit is inspectable rather than a silent hallucination. No model, no
external knowledge.

    normalize(text, metadata) -> (clean_text, edits)

High precision is the whole point, so the rules are deliberately narrow:

- the glossary holds acronyms and multiword name runs, not every capitalised
  word in the description (that path promoted ``Cartel``/``Quick``/``Mexican``
  and "corrected" ordinary words);
- a match must be name-like, may not swallow words the name does not have, and
  — when the word counts agree — every leading word must match exactly, so
  ``Katherine and`` is not rewritten to ``Katherine Long``;
- a word that already differs only by case, accents, plural or possessive is
  *not* touched: those are the transcript's own spelling, not a name YouTube
  mangled;
- acronyms are never fuzzy-matched, so ``Fernan`` is not forced into ``FERN``.

It never writes the corpus. The production pipeline calls ``normalize()`` from
``tessera.summarize`` before the model ever sees the text; the module can also be
run directly to inspect one stored transcript:

    uv run python -m tessera.normalize d61n1isTfeo      # show the diff
    uv run python tests/test_normalize.py               # unit + golden checks
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher

# Same word count, one word: a lone word has no context, so it must be a very
# close match. This rejects prefix+suffix coincidences like coating/cooking and
# raven/tavern while keeping complete/compleat.
SINGLE_THRESHOLD = 0.85
# Same word count, several words: the leading words already match exactly, so
# the final word may differ more (meno -> mencho is 0.8).
LAST_THRESHOLD = 0.70
# Fused/partial window (fewer words than the name): required common-prefix
# fraction. Calibrated on "elmeno"/"elmencho" = 5/8 = 0.625.
PREFIX_THRESHOLD = 0.60
# Two candidates this close for one window is ambiguity, not a match.
EPSILON = 0.05
# Longest transcript n-gram considered; glossary runs are capped to match.
MAX_WINDOW = 3
# A runaway matcher on a long transcript is a bug, not a feature.
MAX_EDITS = 200
# Shortest significant collapsed form.
MIN_CHARS = 4

# Letters only: apostrophes and hyphens are separators, so a possessive
# ("China's", "Joe Pistone's") splits into the name plus a stray "s" that no
# name run can absorb. That is what keeps possessives out of the glossary.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+\.\S+")
# The tail of a fern description is a music/source dump; drop it whole.
_HEADING = re.compile(
    r"^\s*(music list|sources|artlist|epidemic sound|lens distortions|tom fox)\s*:?\s*$",
    re.IGNORECASE,
)
_BOILER = re.compile(
    r"sponsor|newsletter|patreon|subscribe|promo code|discount|"
    r"armchair documentaries|retail website|viewing experience|instagram",
    re.IGNORECASE,
)
# Lowercase words that may sit *between* two names ("El Mencho", "of Sweden").
_CONNECTORS = {
    "de", "del", "la", "las", "los", "el", "of", "and", "y", "e",
    "van", "von", "da", "dos", "der", "di", "san", "santa",
}
# Filler that must never become a glossary term, and that a single-word window
# may never be rewritten into. Expanded from the words that caused the first,
# over-eager pass to rewrite ordinary text.
_STOP = {
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "been", "before", "behind", "being", "below", "between", "book",
    "brand", "but", "by", "can", "card", "cartel", "cartels", "could", "criminal",
    "deadliest", "did", "do", "does", "drug", "during", "each", "empire", "every",
    "experience", "fears", "for", "from", "global", "growing", "guide", "had",
    "has", "have", "he", "heart", "help", "her", "here", "his", "how", "in",
    "into", "is", "it", "its", "join", "less", "like", "link", "list", "lord",
    "many", "may", "mexican", "american", "more", "most", "much", "music", "new",
    "no", "not", "now", "of", "on", "only", "or", "other", "our", "out", "over",
    "own", "people", "plan", "powerful", "project", "quick", "retail", "ruthless",
    "safety", "said", "same", "secret", "she", "should", "since", "some", "sound",
    "sources", "special", "start", "state", "story", "such", "support", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "thousands", "through", "to", "under", "until", "very", "video",
    "was", "watch", "website", "were", "what", "when", "where", "which", "while",
    "who", "why", "will", "with", "world", "would", "your",
}
# Common suffixes that mark a different form of the same word, not an ASR error.
_SUFFIXES = ("'s", "s", "es", "ly", "ing", "ed")


@dataclass(frozen=True)
class Edit:
    """One replacement, with the character span it occupies in the source."""

    source: str
    target: str
    method: str
    score: float
    start: int
    end: int

    def as_dict(self) -> dict:
        return asdict(self)


def _norm(text: str) -> str:
    """Casefold, strip accents, and reduce to space-joined letter tokens."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(_WORD.findall(folded))


def _collapse(normalized: str) -> str:
    return normalized.replace(" ", "")


def _similar(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def _stem(collapsed: str) -> str:
    """Strip a common inflection so plural/possessive/adverb forms are equal."""
    for suffix in _SUFFIXES:
        if collapsed.endswith(suffix) and len(collapsed) - len(suffix) >= 3:
            return collapsed[: -len(suffix)]
    return collapsed


def _common_prefix(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _is_name(token: str) -> bool:
    return token[:1].isupper() or (len(token) >= 2 and token.isupper())


def _trim_connectors(parts: list[str]) -> list[str]:
    """Drop lowercase connector words from the ends of a name run."""
    while parts and parts[0].islower() and parts[0].lower() in _CONNECTORS:
        parts = parts[1:]
    while parts and parts[-1].islower() and parts[-1].lower() in _CONNECTORS:
        parts = parts[:-1]
    return parts


def clean_metadata(description: str | None) -> str:
    """Drop the sponsor and music boilerplate from a stored description.

    Keeps prose lines, stops at the first music/source heading. A Townsends
    description is nothing but links, so this collapses to the empty string —
    which is the correct glossary for those videos.
    """
    if not description:
        return ""
    kept: list[str] = []
    for line in description.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _HEADING.match(stripped):
            break
        if _URL.search(stripped) or _EMAIL.search(stripped):
            continue
        if _BOILER.search(stripped):
            continue
        if set(stripped) <= set("_-— "):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _name_runs(tokens: list[str]) -> list[list[str]]:
    """Group tokens into runs of names joined by lowercase connectors."""
    runs: list[list[str]] = []
    index, count = 0, len(tokens)
    while index < count:
        if not _is_name(tokens[index]):
            index += 1
            continue
        parts = [tokens[index]]
        cursor = index + 1
        while cursor < count:
            if _is_name(tokens[cursor]):
                parts.append(tokens[cursor])
                cursor += 1
            elif (
                tokens[cursor].lower() in _CONNECTORS
                and cursor + 1 < count
                and _is_name(tokens[cursor + 1])
            ):
                parts.append(tokens[cursor])
                cursor += 1
            else:
                break
        runs.append(parts)
        index = cursor
    return runs


def build_glossary(metadata: dict) -> list[str]:
    """Candidate spellings from the video's own metadata, deduped.

    Acronyms anywhere, multiword name runs (no filler inside) from the title and
    cleaned description, and single title words of five letters or more. Single
    words from the description are deliberately excluded: that was the source of
    the ``Cartel``/``Quick``/``Mexican`` noise.
    """
    title = str(metadata.get("title") or "")
    channel = str(metadata.get("channel_name") or "")
    description = clean_metadata(metadata.get("description"))

    candidates: list[str] = []

    for text in (title, channel, description):
        for token in _WORD.findall(text):
            if len(token) >= 2 and token.isupper():
                candidates.append(token)

    for text in (title, description):
        for run in _name_runs(_WORD.findall(text)):
            parts = _trim_connectors(run)
            for size in (2, 3):
                for start in range(0, len(parts) - size + 1):
                    window = parts[start:start + size]
                    if any(
                        token.lower() not in _CONNECTORS and token.lower() in _STOP
                        for token in window
                    ):
                        continue
                    candidates.append(" ".join(window))

    for token in _WORD.findall(title):
        if _is_name(token) and len(token) >= 5 and token.lower() not in _STOP:
            candidates.append(token)

    glossary: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _norm(candidate)
        if len(_collapse(key)) < MIN_CHARS or key in seen:
            continue
        seen.add(key)
        glossary.append(candidate)
    return glossary


def edit_summary(edits: list[Edit]) -> list[dict]:
    """Aggregate edits into the ``source -> target × count`` shape callers report."""
    counts: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for edit in edits:
        key = (edit.source, edit.target)
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    return [
        {"source": source, "target": target, "count": counts[(source, target)]}
        for source, target in order
    ]


def normalize(text: str, metadata: dict) -> tuple[str, list[Edit]]:
    """Return ``(clean_text, edits)`` with no external knowledge involved."""
    if not text:
        return text, []
    glossary = build_glossary(metadata)
    if not glossary:
        return text, []

    # (collapsed, words, is_acronym, surface)
    terms: list[tuple[str, list[str], bool, str]] = []
    for surface in glossary:
        words = _norm(surface).split()
        if not words:
            continue
        acronym = surface.replace(" ", "").isupper()
        terms.append((_collapse(" ".join(words)), words, acronym, surface))

    tokens = [(match.group(0), match.start(), match.end()) for match in _WORD.finditer(text)]
    if not tokens:
        return text, []
    norms = [_norm(token) for token, _, _ in tokens]

    candidates: list[tuple[float, int, int, str]] = []
    count = len(tokens)
    for size in range(1, MAX_WINDOW + 1):
        for start in range(0, count - size + 1):
            words = norms[start:start + size]
            if not _is_name(tokens[start][0]):
                # Only proper nouns are repaired; a lowercase word is the
                # transcript's own vocabulary, not a mangled name.
                continue
            if size == 1 and words[0] in _STOP:
                continue
            window = "".join(words)
            if len(window) < MIN_CHARS:
                continue

            best: tuple[float, str] | None = None
            second: tuple[float, str] | None = None
            for term, term_words, acronym, surface in terms:
                if acronym or window == term:
                    # Acronyms are matched exactly, never fuzzily; an exact
                    # normalized form needs no rewrite.
                    continue
                if size > len(term_words):
                    # A window may not swallow words the name does not have.
                    continue
                if _stem(window) == _stem(term):
                    # Same lexeme, different inflection: the transcript is fine.
                    continue
                if size == len(term_words):
                    if size == 1:
                        score = _similar(words[0], term_words[0])
                        if score < SINGLE_THRESHOLD:
                            continue
                    else:
                        if words[:-1] != term_words[:-1]:
                            continue
                        score = _similar(words[-1], term_words[-1])
                        if score < LAST_THRESHOLD:
                            continue
                else:
                    # Fused or partial token ("Elmeno" for "El Mencho").
                    if window == "".join(term_words[:size]):
                        continue  # the term merely extends the window's words
                    score = _common_prefix(window, term) / max(len(window), len(term))
                    if score < PREFIX_THRESHOLD:
                        continue

                if best is None or score > best[0]:
                    second, best = best, (score, surface)
                elif second is None or score > second[0]:
                    second = (score, surface)
            if best is None:
                continue
            if second is not None and best[0] - second[0] < EPSILON:
                continue  # two plausible names: leave it alone
            candidates.append((best[0], size, start, best[1]))

    if not candidates:
        return text, []

    # Greedy, best-first, non-overlapping. Longer and earlier win ties.
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    chosen: list[tuple[int, int, str, float]] = []
    used: set[int] = set()
    for score, size, start, target in candidates:
        span = set(range(start, start + size))
        if span & used:
            continue
        source = text[tokens[start][1]:tokens[start + size - 1][2]]
        if source == target:
            continue
        chosen.append((start, size, target, score))
        used |= span
        if len(chosen) >= MAX_EDITS:
            break

    if not chosen:
        return text, []

    chosen.sort(key=lambda item: item[0])
    pieces: list[str] = []
    edits: list[Edit] = []
    cursor = 0
    for start, size, target, score in chosen:
        begin = tokens[start][1]
        end = tokens[start + size - 1][2]
        source = text[begin:end]
        pieces.append(text[cursor:begin])
        pieces.append(target)
        cursor = end
        edits.append(Edit(source, target, "prefix", round(score, 3), begin, end))
    pieces.append(text[cursor:])
    return "".join(pieces), edits


# ---------------------------------------------------------------------------
# CLI — read-only inspection against the corpus
# ---------------------------------------------------------------------------


def _load_video(conn: sqlite3.Connection, video_id: str) -> tuple[dict, str] | None:
    row = conn.execute(
        """SELECT v.title, v.channel_name, v.description, t.text
           FROM transcripts t JOIN videos v ON v.video_id = t.video_id
           WHERE t.video_id = ? AND t.text IS NOT NULL""",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    metadata = {"title": row[0], "channel_name": row[1], "description": row[2]}
    return metadata, row[3]


def main(argv: list[str] | None = None) -> int:
    from tessera import store

    parser = argparse.ArgumentParser(prog="normalize", description=__doc__.splitlines()[0])
    parser.add_argument("video_id", help="a transcript in tessera.db")
    args = parser.parse_args(argv)

    conn = store.connect()
    try:
        loaded = _load_video(conn, args.video_id)
    finally:
        conn.close()
    if loaded is None:
        print(f"error: no stored transcript for {args.video_id!r}", file=sys.stderr)
        return 1

    metadata, text = loaded
    glossary = build_glossary(metadata)
    clean, edits = normalize(text, metadata)
    print(f"glossary: {len(glossary)} term(s)")
    for term in glossary:
        print(f"  {term}")
    print(f"\ntext: {len(text):,} -> {len(clean):,} chars; {len(edits)} edit(s)")
    for item in edit_summary(edits):
        print(f"  {item['source']} -> {item['target']}  x{item['count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
