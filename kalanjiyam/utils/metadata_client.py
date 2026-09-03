"""HTTP client for the archival description extractor.

Talks to ``POST /v1/metadata`` on the same host as OCR, with the same API key and
the same two-target failover -- see `METADATA_API_Payload_Specification.md`.

Modelled on `utils.ocr_client` rather than `utils.llm_client`: this endpoint takes
a request-shaped payload (taxonomy version, tag list, typed blocks) that the
persona API cannot express, and it fails over between service targets the way OCR
does.

Two things the caller must not have to think about:

* **Write-locked tags never leave this module.** `build_request` filters them out
  of ``tags``, and `parse_response` drops them again on the way back. A model has
  no opportunity to fabricate a custodial history even if it invents one.
* **A bad *generation* is not an exception.** As in `llm_client`, an unusable
  payload comes back as a result with ``ok`` False so the caller can record a
  failed window and move on. `MetadataServiceError` is raised only when the
  service itself refuses or is unreachable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from flask import current_app

from kalanjiyam.utils import archival_taxonomy as at

logger = logging.getLogger(__name__)

#: Version of the request/response contract this client speaks.
CONTRACT_VERSION = "1.0"

#: Request timeout, in seconds. Matches the OCR service default: a dense window
#: with a 4,500-token output budget is genuinely slow under GPU contention.
REQUEST_TIMEOUT = 300.0

#: Per-target attempts for transport errors and 5xx. Kept low -- these calls are
#: expensive and a failed window is retried as a unit by the task layer.
MAX_ATTEMPTS = 2


class MetadataServiceError(RuntimeError):
    """The service refused the request or could not be reached."""

    def __init__(
        self, message: str, *, status: int | None = None, code: str | None = None
    ):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class WindowResult:
    """One window's extraction."""

    #: Tag code -> field object, as returned. Empty when nothing was supported.
    fields: dict = field(default_factory=dict)
    #: Provenance echoed by the service.
    engine: str = ""
    model_name: str = ""
    model_version: str = ""
    taxonomy_version: str = ""
    contract_version: str = ""
    #: Counts as the service reported them.
    fields_attempted: int | None = None
    fields_returned: int | None = None
    fields_declined: int | None = None
    chars_in: int | None = None
    engine_latency_ms: float | None = None
    usage: dict = field(default_factory=dict)
    #: Tags the service returned that it was never asked for. Recorded rather
    #: than silently dropped -- a service returning locked tags is a contract
    #: violation worth seeing in the run log.
    unknown_tags: list = field(default_factory=list)
    locked_tags_returned: list = field(default_factory=list)
    #: The response as received, for debugging a bad window without re-running.
    raw: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _get_targets() -> list[tuple[str, str]]:
    """(base_url, api_key) pairs for the primary and fallback services."""
    import os
    targets = []
    url1 = (current_app.config.get("OCR_SERVICE_URL") or os.environ.get("OCR_SERVICE_URL") or "").rstrip("/")
    key1 = current_app.config.get("OCR_SERVICE_API_KEY") or os.environ.get("OCR_SERVICE_API_KEY") or ""
    if url1:
        targets.append((url1, key1))

    url2 = (current_app.config.get("OCR_SERVICE_URL_2") or os.environ.get("OCR_SERVICE_URL_2") or "").rstrip("/")
    key2 = current_app.config.get("OCR_SERVICE_API_KEY_2") or os.environ.get("OCR_SERVICE_API_KEY_2") or key1
    if url2 and url2 != url1:
        targets.append((url2, key2))
    return targets


