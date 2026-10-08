"""Transcript selection follows YouTube's original audio language.

Run with ``uv run python tests/test_transcript_language.py``. All YouTube
responses and transcript tracks are synthetic; these tests make no requests.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from support import Suite
from tessera import asr, transcript
from youtube_transcript_api import TranscriptsDisabled

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
def sole_asr_with_same_language_standard_caption_identifies_single_audio_source():
    standard = FakeTrack("en", generated=False, text="Standard English captions.")
    asr = FakeTrack("en", generated=True, text="Auto-generated English captions.")
    player_response = {
        "streamingData": {"adaptiveFormats": []},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr"},
                    {"languageCode": "en"},
                ],
                # YouTube can list both standard and ASR tracks in reverse order.
                "audioTracks": [{"captionTrackIndices": [1, 0]}],
            }
        },
    }
    tracks = [standard, asr]

    assert transcript._original_audio_language(player_response, tracks) == "en"
    assert transcript._pick_track_for_audio(tracks, "en") is standard


@case
def one_asr_does_not_make_different_language_caption_variants_unambiguous():
    tracks = [
        FakeTrack("en", generated=True, text="English."),
        FakeTrack("fr", generated=False, text="Français."),
    ]
    player_response = {
        "streamingData": {"adaptiveFormats": []},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr"},
                    {"languageCode": "fr"},
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
        raise AssertionError("a translated caption must not be mistaken for source audio")


@case
def single_audio_group_regression_fetches_the_matching_standard_track():
    standard = FakeTrack("en", generated=False, text="Windows update overview.")
    asr = FakeTrack("en", generated=True, text="Auto-generated alternative.")
    player_response = {
        "videoDetails": {"lengthSeconds": "3725"},
        "streamingData": {"adaptiveFormats": [{"xtags": "CggKA2RyYxIBMQ"}]},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "kind": "asr"},
                    {"languageCode": "en"},
                ],
                "audioTracks": [{
                    "captionTrackIndices": [1, 0],
                    "defaultCaptionTrackIndex": 1,
                    "hasDefaultTrack": True,
                }],
            }
        },
    }
    original_lookup = transcript._list_transcripts_with_player_response
    original_api = transcript.YouTubeTranscriptApi
    transcript._list_transcripts_with_player_response = lambda api, video_id: (
        [standard, asr], player_response,
    )
    transcript.YouTubeTranscriptApi = lambda: object()
    try:
        result = transcript.fetch_transcript("63Pf51WuUbo")
    finally:
        transcript._list_transcripts_with_player_response = original_lookup
        transcript.YouTubeTranscriptApi = original_api

    assert result.status == transcript.OK, result
    assert result.lang == "en"
    assert result.kind == "manual"
    assert result.text == "Windows update overview."
    assert result.duration_seconds == 3725


@case
def video_duration_is_missing_for_invalid_youtube_metadata():
    assert transcript._video_duration_seconds({"videoDetails": {"lengthSeconds": "0"}}) is None
    assert transcript._video_duration_seconds({"videoDetails": {"lengthSeconds": "1.5"}}) is None
    assert transcript._video_duration_seconds({"videoDetails": {"lengthSeconds": True}}) is None
    assert transcript._video_duration_seconds({}) is None


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
    original_asr = asr.transcribe_video
    transcript._list_transcripts_with_player_response = lambda api, video_id: (
        [arabic],
        {},
    )
    transcript._original_audio_language = lambda response, transcript_list=None: "en-US"
    transcript.YouTubeTranscriptApi = lambda: object()
    asr.transcribe_video = lambda video_id, **kwargs: None
    try:
        result = transcript.fetch_transcript("video1234567")
    finally:
        transcript._list_transcripts_with_player_response = original_lookup
        transcript._original_audio_language = original_audio_language
        transcript.YouTubeTranscriptApi = original_api
        asr.transcribe_video = original_asr

    assert result.status == transcript.ERROR, result.status
    assert "no caption track matches original audio language 'en-US'" == result.error


@case
def captions_disabled_uses_local_asr_when_available():
    original_api = transcript.YouTubeTranscriptApi
    original_lookup = transcript._list_transcripts_with_player_response
    original_asr = asr.transcribe_video
    transcript.YouTubeTranscriptApi = lambda: object()

    def no_captions(api, video_id):
        raise TranscriptsDisabled(video_id)

    transcript._list_transcripts_with_player_response = no_captions
    asr.transcribe_video = lambda video_id: transcript.TranscriptResult(
        transcript.OK,
        lang="en",
        kind="asr",
        text="Recognized speech.",
        duration_seconds=1250,
    )
    try:
        result = transcript.fetch_transcript("XFsbtGB1TXQ")
    finally:
        transcript.YouTubeTranscriptApi = original_api
        transcript._list_transcripts_with_player_response = original_lookup
        asr.transcribe_video = original_asr

    assert result.status == transcript.OK
    assert result.kind == "asr"
    assert result.text == "Recognized speech."
    assert result.duration_seconds == 1250


@case
def missing_caption_language_hint_is_forwarded_to_local_asr():
    original_lookup = transcript._list_transcripts_with_player_response
    original_audio_language = transcript._original_audio_language
    original_api = transcript.YouTubeTranscriptApi
    original_asr = asr.transcribe_video
    arabic = FakeTrack("ar", generated=True, text="كلام عربي")
    seen = []
    transcript._list_transcripts_with_player_response = lambda api, video_id: (
        [arabic], {},
    )
    transcript._original_audio_language = lambda response, transcript_list=None: "en-US"
    transcript.YouTubeTranscriptApi = lambda: object()

    def recognize(video_id, *, language_hint=None):
        seen.append(language_hint)
        return transcript.TranscriptResult(
            transcript.OK, lang="en", kind="asr", text="Recognized speech."
        )

    asr.transcribe_video = recognize
    try:
        result = transcript.fetch_transcript("video1234567")
    finally:
        transcript._list_transcripts_with_player_response = original_lookup
        transcript._original_audio_language = original_audio_language
        transcript.YouTubeTranscriptApi = original_api
        asr.transcribe_video = original_asr

    assert seen == ["en-US"]
    assert result.status == transcript.OK
    assert result.kind == "asr"


@case
def ambiguous_youtube_audio_metadata_uses_whisper_autodetection():
    original_lookup = transcript._list_transcripts_with_player_response
    original_audio_language = transcript._original_audio_language
    original_api = transcript.YouTubeTranscriptApi
    original_asr = asr.transcribe_video
    transcript._list_transcripts_with_player_response = lambda api, video_id: ([], {})

    def ambiguous(response, transcript_list=None):
        raise ValueError(
            "could not determine one original audio language from YouTube metadata"
        )

    transcript._original_audio_language = ambiguous
    transcript.YouTubeTranscriptApi = lambda: object()
    seen = []

    def recognize(video_id):
        seen.append(video_id)
        return transcript.TranscriptResult(
            transcript.OK, lang="fr", kind="asr", text="Parole reconnue."
        )

    asr.transcribe_video = recognize
    try:
        result = transcript.fetch_transcript("video1234567")
    finally:
        transcript._list_transcripts_with_player_response = original_lookup
        transcript._original_audio_language = original_audio_language
        transcript.YouTubeTranscriptApi = original_api
        asr.transcribe_video = original_asr

    assert seen == ["video1234567"]
    assert result.status == transcript.OK
    assert result.lang == "fr"
    assert result.kind == "asr"


if __name__ == "__main__":
    sys.exit(suite.run())
