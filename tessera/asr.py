"""Local audio transcription fallback using yt-dlp and MLX Whisper.

This is intentionally an optional worker: a normal install stays lightweight,
and missing ASR dependencies leave the normal YouTube-caption result unchanged.
Only audio is downloaded; recognition and transcript cleanup run locally.
"""

from __future__ import annotations

import importlib
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from . import config
from .transcript import ERROR, OK, TranscriptResult, clean_captions_with_cues

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class AudioDownload:
    """Downloaded audio plus the metadata yt-dlp discovered for its track."""

    path: Path
    language: str | None
    duration_seconds: int | None


def _whisper_language(language: str | None) -> str | None:
    """Convert a BCP-47 audio tag to the language code Whisper expects."""
    if not isinstance(language, str) or not language.strip():
        return None
    language = language.strip().replace("_", "-")
    primary = language.split("-", 1)[0].casefold()
    # Whisper accepts ISO language codes or English language names. Keep the
    # latter intact while reducing tags such as `fr-FR` and `en-US` to `fr`/`en`.
    return primary if re.fullmatch(r"[a-z]{2,3}", primary) else language


def _audio_language(info: dict) -> str | None:
    """Prefer the selected audio format's language over container metadata.

    M4A container tags can misleadingly say `eng` for a French audio stream.
    yt-dlp's requested format metadata is the language of the actual track.
    """
    requested = info.get("requested_downloads") or []
    for download in requested:
        language = download.get("language")
        if isinstance(language, str) and language.strip():
            return language.strip()
    language = info.get("language")
    return language.strip() if isinstance(language, str) and language.strip() else None


def _duration_seconds(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 < value <= 2**63 - 1:
        return None
    return int(round(value))


def _duration_filter(info: dict, *, incomplete: bool = False) -> str | None:
    """Reject long videos before yt-dlp starts downloading their audio."""
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > config.ASR_MAX_DURATION_SECONDS:
        return f"video duration exceeds {config.ASR_MAX_DURATION_SECONDS} seconds"
    return None


def _download_audio(video_id: str, directory: Path) -> AudioDownload:
    """Download one bounded audio stream and preserve its source metadata."""
    yt_dlp = importlib.import_module("yt_dlp")
    options = {
        "format": "bestaudio/best",
        "outtmpl": str(directory / "audio.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "cachedir": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "max_filesize": config.ASR_MAX_AUDIO_BYTES,
        "match_filter": _duration_filter,
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        filename = downloader.prepare_filename(info)

    path = Path(filename)
    if not path.is_file():
        candidates = [item for item in directory.glob("audio.*") if item.is_file()]
        if len(candidates) != 1:
            raise RuntimeError("yt-dlp did not produce exactly one audio file")
        path = candidates[0]
    if path.stat().st_size > config.ASR_MAX_AUDIO_BYTES:
        raise RuntimeError("downloaded audio exceeds the configured size limit")
    return AudioDownload(
        path=path,
        language=_audio_language(info),
        duration_seconds=_duration_seconds(info.get("duration")),
    )


def _recognize(audio_path: Path, language: str | None = None) -> dict:
    """Run MLX Whisper with a source-language hint when one is available."""
    mlx_whisper = importlib.import_module("mlx_whisper")
    options = {
        "path_or_hf_repo": config.ASR_MODEL,
        "verbose": False,
        # Avoid carrying a mistaken guess from music/silence into later chunks.
        "condition_on_previous_text": False,
    }
    if language:
        options["language"] = language
    return mlx_whisper.transcribe(
        str(audio_path),
        **options,
    )


def transcribe_video(
    video_id: str,
    *,
    language_hint: str | None = None,
) -> TranscriptResult | None:
    """Transcribe a video locally; return None only when optional packages are absent.

    All worker failures become ordinary retryable transcript errors. The caller
    can therefore retain them in the corpus without allowing one broken download
    or model invocation to interrupt a fetch run.
    """
    if not _VIDEO_ID.fullmatch(video_id):
        return TranscriptResult(ERROR, error="invalid YouTube video id for ASR")

    try:
        importlib.import_module("yt_dlp")
        importlib.import_module("mlx_whisper")
    except ImportError:
        return None

    audio: AudioDownload | None = None
    try:
        print("            local ASR · downloading audio", file=sys.stderr, flush=True)
        with tempfile.TemporaryDirectory(prefix="tessera-asr-") as temporary:
            audio = _download_audio(video_id, Path(temporary))
            source_language = _whisper_language(language_hint or audio.language)
            mode = source_language or "auto-detect"
            duration = (
                f", {audio.duration_seconds // 60}m{audio.duration_seconds % 60:02d}s"
                if audio.duration_seconds
                else ""
            )
            print(
                f"            local ASR · Whisper ({mode}{duration})",
                file=sys.stderr,
                flush=True,
            )
            recognized = _recognize(audio.path, source_language)

        lang = recognized.get("language") or source_language
        segments = recognized.get("segments") or []
        snippets = []
        for segment in segments:
            text = segment.get("text")
            start, end = segment.get("start"), segment.get("end")
            if not isinstance(text, str) or start is None or end is None:
                continue
            snippets.append(
                SimpleNamespace(text=text, start=start, duration=float(end) - float(start))
            )
        text, cues = clean_captions_with_cues(snippets)
        if not isinstance(lang, str) or not lang or not text:
            raise RuntimeError("Whisper returned no language or transcript text")
        return TranscriptResult(
            OK,
            lang=lang,
            kind="asr",
            text=text,
            cues=cues,
            duration_seconds=audio.duration_seconds,
        )
    except Exception as exc:
        message = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")
        detail = f": {message[:240]}" if message else ""
        return TranscriptResult(
            ERROR,
            error=f"local ASR failed ({type(exc).__name__}){detail}",
            duration_seconds=audio.duration_seconds if audio else None,
        )
