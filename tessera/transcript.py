"""Fetch one video's transcript, classify failures, clean the text.

Language rule: match a caption track to the video's original audio language.
YouTube can expose auto-generated caption tracks in several languages, so neither
the first track nor the `is_generated` flag identifies the source-language track.
We use the default/original audio track metadata and never translate a caption.

Failure classification is load-bearing. The first three statuses below are
permanent and the rest are retried; that split is literally the WHERE clause
for the retry path in store.py. Without it a retry pass re-hammers permanently
captionless videos forever.

`rate_limited` is kept apart from `blocked` because the two deserve different
handling: HTTP 429s and IP-level CAPTCHA blocks stop the run outright, while a
video-level bot-check challenge ends the fetch loop but still allows local
summary work to finish.
"""

from __future__ import annotations

import base64
import math
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
    TranscriptList,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeTranscriptApi,
)

OK = "ok"
NO_CAPTIONS = "no_captions"
DISABLED = "disabled"
UNAVAILABLE = "unavailable"
BLOCKED = "blocked"
#: HTTP 429 or an IP-level CAPTCHA block. Retried later, and the run that sees
#: it stops immediately rather than continuing to feed the quota.
RATE_LIMITED = "rate_limited"
ERROR = "error"

#: Permanent. Retrying cannot change the outcome.
PERMANENT_STATUSES = frozenset({NO_CAPTIONS, DISABLED, UNAVAILABLE})
#: Transient. Worth another attempt on an ordinary run.
RETRYABLE_STATUSES = frozenset({BLOCKED, RATE_LIMITED, ERROR})
ALL_STATUSES = PERMANENT_STATUSES | RETRYABLE_STATUSES | {OK}

# Exception -> status. Ordered most-specific first.
#
# `IpBlocked` is a subclass of `RequestBlocked` and is what the library raises
# for HTTP 429s and some IP-level CAPTCHA blocks, so match it first.
_CLASSIFICATION: tuple[tuple[type[Exception], str], ...] = (
    (TranscriptsDisabled, DISABLED),
    (NoTranscriptFound, NO_CAPTIONS),
    (AgeRestricted, UNAVAILABLE),
    (VideoUnavailable, UNAVAILABLE),
    (VideoUnplayable, UNAVAILABLE),
    (InvalidVideoId, UNAVAILABLE),
    (IpBlocked, RATE_LIMITED),
    (RequestBlocked, BLOCKED),
    (PoTokenRequired, BLOCKED),
)

# The library's blocked/banned messages are seven lines of prose beginning
# "Could not retrieve a transcript for the video https://…", identical for every
# video and carrying the actual cause only at the end. Store the cause instead —
# the `error` column is a reason, not an essay.
_REASONS = {
    RATE_LIMITED: "YouTube returned HTTP 429 or an IP-level CAPTCHA block",
    BLOCKED: "YouTube challenged the request (bot check)",
    DISABLED: "captions disabled by the author",
    NO_CAPTIONS: "no caption tracks on this video",
    UNAVAILABLE: "video private, removed, or age/region restricted",
}


@dataclass(frozen=True)
class TranscriptResult:
    """Outcome of one transcript attempt."""

    status: str
    lang: str | None = None
    kind: str | None = None
    text: str | None = None
    error: str | None = None
    cues: tuple[TranscriptCue, ...] = ()


@dataclass(frozen=True)
class TranscriptCue:
    """Timing and Unicode character span for one cleaned caption cue."""

    cue_index: int
    start_ms: int
    duration_ms: int
    char_start: int
    char_end: int


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


def clean_captions_with_cues(snippets) -> tuple[str, tuple[TranscriptCue, ...]]:
    """Clean captions exactly as before and add offsets when they remain exact.

    Cleanup is also applied per cue to calculate spans. If that changes the
    historical flattened result (for example, noise split across cues), retain
    the transcript but return no offsets rather than persisting bad positions.
    Offsets are Python Unicode code-point positions and end-exclusive.
    """
    snippets = list(snippets)
    text = clean_captions("\n".join(snippet.text for snippet in snippets))
    parts: list[tuple[int, str, int, int]] = []
    for index, snippet in enumerate(snippets):
        part = clean_captions(snippet.text)
        if not part:
            continue
        start, duration = getattr(snippet, "start", None), getattr(snippet, "duration", None)
        if start is None or duration is None:
            return text, ()
        try:
            start_seconds, duration_seconds = float(start), float(duration)
        except (TypeError, ValueError):
            return text, ()
        if (
            not math.isfinite(start_seconds)
            or not math.isfinite(duration_seconds)
            or start_seconds < 0
            or duration_seconds < 0
        ):
            return text, ()
        parts.append((index, part, round(start_seconds * 1000), round(duration_seconds * 1000)))

    flattened = " ".join(part for _, part, _, _ in parts)
    if flattened != text:
        return text, ()

    cues: list[TranscriptCue] = []
    offset = 0
    for index, part, start_ms, duration_ms in parts:
        if cues:
            offset += 1  # the single separating space is not part of either cue
        end = offset + len(part)
        cues.append(TranscriptCue(index, start_ms, duration_ms, offset, end))
        offset = end
    return text, tuple(cues)


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


