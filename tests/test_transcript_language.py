"""Transcript selection follows YouTube's original audio language.

Run with ``uv run python tests/test_transcript_language.py``. All YouTube
responses and transcript tracks are synthetic; these tests make no requests.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from support import Suite
from tessera import transcript

suite = Suite()
case = suite.case


class FakeTrack:
    def __init__(self, language_code: str, *, generated: bool, text: str):
        self.language_code = language_code
        self.is_generated = generated
        self.text = text

    def fetch(self):
        return [SimpleNamespace(text=self.text, start=0.25, duration=1.0)]


@case
def original_audio_language_comes_from_default_audio_formats():
    response = {
        "streamingData": {
            "adaptiveFormats": [
                {
                    "audioTrack": {
                        "id": "en-US.10",
                        "audioIsDefault": True,
                        "isAutoDubbed": True,
                    }
                },
                {
                    "audioTrack": {
                        "id": "fr-FR.4",
                        "audioIsDefault": False,
                        "displayName": "French (FR) original",
                    }
                },
                {
                    "audioTrack": {
                        "id": "fr-FR.4",
                        "audioIsDefault": False,
                        "displayName": "French (FR) original",
                    }
                },
            ]
        }
    }
    assert transcript._original_audio_language(response) == "fr-FR"


@case
def default_playback_dub_is_not_mistaken_for_original_audio():
    response = {
        "streamingData": {
            "adaptiveFormats": [
                {
                    "audioTrack": {
                        "id": "en-US.10",
                        "audioIsDefault": True,
                        "isAutoDubbed": True,
                    }
                },
                {"audioTrack": {"id": "fr-FR.4", "audioIsDefault": False}},
            ]
        }
    }
    assert transcript._original_audio_language(response) == "fr-FR"


@case
def original_audio_language_fails_closed_when_metadata_is_ambiguous():
    response = {
        "streamingData": {
            "adaptiveFormats": [
                {
                    "audioTrack": {
                        "id": "en-US.4",
                        "audioIsDefault": True,
                        "displayName": "English original",
                    }
                },
                {
                    "audioTrack": {
                        "id": "fr-FR.4",
                        "audioIsDefault": True,
                        "displayName": "French original",
                    }
                },
            ]
        }
    }
    try:
        transcript._original_audio_language(response)
    except ValueError as exc:
        assert "could not determine" in str(exc)
    else:
        raise AssertionError("ambiguous source audio must not be guessed")


@case
def sole_asr_caption_is_used_when_youtube_omits_single_audio_metadata():
    track = FakeTrack("en", generated=True, text="A single source caption.")
    player_response = {
        "streamingData": {"adaptiveFormats": []},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [{"languageCode": "en", "kind": "asr"}],
                "audioTracks": [{"captionTrackIndices": [0]}],
            }
        },
    }
    assert transcript._original_audio_language(player_response, [track]) == "en"


@case
def single_audio_fallback_does_not_guess_between_caption_languages():
    tracks = [
        FakeTrack("en", generated=True, text="English."),
        FakeTrack("fr", generated=True, text="Français."),
    ]
    player_response = {
        "streamingData": {"adaptiveFormats": []},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr"},
                    {"languageCode": "fr", "kind": "asr"},
                ],
                "audioTracks": [{"captionTrackIndices": [0, 1]}],
            }
        },
    }
    try:
        transcript._original_audio_language(player_response, tracks)
    except ValueError as exc:
        assert "could not determine" in str(exc)
    else:
        raise AssertionError("multiple caption languages must not be guessed")


@case
def metadata_failure_reason_is_preserved_for_the_user():
    message = transcript.describe(
        ValueError("could not determine one original audio language"),
        transcript.ERROR,
    )
    assert message == "ValueError: could not determine one original audio language"


@case
def selects_manual_caption_matching_original_audio_not_first_auto_track():
    arabic = FakeTrack("ar", generated=True, text="هذا ليس الصوت الأصلي")
    english_auto = FakeTrack("en", generated=True, text="Original English speech.")
    english_manual = FakeTrack("en-US", generated=False, text="Manual English speech.")
    french = FakeTrack("fr", generated=False, text="Ce n'est pas la piste source.")
    transcript_list = [arabic, english_auto, english_manual, french]

    selected = transcript._pick_track_for_audio(transcript_list, "en-US")

    assert selected is english_manual


@case
def fetch_uses_matching_track_and_reports_language():
    arabic = FakeTrack("ar", generated=True, text="كلام عربي")
    english = FakeTrack("en", generated=True, text="Hello from the English source.")
    original_lookup = transcript._list_transcripts_with_player_response
    original_api = transcript.YouTubeTranscriptApi
    transcript._list_transcripts_with_player_response = lambda api, video_id: (
        [arabic, english],
        {
            "streamingData": {
                "adaptiveFormats": [
                    {"audioTrack": {"id": "en-US.4", "audioIsDefault": True}}
                ]
            }
        },
    )
    transcript.YouTubeTranscriptApi = lambda: object()
    try:
        result = transcript.fetch_transcript("video1234567")
    finally:
        transcript._list_transcripts_with_player_response = original_lookup
        transcript.YouTubeTranscriptApi = original_api

    assert result.status == transcript.OK, result
    assert result.lang == "en", result.lang
    assert result.text == "Hello from the English source.", result.text
    assert result.cues[0].start_ms == 250
    assert result.cues[0].char_end == len(result.text)


@case
def missing_matching_track_returns_an_error_instead_of_falling_back():
    arabic = FakeTrack("ar", generated=True, text="كلام عربي")
    original_lookup = transcript._list_transcripts_with_player_response
    original_audio_language = transcript._original_audio_language
    original_api = transcript.YouTubeTranscriptApi
    transcript._list_transcripts_with_player_response = lambda api, video_id: (
        [arabic],
        {},
    )
    transcript._original_audio_language = lambda response, transcript_list=None: "en-US"
    transcript.YouTubeTranscriptApi = lambda: object()
    try:
        result = transcript.fetch_transcript("video1234567")
    finally:
        transcript._list_transcripts_with_player_response = original_lookup
        transcript._original_audio_language = original_audio_language
        transcript.YouTubeTranscriptApi = original_api

    assert result.status == transcript.ERROR, result.status
    assert "no caption track matches original audio language 'en-US'" == result.error


if __name__ == "__main__":
    sys.exit(suite.run())