def build_request(
    *,
    unit_id: str,
    window_index: int,
    window_total: int,
    pages: list[dict],
    language_hint: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Assemble the request body for one window.

    `pages` is a list of ``{"page_slug", "ocr_confidence", "blocks": [...]}``.
    ``ocr_confidence`` may be None: three of the OCR engines in service produce
    no confidence signal, and null is the honest value for those pages.
    """
    codes = tags or [tag.code for tag in at.extractable_tags()]
    # Belt and braces. `extractable_tags` already excludes these, but a caller
    # passing an explicit list must not be able to widen the request.
    codes = [c for c in codes if c not in at.WRITE_LOCKED]

    return {
        "contract_version": CONTRACT_VERSION,
        "unit_id": unit_id,
        "window": {
            "index": window_index,
            "total": window_total,
            "page_slugs": [p["page_slug"] for p in pages],
        },
        "taxonomy_version": at.TAXONOMY_VERSION,
        "tags": codes,
        "language_hint": language_hint or [],
        "pages": pages,
    }


def _error_message(payload: dict) -> tuple[str, str | None]:
    """Pull a message out of either error envelope the service can return."""
    for key in ("detail", "error"):
        blob = payload.get(key)
        if isinstance(blob, dict):
            return (
                str(blob.get("message") or blob.get("code") or blob),
                blob.get("code"),
            )
        if isinstance(blob, str):
            return blob, None
    return "unknown error", None


def parse_response(payload: dict, requested: list[str]) -> WindowResult:
    """Turn a service payload into a `WindowResult`.

    Unknown and write-locked tags are quarantined rather than admitted. Values are
    coerced through the taxonomy normalisers, which already tolerate the shapes
    models actually emit (a bare string where a list was specified, and so on).
    """
    if not isinstance(payload, dict):
        return WindowResult(error="response was not a JSON object")

    raw_fields = payload.get("fields")
    if raw_fields is None:
        return WindowResult(raw=payload, error="response has no `fields` object")
    if not isinstance(raw_fields, dict):
        return WindowResult(raw=payload, error="`fields` was not an object")

    asked = set(requested)
    fields, unknown, locked = {}, [], []
    for code, blob in raw_fields.items():
        if code in at.WRITE_LOCKED:
            locked.append(code)
            continue
        if code not in at.BY_CODE or code not in asked:
            unknown.append(code)
            continue
        normalised = _normalise_field(code, blob)
        if normalised is not None:
            fields[code] = normalised

    model = payload.get("model") or {}
    usage = payload.get("usage") or {}
    return WindowResult(
        fields=fields,
        engine=payload.get("engine") or "",
        model_name=(model.get("name") if isinstance(model, dict) else "") or "",
        model_version=(model.get("version") if isinstance(model, dict) else "") or "",
        taxonomy_version=payload.get("taxonomy_version") or "",
        contract_version=payload.get("contract_version") or "",
        fields_attempted=_as_int(payload.get("fields_attempted")),
        fields_returned=_as_int(payload.get("fields_returned")),
        fields_declined=_as_int(payload.get("fields_declined")),
        chars_in=_as_int(payload.get("chars_in")),
        engine_latency_ms=_as_float(payload.get("engine_latency_ms")),
        usage=usage if isinstance(usage, dict) else {},
        unknown_tags=sorted(unknown),
        locked_tags_returned=sorted(locked),
        raw=payload,
    )


def _normalise_field(code: str, blob) -> dict | None:
    """Coerce one field object into ``{value, confidence, source, evidence}``.

    Returns None for a field with no usable value, so a tag the model nominally
    answered but left empty counts as declined rather than filled.
    """
    tag = at.BY_CODE[code]

    # A model that ignores the wrapper and returns the bare value is common
    # enough to be worth handling rather than discarding the window.
    if not isinstance(blob, dict) or "value" not in blob:
        blob = {"value": blob}

    value = blob.get("value")
    if tag.kind == at.KIND_ENTITIES:
        value = at.normalize_entities(value)
    elif tag.kind == at.KIND_RELATIONS:
        value = at.normalize_relations(value)
    elif value is not None and not isinstance(value, str):
        value = str(value)

    if at.is_empty(value):
        return None

    source = blob.get("source")
    if source not in at.SOURCES:
        # Unstated provenance defaults to `record`, which is the strict reading:
        # it makes the value subject to quote verification rather than exempt.
        source = at.SOURCE_RECORD

    return {
        "value": value,
        "confidence": _as_float(blob.get("confidence")),
        "source": source,
        "evidence": _normalise_evidence(blob.get("evidence")),
    }


def _normalise_evidence(value) -> list[dict]:
    """Coerce evidence into a list of ``{page_slug, block_id, quote}`` spans."""
    if at.is_empty(value):
        return []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []

    spans = []
    for item in value:
        if not isinstance(item, dict):
            continue
        span = {
            "page_slug": _as_str(item.get("page_slug") or item.get("page")),
            "block_id": _as_str(item.get("block_id") or item.get("block")),
            "quote": _as_str(item.get("quote") or item.get("text")),
        }
        if span["page_slug"] or span["quote"]:
            spans.append(span)
    return spans


def _as_str(value) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_window(request_body: dict, *, timeout: float | None = None) -> WindowResult:
    """Call the service for one window, failing over between targets.

    Raises `MetadataServiceError` only when every target refuses or is
    unreachable. An unusable *generation* comes back as a result with ``ok``
    False.
    """
    targets = _get_targets()
    if not targets:
        raise MetadataServiceError("OCR_SERVICE_URL is not configured.")

    timeout = timeout or float(
        current_app.config.get("OCR_SERVICE_TIMEOUT", REQUEST_TIMEOUT)
    )
    requested = list(request_body.get("tags") or [])
    last_error: Exception | None = None

    for idx, (base_url, api_key) in enumerate(targets):
        url = f"{base_url}/v1/metadata"
        headers = {"X-API-Key": api_key} if api_key else {}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    url, json=request_body, headers=headers, timeout=timeout
                )
            except httpx.HTTPError as e:
                last_error = e
                logger.warning(
                    "metadata transport error at %s (attempt %s): %s",
                    base_url,
                    attempt,
                    e,
                )
                continue

            if response.status_code >= 500 or (response.status_code == 404 and idx < len(targets) - 1):
                last_error = MetadataServiceError(
                    f"service returned {response.status_code}",
                    status=response.status_code,
                )
                logger.warning(
                    "metadata service %s at %s (attempt %s). Falling back if next target exists...",
                    response.status_code,
                    base_url,
                    attempt,
                )
                continue

            if response.status_code >= 400:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                message, code = _error_message(payload)
                raise MetadataServiceError(
                    message, status=response.status_code, code=code
                )

            try:
                payload = response.json()
            except ValueError as e:
                return WindowResult(error=f"service returned non-JSON body: {e}")

            if (payload or {}).get("status") == "error":
                message, _ = _error_message(payload)
                return WindowResult(raw=payload, error=message)

            result = parse_response(payload, requested)
            if result.locked_tags_returned:
                logger.warning(
                    "metadata service returned write-locked tags: %s",
                    result.locked_tags_returned,
                )
            return result

    raise MetadataServiceError(f"metadata call failed on all targets: {last_error}")
