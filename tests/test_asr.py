"""Tests for the optional local speech-recognition worker (no downloads/models)."""

from __future__ import annotations

import sys
from support import Suite
from tessera import asr, transcript

suite = Suite()
case = suite.case


@case
def local_worker_returns_language_text_and_timestamped_cues():
    original_import = asr.importlib.import_module
    original_download = asr._download_audio
    original_recognize = asr._recognize
    imported = []

    def fake_import(name):
        imported.append(name)
        return object()

    asr.importlib.import_module = fake_import
    asr._download_audio = lambda video_id, directory: asr.AudioDownload(
        directory / "audio.m4a", language="en-US", duration_seconds=42
    )
    recognized_languages = []

    def recognize(audio_path, language=None):
        recognized_languages.append(language)
        return {
            "language": "en",
            "segments": [
                {"start": 0.25, "end": 1.5, "text": " First phrase."},
                {"start": 1.5, "end": 2.0, "text": "Second phrase."},
            ],
        }

    asr._recognize = recognize
    try:
        result = asr.transcribe_video("XFsbtGB1TXQ")
    finally:
        asr.importlib.import_module = original_import
        asr._download_audio = original_download
        asr._recognize = original_recognize

    assert imported == ["yt_dlp", "mlx_whisper"]
    assert result.status == transcript.OK
    assert result.lang == "en"
    assert result.kind == "asr"
    assert result.duration_seconds == 42
    assert recognized_languages == ["en"]
    assert result.text == "First phrase. Second phrase."
    assert [(cue.start_ms, cue.duration_ms) for cue in result.cues] == [
        (250, 1250), (1500, 500),
    ]
    assert result.cues[1].char_start == len("First phrase. ")


@case
def missing_optional_asr_packages_do_not_break_caption_fetches():
    original_import = asr.importlib.import_module

    def missing(name):
        if name == "yt_dlp":
            raise ImportError(name)
        return original_import(name)

    asr.importlib.import_module = missing
    try:
        assert asr.transcribe_video("XFsbtGB1TXQ") is None
    finally:
        asr.importlib.import_module = original_import


@case
def worker_errors_are_returned_as_retryable_results():
    original_import = asr.importlib.import_module
    original_download = asr._download_audio

    asr.importlib.import_module = lambda name: object()
    asr._download_audio = lambda video_id, directory: (_ for _ in ()).throw(
        RuntimeError("temporary download problem")
    )
    try:
        result = asr.transcribe_video("XFsbtGB1TXQ")
    finally:
        asr.importlib.import_module = original_import
        asr._download_audio = original_download

    assert result.status == "error"
    assert "local ASR failed" in result.error
    assert "temporary download problem" in result.error


@case
def youtube_audio_track_metadata_supplies_whisper_language_and_duration():
    info = {
        "language": "en",
        "duration": 125.4,
        "requested_downloads": [{"language": "fr-FR"}],
    }
    assert asr._audio_language(info) == "fr-FR"
    assert asr._duration_seconds(info["duration"]) == 125
    assert asr._whisper_language(asr._audio_language(info)) == "fr"
    assert asr._whisper_language("en-US") == "en"
    assert asr._whisper_language(None) is None


@case
def invalid_or_missing_duration_is_not_stored():
    assert asr._duration_seconds(None) is None
    assert asr._duration_seconds(True) is None
    assert asr._duration_seconds(0) is None


if __name__ == "__main__":
    sys.exit(suite.run())
