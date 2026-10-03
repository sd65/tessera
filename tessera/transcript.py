"""Fetch one video's transcript, classify failures, clean the text.

Language rule: take the video's *native* caption track. No language list, no
fr/en preference, no translation. youtube-transcript-api's TranscriptList keeps
native tracks and auto-translation targets in separate collections — only the
native ones are ever candidates here, and `translate()` is never called, so an
auto-translated track is structurally unreachable rather than merely filtered
out.

Failure classification is load-bearing. The first three statuses below are
permanent and the last two are retried; that split is literally the WHERE clause
for the retry path in store.py. Without it a retry pass re-hammers permanently
captionless videos forever.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from youtube_transcript_api import (
    AgeRestricted,
    InvalidVideoId,
    IpBlocked,
    NoTranscriptFound,
    PoTokenRequired,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)

OK = "ok"
NO_CAPTIONS = "no_captions"
DISABLED = "disabled"
UNAVAILABLE = "unavailable"
BLOCKED = "blocked"
ERROR = "error"

#: Permanent. Retrying cannot change the outcome.
PERMANENT_STATUSES = frozenset({NO_CAPTIONS, DISABLED, UNAVAILABLE})
#: Transient. Worth another attempt on an ordinary run.
RETRYABLE_STATUSES = frozenset({BLOCKED, ERROR})
ALL_STATUSES = PERMANENT_STATUSES | RETRYABLE_STATUSES | {OK}

# Exception -> status. Ordered most-specific first.
_CLASSIFICATION: tuple[tuple[type[Exception], str], ...] = (
    (TranscriptsDisabled, DISABLED),
    (NoTranscriptFound, NO_CAPTIONS),
    (AgeRestricted, UNAVAILABLE),
    (VideoUnavailable, UNAVAILABLE),
    (VideoUnplayable, UNAVAILABLE),
    (InvalidVideoId, UNAVAILABLE),
    (IpBlocked, BLOCKED),
    (RequestBlocked, BLOCKED),
    (PoTokenRequired, BLOCKED),
)

# The library's blocked/banned messages are seven lines of prose beginning
# "Could not retrieve a transcript for the video https://…", identical for every
# video and carrying the actual cause only at the end. Store the cause instead —
# the `error` column is a reason, not an essay.
_REASONS = {
    BLOCKED: "YouTube blocked the request (IP rate limit or bot check)",
    DISABLED: "captions disabled by the author",
    NO_CAPTIONS: "no caption tracks on this video",
    UNAVAILABLE: "video private, removed, or age/region restricted",
    ERROR: "unexpected failure",
}


@dataclass(frozen=True)
class TranscriptResult:
    """Outcome of one transcript attempt."""

    status: str
    lang: str | None = None
    kind: str | None = None
    text: str | None = None
    error: str | None = None


# --------------------------------------------------------------------------
# Caption cleanup
# --------------------------------------------------------------------------

# Inline noise YouTube mixes into the caption text itself. Three alternatives:
#   1. the bare `>>` it drops at speaker and segment changes (left in, the text
#      reads "use it for >> >> defense");
#   2. bracketed non-speech cues, which YouTube emits as [Music] or [music];
#   3. any other short ALL-CAPS bracketed span, e.g. [BLUES MUSIC].
_NOISE = re.compile(
    r"\s*>>\s*"
    r"|\[\s*(?:music|applause|clapping|cheering|cheers|laughter|laugh|laughing|laughs"
    r"|silence|inaudible|muffled|no audio|static|gunshot|sighs?|coughing|noise"
    r"|beeping|alarm|background (?:music|noise|applause))[\s.,!]*\]"
    r"|\[\s*[A-Z][A-Z\s.\!\-]{1,30}\]",
    re.IGNORECASE,
)

_SPACES = re.compile(r"\s+")


def clean_captions(raw: str) -> str:
    """Turn raw caption snippets into readable prose.

    Pure string processing, no model, no dependencies.

    YouTube serves captions one phrase per line with no rolling overlap, so
    joining on whitespace is all the line handling needed — no dedup pass. The
    only real mess is the noise mixed into the text itself, which is one regex.
    Manual captions are usually clean already and get the same pass for safety.
    """
    return _SPACES.sub(" ", _NOISE.sub(" ", raw)).strip()


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def classify(exc: Exception) -> str:
    """Map a youtube-transcript-api exception onto one of our statuses."""
    for exc_type, status in _CLASSIFICATION:
        if isinstance(exc, exc_type):
            return status
    # Anything unrecognised is a retryable `error` — including a bug in the
    # cleanup code above, which is exactly the case you want retried and, failing
    # that, visible in `status` rather than silently stored as permanent.
    return ERROR


def describe(exc: Exception, status: str) -> str:
    """A short, stored reason. Keeps the exception class, drops the prose.

    For an unknown error the exception's own first non-empty line is the most
    informative thing available, so use it.
    """
    reason = _REASONS.get(status)
    if reason:
        return f"{type(exc).__name__}: {reason}"
    first = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")
    return f"{type(exc).__name__}: {first}" if first else type(exc).__name__


def _pick_native_track(transcript_list) -> object | None:
    """Manual captions first, then auto — never a translation.

    Both dicts hold only native tracks (auto-translations live in a separate
    list of possible targets), and dict order follows YouTube's own caption track
    order, so the first entry is the track YouTube considers primary.
    """
    for transcripts in (transcript_list._manually_created_transcripts, transcript_list._generated_transcripts):
        if transcripts:
            return next(iter(transcripts.values()))
    return None


def fetch_transcript(video_id: str) -> TranscriptResult:
    """Fetch, clean and classify the native caption track for one video.

    Never raises: every failure comes back as a TranscriptResult with a status,
    because a single bad video must not end a run over a hundred good ones.
    """
    api = YouTubeTranscriptApi()

    try:
        track = _pick_native_track(api.list(video_id))
        if track is None:
            return TranscriptResult(NO_CAPTIONS, error="video has no caption tracks")
        fetched = track.fetch()
    except Exception as exc:
        status = classify(exc)
        return TranscriptResult(status, error=describe(exc, status))

    # Outside the try so a failure here is unambiguously our bug, per the schema
    # note on `error`.
    text = clean_captions("\n".join(snippet.text for snippet in fetched))
    if not text:
        return TranscriptResult(
            ERROR,
            lang=track.language_code,
            kind="auto" if track.is_generated else "manual",
            error="caption track fetched but produced no text after cleanup",
        )

    return TranscriptResult(
        OK,
        lang=track.language_code,
        kind="auto" if track.is_generated else "manual",
        text=text,
    )