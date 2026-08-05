"""Tests for the chat-persona client.

The service returns model output as an unvalidated string in a 200 response,
so most of the risk here is in parsing rather than transport.
"""

from unittest.mock import patch

import httpx
import pytest

from kalanjiyam.utils import llm_client as lc


# Parsing
# -------


def test_parse_content__plain_json():
    data, truncated = lc.parse_content('{"title": "X"}', lc.PERSONA_FRONT_MATTER)
    assert data["title"] == "X"
    assert truncated is False


def test_parse_content__fills_missing_keys_with_none():
    data, _ = lc.parse_content('{"title": "X"}', lc.PERSONA_FRONT_MATTER)
    assert data["author"] is None
    assert data["publisher"] is None


def test_parse_content__strips_code_fences():
    data, _ = lc.parse_content('```json\n{"title": "Y"}\n```', lc.PERSONA_FRONT_MATTER)
    assert data["title"] == "Y"


def test_parse_content__tolerates_surrounding_prose():
    raw = 'Here is the metadata:\n{"title": "Z"}\nHope this helps!'
    data, _ = lc.parse_content(raw, lc.PERSONA_FRONT_MATTER)
    assert data["title"] == "Z"


def test_parse_content__truncated_output_is_flagged():
    """Output cut off by max_tokens still arrives as a 200."""
    data, truncated = lc.parse_content('{"title": "cut o', lc.PERSONA_FRONT_MATTER)
    assert data is None
    assert truncated is True


def test_parse_content__empty_is_not_truncation():
    data, truncated = lc.parse_content("", lc.PERSONA_FRONT_MATTER)
    assert data is None
    assert truncated is False


def test_parse_content__rejects_non_objects():
    data, _ = lc.parse_content('["a", "b"]', lc.PERSONA_FRONT_MATTER)
    assert data is None


# Error shapes
# ------------


def test_error_message__detail_shape():
    """Handled errors arrive as `detail`, not `error`."""
    message, code = lc._error_message({"detail": {"code": "bad_key", "message": "nope"}})
    assert message == "nope"
    assert code == "bad_key"


def test_error_message__error_shape():
    """Only unhandled exceptions produce the `error` shape."""
    message, _ = lc._error_message({"error": {"message": "boom"}})
    assert message == "boom"


def test_error_message__string_detail():
    message, _ = lc._error_message({"detail": "plain string"})
    assert message == "plain string"


def test_error_message__unknown_shape():
    message, _ = lc._error_message({})
    assert message == "unknown error"


# Requests
# --------


def _response(payload, status=200):
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", "http://ocr.test/v1/chat"),
    )


def _ok_payload(content='{"title": "T"}', **extra):
    payload = {
        "id": "1",
        "model": "llm-gemma",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "cached": False,
    }
    payload.update(extra)
    return payload


def test_chat__sends_persona_and_reuses_the_ocr_credentials(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        flask_app.config["OCR_SERVICE_API_KEY"] = "secret"

        with patch("httpx.post", return_value=_response(_ok_payload())) as post:
            result = lc.chat(lc.PERSONA_FRONT_MATTER, "page text")

        _args, kwargs = post.call_args
        assert post.call_args[0][0] == "http://ocr.test/v1/chat"
        assert kwargs["headers"]["X-API-Key"] == "secret"
        assert kwargs["json"]["persona"] == lc.PERSONA_FRONT_MATTER
        assert kwargs["json"]["messages"] == [
            {"role": "user", "content": "page text"}
        ]
        assert result.ok
        assert result.data["title"] == "T"
        assert result.usage["total_tokens"] == 15


def test_chat__does_not_override_persona_sampling_params(flask_app):
    """temperature/max_tokens live server-side; sending them breaks the contract."""
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"

        with patch("httpx.post", return_value=_response(_ok_payload())) as post:
            lc.chat(lc.PERSONA_CONTENT_ANALYSIS, "text")

        body = post.call_args[1]["json"]
        assert "temperature" not in body
        assert "max_tokens" not in body
        assert "system" not in body


def test_chat__uses_a_timeout_above_the_engine_timeout(flask_app):
    """The packaged SDK's 120s would abort slow generations."""
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"

        with patch("httpx.post", return_value=_response(_ok_payload())) as post:
            lc.chat(lc.PERSONA_FRONT_MATTER, "text")

        assert post.call_args[1]["timeout"] >= 300.0


def test_chat__client_errors_raise(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        payload = {"detail": {"code": "forbidden", "message": "persona not allowed"}}

        with patch("httpx.post", return_value=_response(payload, status=403)):
            with pytest.raises(lc.LlmError) as excinfo:
                lc.chat(lc.PERSONA_FRONT_MATTER, "text")

        assert excinfo.value.status == 403
        assert "persona not allowed" in str(excinfo.value)


def test_chat__bad_generation_is_a_result_not_an_exception(flask_app):
    """A partial run is more useful than a raised exception."""
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        payload = _ok_payload(content="not json at all")

        with patch("httpx.post", return_value=_response(payload)):
            result = lc.chat(lc.PERSONA_FRONT_MATTER, "text")

        assert result.ok is False
        assert result.error
        assert result.raw == "not json at all"


def test_chat__prefers_finish_reason_over_inference(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        payload = {
            "model": "llm-gemma",
            "choices": [
                {
                    "message": {"role": "assistant", "content": '{"title": "a'},
                    "finish_reason": "length",
                }
            ],
        }

        with patch("httpx.post", return_value=_response(payload)):
            result = lc.chat(lc.PERSONA_FRONT_MATTER, "text")

        assert result.truncated is True


def test_chat__retries_server_errors(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        responses = [_response({}, status=500), _response(_ok_payload())]

        with patch("httpx.post", side_effect=responses) as post:
            result = lc.chat(lc.PERSONA_FRONT_MATTER, "text")

        assert post.call_count == 2
        assert result.ok


def test_chat__requires_a_configured_service_url(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = ""
        with pytest.raises(lc.LlmError):
            lc.chat(lc.PERSONA_FRONT_MATTER, "text")


def test_chat_with_backoff__retries_truncation_with_a_smaller_sample(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        text = "x" * 5000
        responses = [
            _response(_ok_payload(content='{"title": "trunc')),
            _response(_ok_payload()),
        ]

        with patch("httpx.post", side_effect=responses) as post:
            result = lc.chat_with_backoff(lc.PERSONA_FRONT_MATTER, text)

        assert post.call_count == 2
        assert len(post.call_args[1]["json"]["messages"][0]["content"]) == 2500
        assert result.ok


def test_chat_with_backoff__does_not_retry_short_inputs(flask_app):
    with flask_app.app_context():
        flask_app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        payload = _ok_payload(content='{"title": "trunc')

        with patch("httpx.post", return_value=_response(payload)) as post:
            result = lc.chat_with_backoff(lc.PERSONA_FRONT_MATTER, "short")

        assert post.call_count == 1
        assert result.ok is False
