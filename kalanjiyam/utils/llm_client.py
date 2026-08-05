"""HTTP client for the chat personas on the Kalanjiyam OCR service.

Chat shares a host, an app entry, and an API key with OCR -- there is no
separate LLM service and no extra secret. Requests go to ``/v1/chat`` (not
``/v1/chat/completions``, which is the OpenAI-shaped path for tool-calling and
bypasses the persona layer entirely).

The system prompt, temperature, and max_tokens live server-side in the
persona definition. We deliberately do not override them per request so that
behaviour matches the contract.

Responses need defensive handling: ``content`` is a raw string that nothing
server-side validates as JSON, and a generation truncated by max_tokens comes
back as a perfectly ordinary 200.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx
from flask import current_app

logger = logging.getLogger(__name__)

#: Personas defined in the service's personas.yaml.
PERSONA_FRONT_MATTER = "kalanjiyam-front-matter"
PERSONA_CONTENT_ANALYSIS = "kalanjiyam-content-analysis"
PERSONA_LANGUAGE_ID = "kalanjiyam-language-id"

#: Request timeout, in seconds. The service's own inference client allows 300s;
#: the packaged SDK uses 120s, which is *shorter* than the engine timeout and
#: can abort a legitimately slow generation (a 4096-token content-analysis call
#: under GPU contention). Do not lower this to match the SDK.
REQUEST_TIMEOUT = 300.0

#: Retries for transport errors and 5xx. Kept low: these calls are expensive
#: and the task records partial results rather than hammering the service.
MAX_ATTEMPTS = 2

#: Keys we expect back from each persona. Missing keys are coerced to None so
#: downstream code never has to guard for absence.
EXPECTED_KEYS = {
    PERSONA_FRONT_MATTER: (
        "title",
        "subtitle",
        "author",
        "editor_translator",
        "publisher",
        "place_of_publication",
        "year",
        "edition",
        "series",
        "subject",
        "genre",
        "catalog_ids",
        "confidence",
        "notes",
    ),
    PERSONA_CONTENT_ANALYSIS: (
        "summary",
        "keywords",
        "toc",
        "colophon",
        "entities",
    ),
    PERSONA_LANGUAGE_ID: ("languages",),
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class LlmError(RuntimeError):
    """A call failed in a way that leaves us with no usable payload."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class LlmResult:
    """One persona call."""

    persona: str
    #: Parsed JSON payload, or None when the model returned unusable output.
    data: dict | None = None
    #: Raw assistant string, always retained for debugging.
    raw: str = ""
    #: Token accounting reported by the service.
    usage: dict = field(default_factory=dict)
    #: Whether the service served this from its own cache.
    cached: bool = False
    model: str = ""
    #: True when the output looks cut off by max_tokens.
    truncated: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.data is not None


def _error_message(payload: dict) -> tuple[str, str | None]:
    """Pull a message out of either error shape the service can return.

    Handled errors (401/403/404/429/500) arrive as ``{"detail": {...}}`` via
    FastAPI's HTTPException handling; only a truly unhandled exception reaches
    the catch-all and produces ``{"error": {...}}``.
    """
    for key in ("detail", "error"):
        blob = payload.get(key)
        if isinstance(blob, dict):
            return str(blob.get("message") or blob.get("code") or blob), blob.get("code")
        if isinstance(blob, str):
            return blob, None
    return "unknown error", None


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text.strip())


def _slice_object(text: str) -> str:
    """Return the outermost {...} span, tolerating prose around it."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def parse_content(raw: str, persona: str) -> tuple[dict | None, bool]:
    """Parse a persona's reply into a dict.

    :return: ``(data, truncated)``. ``data`` is None when the string cannot be
        recovered; ``truncated`` marks output that looks cut off, which is the
        signal to retry with a smaller sample.
    """
    if not raw or not raw.strip():
        return None, False

    candidate = _slice_object(_strip_fences(raw))
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Non-empty content that will not parse is almost always a generation
        # cut off mid-object by max_tokens.
        return None, True

    if not isinstance(data, dict):
        return None, False

    for key in EXPECTED_KEYS.get(persona, ()):
        data.setdefault(key, None)
    return data, False


def chat(persona: str, text: str, *, timeout: float = REQUEST_TIMEOUT) -> LlmResult:
    """Call one persona with a single user message.

    Never raises for a bad *generation* -- that comes back as a result with
    ``ok`` False so the caller can record a partial run. Raises `LlmError`
    only when the service itself refuses or is unreachable.
    """
    base_url = (current_app.config.get("OCR_SERVICE_URL") or "").rstrip("/")
    if not base_url:
        raise LlmError("OCR_SERVICE_URL is not configured.")

    url = f"{base_url}/v1/chat"
    headers = {"X-API-Key": current_app.config.get("OCR_SERVICE_API_KEY", "")}
    # No system/temperature/max_tokens: the persona owns those.
    body = {"persona": persona, "messages": [{"role": "user", "content": text}]}

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            last_error = e
            logger.warning("llm chat transport error (attempt %s): %s", attempt, e)
            continue

        if response.status_code >= 500:
            last_error = LlmError(
                f"service returned {response.status_code}", status=response.status_code
            )
            logger.warning("llm chat %s (attempt %s)", response.status_code, attempt)
            continue

        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message, code = _error_message(payload)
            raise LlmError(message, status=response.status_code, code=code)

        return _build_result(persona, response)

    raise LlmError(f"chat call failed after {MAX_ATTEMPTS} attempts: {last_error}")


def _build_result(persona: str, response: httpx.Response) -> LlmResult:
    try:
        payload = response.json()
    except ValueError as e:
        raise LlmError(f"service returned non-JSON body: {e}") from e

    choices = payload.get("choices") or []
    raw = ""
    finish_reason = None
    if choices:
        raw = (choices[0].get("message") or {}).get("content") or ""
        finish_reason = choices[0].get("finish_reason")

    data, looks_truncated = parse_content(raw, persona)
    # Prefer the service's own signal when it provides one.
    truncated = finish_reason == "length" if finish_reason else looks_truncated

    result = LlmResult(
        persona=persona,
        data=data,
        raw=raw,
        usage=payload.get("usage") or {},
        cached=bool(payload.get("cached")),
        model=payload.get("model") or "",
        truncated=truncated,
    )
    if data is None:
        result.error = "truncated output" if truncated else "unparseable output"
        logger.warning("llm persona %s returned unusable output: %s", persona, result.error)
    return result


def chat_with_backoff(persona: str, text: str) -> LlmResult:
    """`chat`, retrying once on a truncated generation with half the input.

    Sampling budgets are estimates -- we do not run the model's tokenizer
    locally -- so this is the runtime guard for the case where the estimate
    was too generous.
    """
    result = chat(persona, text)
    if result.ok or not result.truncated or len(text) < 2_000:
        return result

    logger.info("retrying persona %s with a halved sample", persona)
    retry = chat(persona, text[: len(text) // 2])
    if retry.ok:
        retry.truncated = True  # record that we had to shrink the input
        return retry
    return result
