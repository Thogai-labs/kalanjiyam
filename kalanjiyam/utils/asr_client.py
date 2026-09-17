"""HTTP client for Automatic Speech Recognition (ASR) services.

Supports OpenAI-compatible audio transcription endpoints (e.g. LiteLLM proxy,
Whisper, Riva) via ``POST /v1/audio/transcriptions``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from flask import current_app

logger = logging.getLogger(__name__)

#: Default request timeout in seconds.
REQUEST_TIMEOUT = 30.0

#: Standard audio container types supported across browsers and audio files.
ALLOWED_AUDIO_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/flac",
        "audio/m4a",
    }
)

#: Common Whisper silence / hallucination artifacts that should be filtered as noise.
SILENCE_PHRASES = frozenset(
    {
        "",
        "thank you",
        "thank you.",
        "thank you very much.",
        "thanks for watching",
        "thanks for watching!",
        "thanks for watching.",
        "subtitles by",
        "bye",
        "you",
        "you.",
        ".",
    }
)


class AsrError(RuntimeError):
    """Raised when an ASR request fails."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _normalize_asr_url(url: str | None) -> str:
    """Normalize base or specific URL to the full /v1/audio/transcriptions endpoint."""
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith("/audio/transcriptions"):
        return raw
    if raw.endswith("/v1"):
        return f"{raw}/audio/transcriptions"
    return f"{raw}/v1/audio/transcriptions"


def select_asr_model(language: str | None = None, model: str | None = None) -> str:
    """Select the most suitable ASR model given the target language and preference.

    - If a model is explicitly passed, use it.
    - If language is Sanskrit ('sa', 'san', 'sanskrit'), defaults to 'asr-sanskrit'.
    - Otherwise defaults to config DEFAULT_ASR_MODEL or 'asr-large'.
    """
    if model:
        return model.strip()

    lang_clean = (language or "").strip().lower()
    if lang_clean in {"sa", "san", "sanskrit"}:
        return "asr-sanskrit"

    default_model = None
    try:
        default_model = current_app.config.get("DEFAULT_ASR_MODEL")
    except RuntimeError:
        # Outside application context
        pass

    return default_model or "asr-large"


def is_silence_or_noise(text: str) -> bool:
    """Check if the transcription is empty or a typical Whisper silence hallucination."""
    cleaned = text.strip().lower().strip(" .!?,;:")
    return cleaned in SILENCE_PHRASES or not cleaned


def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str = "utterance.wav",
    content_type: str = "audio/wav",
    language: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
    timeout: float | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Send an audio clip to the ASR service and return the transcription result.

    Returns a dict conforming to the OpenAI transcription response:
    {
        "text": "...",
        "language": "...",
        "duration": float,
        "model": "...",
        ...
    }
    """
    if not audio_bytes:
        return {"text": "", "language": language or "", "duration": 0.0, "model": model or ""}

    # Resolve URL and key
    raw_url = base_url
    raw_key = api_key
    default_timeout = REQUEST_TIMEOUT

    try:
        if not raw_url:
            raw_url = (
                current_app.config.get("ASR_SERVICE_URL")
                or current_app.config.get("OCR_SERVICE_URL")
                or ""
            )
        if not raw_key:
            raw_key = (
                current_app.config.get("ASR_SERVICE_API_KEY")
                or current_app.config.get("OCR_SERVICE_API_KEY")
                or ""
            )
        if timeout is None:
            default_timeout = float(
                current_app.config.get("ASR_SERVICE_TIMEOUT")
                or current_app.config.get("VOICE_SERVICE_TIMEOUT")
                or REQUEST_TIMEOUT
            )
    except RuntimeError:
        pass

    endpoint_url = _normalize_asr_url(raw_url)
    if not endpoint_url:
        raise AsrError("ASR service URL is not configured.")

    req_timeout = timeout if timeout is not None else default_timeout
    selected_model = select_asr_model(language=language, model=model)

    files = {"file": (filename, audio_bytes, content_type)}
    data: dict[str, Any] = {"model": selected_model}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    headers: dict[str, str] = {}
    if raw_key:
        headers["Authorization"] = f"Bearer {raw_key}"
        headers["X-API-Key"] = raw_key

    try:
        with httpx.Client(timeout=req_timeout, trust_env=False) as client:
            response = client.post(endpoint_url, files=files, data=data, headers=headers)
    except httpx.HTTPError as e:
        raise AsrError(f"ASR service unreachable at {endpoint_url}: {e}") from e

    if response.status_code >= 400:
        err_msg = response.text
        code = None
        try:
            err_json = response.json()
            if isinstance(err_json, dict):
                err_detail = err_json.get("detail") or err_json.get("error")
                if isinstance(err_detail, dict):
                    err_msg = str(err_detail.get("message") or err_detail.get("code") or err_detail)
                    code = err_detail.get("code")
                elif isinstance(err_detail, str):
                    err_msg = err_detail
        except Exception:
            pass
        raise AsrError(
            f"ASR service returned {response.status_code}: {err_msg}",
            status=response.status_code,
            code=code,
        )

    try:
        result = response.json()
    except Exception as e:
        raise AsrError(f"ASR service returned non-JSON body: {e}") from e

    if not isinstance(result, dict):
        raise AsrError("ASR service returned a non-object body")

    if "model" not in result:
        result["model"] = selected_model

    return result
