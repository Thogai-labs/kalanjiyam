"""Tests for the ASR client and server-side voice pipeline."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from kalanjiyam.utils import asr_client
from kalanjiyam.utils.asr_client import (
    AsrError,
    _normalize_asr_url,
    is_silence_or_noise,
    select_asr_model,
    transcribe_audio,
)
from kalanjiyam.utils.voice_client import VoiceResult, transcribe_and_interpret


def test_normalize_asr_url():
    assert _normalize_asr_url("") == ""
    assert (
        _normalize_asr_url("http://10.195.100.51:4000/v1")
        == "http://10.195.100.51:4000/v1/audio/transcriptions"
    )
    assert (
        _normalize_asr_url("http://10.195.100.51:4000/v1/")
        == "http://10.195.100.51:4000/v1/audio/transcriptions"
    )
    assert (
        _normalize_asr_url("http://10.195.100.51:4000")
        == "http://10.195.100.51:4000/v1/audio/transcriptions"
    )
    assert (
        _normalize_asr_url("http://10.195.100.51:4000/v1/audio/transcriptions")
        == "http://10.195.100.51:4000/v1/audio/transcriptions"
    )


def test_select_asr_model():
    assert select_asr_model(model="custom-model") == "custom-model"
    assert select_asr_model(language="sa") == "asr-sanskrit"
    assert select_asr_model(language="san") == "asr-sanskrit"
    assert select_asr_model(language="sanskrit") == "asr-sanskrit"
    assert select_asr_model(language="ta") == "asr-large"
    assert select_asr_model(language="en") == "asr-large"


def test_is_silence_or_noise():
    assert is_silence_or_noise("") is True
    assert is_silence_or_noise("   ") is True
    assert is_silence_or_noise("Thank you.") is True
    assert is_silence_or_noise("thank you") is True
    assert is_silence_or_noise("Thanks for watching!") is True
    assert is_silence_or_noise("change rama to lakshmana") is False
    assert is_silence_or_noise("சித்த மருத்துவம்") is False


def test_transcribe_audio_empty_bytes():
    res = transcribe_audio(b"")
    assert res["text"] == ""


def test_transcribe_audio_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "text": "Hello world",
        "language": "en",
        "duration": 1.2,
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = transcribe_audio(
            b"fake-audio-bytes",
            base_url="http://10.195.100.51:4000/v1",
            api_key="test-key",
            language="en",
        )
        assert result["text"] == "Hello world"
        assert result["language"] == "en"
        assert result["model"] == "asr-large"
        assert mock_post.called
        assert mock_post.call_args[0][0] == "http://10.195.100.51:4000/v1/audio/transcriptions"


def test_transcribe_audio_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal error"
    mock_resp.json.side_effect = ValueError()

    with patch("httpx.Client.post", return_value=mock_resp):
        with pytest.raises(AsrError) as exc_info:
            transcribe_audio(
                b"fake-audio-bytes",
                base_url="http://10.195.100.51:4000/v1",
                api_key="test-key",
            )
        assert exc_info.value.status == 500


def test_voice_client_fallback_on_404(flask_app):
    """When /v1/voice-edit returns 404, fallback to ASR + LLM interpretation pipeline."""
    # First response: /v1/voice-edit returns 404
    resp_404 = MagicMock()
    resp_404.status_code = 404

    # Second response: ASR transcription returns text
    resp_asr = MagicMock()
    resp_asr.status_code = 200
    resp_asr.json.return_value = {
        "text": "change rama to lakshmana in block-a",
        "language": "ta",
    }

    # Third response: LLM chat completion returns edit ops
    resp_llm = MagicMock()
    resp_llm.status_code = 200
    resp_llm.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": '```json\n{\n  "transcript": "change rama to lakshmana in block-a",\n  "language": "ta",\n  "intent": "edit",\n  "ops": [\n    {"op": "replace", "block_id": "block-a", "find": "rama", "replace": "lakshmana"}\n  ]\n}\n```'
                }
            }
        ]
    }

    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://10.195.100.51:4000/v1"
        flask_app.config["OCR_SERVICE_API_KEY"] = "test-key"

        with patch("httpx.Client.post", side_effect=[resp_404, resp_asr, resp_llm]):
            context = {
                "blocks": [{"id": "block-a", "reading_order": 1, "content": "rama"}]
            }
            res = transcribe_and_interpret(
                b"fake-audio",
                filename="test.wav",
                content_type="audio/wav",
                language="ta",
                context=context,
            )

            assert res.intent == "edit"
            assert res.transcript == "change rama to lakshmana in block-a"
            assert len(res.ops) == 1
            assert res.ops[0]["replace"] == "lakshmana"
            assert res.ops[0]["block_id"] == "block-a"