def _list_transcripts_with_player_response(api, video_id: str):
    """Get the caption list and player audio metadata in one YouTube lookup.

    youtube-transcript-api's public ``list()`` discards the rest of the player
    response, including the original audio track. Its 1.2.x fetcher already
    obtains that response, so use that same flow and build its normal TranscriptList
    rather than making an additional watch-page request. This is intentionally
    isolated because the library's fetcher methods are private.
    """
    fetcher = api._fetcher
    html = fetcher._fetch_video_html(video_id)
    api_key = fetcher._extract_innertube_api_key(html, video_id)
    player_response = fetcher._fetch_innertube_data(video_id, api_key)
    captions = fetcher._extract_captions_json(player_response, video_id)
    transcript_list = TranscriptList.build(
        fetcher._http_client, video_id, captions
    )
    return transcript_list, player_response


def _original_audio_language(player_response: dict, transcript_list=None) -> str:
    """Return the BCP-47 language of YouTube's original audio track.

    ``audioIsDefault`` means the currently selected playback track, not necessarily
    the original: YouTube may default to an auto-dub in the viewer's language.
    Prefer explicit ``original`` markers and exclude ``isAutoDubbed`` tracks.
    Audio formats repeat tracks at several qualities, so deduplicate them. Some
    single-audio videos omit audioTrack metadata entirely; only in that case, a
    sole YouTube ASR caption track attached to the sole audio group is a safe
    fallback. Multiple possible languages still fail closed.
    """
    formats = player_response.get("streamingData", {}).get("adaptiveFormats", [])
    explicitly_original: set[str] = set()
    non_dubbed: set[str] = set()
    for format_data in formats:
        audio_track = format_data.get("audioTrack", {})
        audio_track_id = audio_track.get("id")
        if not audio_track_id:
            continue

        language = audio_track_id.split(".", 1)[0]
        display_name = str(audio_track.get("displayName", "")).casefold()
        try:
            tags = base64.b64decode(format_data.get("xtags", "")).decode(
                "utf-8", errors="ignore"
            ).casefold()
        except (ValueError, TypeError):
            tags = ""

        is_original = "original" in display_name or "original" in tags
        is_dubbed = audio_track.get("isAutoDubbed") is True or "dubbed-auto" in tags
        if is_original and not is_dubbed:
            explicitly_original.add(language)
        if not is_dubbed:
            non_dubbed.add(language)

    # Explicit source markers beat playback defaults. If older player responses
    # omit them, accept a sole non-dubbed audio language; never choose a dub.
    languages = explicitly_original or non_dubbed
    by_primary_language = {
        language.split("-", 1)[0].casefold(): language for language in languages
    }
    if not by_primary_language:
        # YouTube omits `audioTrack` from adaptive formats for some ordinary
        # single-audio videos. In that narrow case, the only ASR track belongs
        # to the only audio group and reports the language detected from that
        # audio. Do not use a manual caption or guess when there are alternatives.
        renderer = (
            player_response.get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
        )
        caption_tracks = renderer.get("captionTracks", [])
        audio_tracks = renderer.get("audioTracks", [])
        available_tracks = list(transcript_list) if transcript_list is not None else []
        if (
            transcript_list is not None
            and len(available_tracks) == 1
            and len(caption_tracks) == 1
            and len(audio_tracks) == 1
            and caption_tracks[0].get("kind") == "asr"
            and audio_tracks[0].get("captionTrackIndices") == [0]
            and not audio_tracks[0].get("audioTrackId")
        ):
            language = caption_tracks[0].get("languageCode")
            if language and available_tracks[0].language_code.casefold() == language.casefold():
                return language

    if len(by_primary_language) != 1:
        raise ValueError(
            "could not determine one original audio language from YouTube metadata"
        )
    return next(iter(by_primary_language.values()))


def _pick_track_for_audio(transcript_list, audio_language: str) -> object | None:
    """Pick a caption in the original audio language, preferring manual tracks.

    Match the primary language subtag because YouTube commonly labels the audio
    ``en-US`` while its caption track is just ``en``. Never fall back to another
    language: a mismatch must be visible rather than summarized as if it matched.
    """
    audio_primary = audio_language.split("-", 1)[0].casefold()
    candidates = [
        track
        for track in transcript_list
        if track.language_code.split("-", 1)[0].casefold() == audio_primary
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda track: (
            bool(track.is_generated),
            track.language_code.casefold() != audio_language.casefold(),
        ),
    )


def fetch_transcript(video_id: str) -> TranscriptResult:
    """Fetch, clean and classify the native caption track for one video.

    Never raises: every failure comes back as a TranscriptResult with a status,
    because a single bad video must not end a run over a hundred good ones.
    """
    api = YouTubeTranscriptApi()

    try:
        transcript_list, player_response = _list_transcripts_with_player_response(
            api, video_id
        )
        audio_language = _original_audio_language(player_response, transcript_list)
        track = _pick_track_for_audio(transcript_list, audio_language)
        if track is None:
            return TranscriptResult(
                ERROR,
                error=f"no caption track matches original audio language {audio_language!r}",
            )
        fetched = track.fetch()
    except Exception as exc:
        status = classify(exc)
        return TranscriptResult(status, error=describe(exc, status))

    # Outside the try so a failure here is unambiguously our bug, per the schema
    # note on `error`.
    text, cues = clean_captions_with_cues(fetched)
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
        cues=cues,
    )
