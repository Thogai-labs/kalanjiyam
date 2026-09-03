"""HTTP client for the voice-editing agents on the Kalanjiyam OCR service.

Voice editing shares a host, an app entry, and an API key with OCR and chat --
there is no separate voice service and no extra secret. Requests go to
``/v1/voice-edit``, which does speech recognition *and* intent interpretation in
one round trip: the user is mid-sentence, and a second hop would be felt.

See ``docs/voice-edit-service-contract.rst`` for the wire format.

Two things differ deliberately from `llm_client`:

- The timeout is short and there are no retries. Chat and OCR can afford 300s
  because a human is not waiting inside a loop; here they are, and a late answer
  applies to an utterance the user has already moved on from.
- Operations are validated against the request context before they are returned.
  A model that names a block we never sent it has misunderstood the request, and
  that output must not reach the editor.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from flask import current_app

logger = logging.getLogger(__name__)

#: Request timeout, in seconds. Short on purpose -- see the module docstring.
REQUEST_TIMEOUT = 30.0

#: Largest audio clip we will forward, in bytes. Client-side segmentation keeps
#: utterances to a few seconds; anything near this cap means the silence
#: detector failed open and is streaming a whole room.
MAX_AUDIO_BYTES = 5 * 1024 * 1024

#: Audio container types the browsers we support actually produce. Chrome and
#: Firefox emit webm/opus; Safari emits mp4.
ALLOWED_AUDIO_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
    }
)

#: Intents the editor knows how to act on. Anything else is treated as noise.
INTENTS = frozenset({"edit", "dictate", "navigate", "question", "clarify", "noise"})

#: Operation types and their required fields, beyond ``block_id``.
OP_FIELDS: dict[str, tuple[str, ...]] = {
    "replace": ("find", "replace"),
    "replace_block": ("content",),
    "append": ("content",),
    "insert_after": ("content",),
    "insert_before": ("content",),
    "delete_block": (),
    "set_language": ("language",),
}

#: Navigation actions the editor implements.
COMMANDS = frozenset(
    {
        "save",
        "next_page",
        "prev_page",
        "undo",
        "stop_listening",
        "zoom_in",
        "zoom_out",
        "reset_zoom",
        "select_block",
    }
)

#: Caps on the context we forward. A page of manuscript is tens of blocks; these
#: bounds exist so a corrupted client document cannot turn into a huge prompt.
MAX_CONTEXT_BLOCKS = 200
MAX_BLOCK_CHARS = 4_000


class VoiceError(RuntimeError):
    """A call failed in a way that leaves us with no usable payload."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass
class VoiceResult:
    """One voice-edit turn."""

    #: What the user said, verbatim. Populated even when nothing is actionable.
    transcript: str = ""
    #: Detected/echoed language code.
    language: str = ""
    #: One of `INTENTS`. Anything unrecognised is coerced to "noise".
    intent: str = "noise"
    #: Validated edit operations, in application order.
    ops: list[dict[str, Any]] = field(default_factory=list)
    #: ``{"action": ..., "args": {...}}`` for navigate intents.
    command: dict[str, Any] | None = None
    #: Plain-text reply for question intents.
    answer: str = ""
    #: ``{"id", "question", "options"}`` when the model needs disambiguation.
    clarification: dict[str, Any] | None = None
    model: str = ""
    usage: dict = field(default_factory=dict)
    #: Operations dropped during validation, as ``(op, reason)`` pairs. Kept for
    #: logging and metrics; the editor never sees these.
    dropped: list[tuple[dict[str, Any], str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the turn produced something worth showing the user."""
        return bool(self.ops or self.command or self.answer or self.clarification)

    def to_api_dict(self) -> dict[str, Any]:
        """The shape the editor receives."""
        return {
            "transcript": self.transcript,
            "language": self.language,
            "intent": self.intent,
            "ops": self.ops,
            "command": self.command,
            "answer": self.answer,
            "clarification": self.clarification,
        }


def _error_message(payload: dict) -> tuple[str, str | None]:
    """Pull a message out of either error shape the service can return.

    Mirrors `llm_client._error_message`: handled errors arrive as
    ``{"detail": {...}}`` from FastAPI, unhandled ones as ``{"error": {...}}``.
    """
    for key in ("detail", "error"):
        blob = payload.get(key)
        if isinstance(blob, dict):
            return str(blob.get("message") or blob.get("code") or blob), blob.get("code")
        if isinstance(blob, str):
            return blob, None
    return "voice service returned an error", None


def build_context(
    blocks: list[dict[str, Any]],
    *,
    selected_block_id: str | None = None,
    pending_clarification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trim the client's document down to what the agents need to decide.

    Only id, reading_order, content and language are forwarded. Bounding boxes
    and confidences are withheld deliberately: they are not evidence for any
    decision made here, and they would multiply the payload size several times over.
    """
    trimmed = []
    for raw in blocks[:MAX_CONTEXT_BLOCKS]:
        if not isinstance(raw, dict):
            continue
        block_id = raw.get("id")
        if not block_id:
            continue
        entry: dict[str, Any] = {
            "id": str(block_id),
            "reading_order": int(raw.get("reading_order") or 0),
            "content": str(raw.get("content") or "")[:MAX_BLOCK_CHARS],
        }
        if raw.get("language"):
            entry["language"] = str(raw["language"])
        trimmed.append(entry)

    context: dict[str, Any] = {"blocks": trimmed}
    if selected_block_id:
        context["selected_block_id"] = str(selected_block_id)
    if pending_clarification:
        context["pending_clarification"] = pending_clarification
    return context


def _validate_ops(
    raw_ops: Any, known_block_ids: set[str]
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """Keep only operations the editor can safely apply.

    The frontend re-checks all of this before touching the document -- exact
    `find` matching in particular can only be verified against the live text.
    We check here too so that a malformed generation is caught and logged at the
    boundary rather than surfacing as a confusing no-op in the UI.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[tuple[dict[str, Any], str]] = []

    if not isinstance(raw_ops, list):
        return kept, dropped

    for raw in raw_ops:
        if not isinstance(raw, dict):
            dropped.append(({"raw": repr(raw)[:200]}, "not an object"))
            continue

        op_name = str(raw.get("op") or "")
        if op_name not in OP_FIELDS:
            dropped.append((raw, f"unknown op {op_name!r}"))
            continue

        block_id = str(raw.get("block_id") or "")
        if not block_id:
            dropped.append((raw, "missing block_id"))
            continue
        if block_id not in known_block_ids:
            # Rule 3 of the contract. This means the request was misunderstood.
            dropped.append((raw, f"block_id {block_id!r} not in context"))
            continue

        missing = [f for f in OP_FIELDS[op_name] if raw.get(f) is None]
        if missing:
            dropped.append((raw, f"missing {', '.join(missing)}"))
            continue

        op: dict[str, Any] = {"op": op_name, "block_id": block_id}
        for name in OP_FIELDS[op_name]:
            op[name] = raw[name]
        if op_name == "replace":
            try:
                occurrence = int(raw.get("occurrence") or 1)
            except (TypeError, ValueError):
                occurrence = 1
            op["occurrence"] = max(1, occurrence)
        try:
            op["confidence"] = float(raw["confidence"]) if raw.get("confidence") is not None else None
        except (TypeError, ValueError):
            op["confidence"] = None

        kept.append(op)

    return kept, dropped


def _validate_clarification(raw: Any, known_block_ids: set[str]) -> dict[str, Any] | None:
    """Validate a clarification, including the ops carried by each option.

    Options whose operations do not survive validation are dropped: offering a
    choice that would silently do nothing is worse than not offering it.
    """
    if not isinstance(raw, dict):
        return None
    question = str(raw.get("question") or "").strip()
    if not question:
        return None

    options = []
    for i, opt in enumerate(raw.get("options") or []):
        if not isinstance(opt, dict):
            continue
        label = str(opt.get("label") or "").strip()
        if not label:
            continue
        ops, _ = _validate_ops(opt.get("ops"), known_block_ids)
        if not ops:
            continue
        options.append({"id": str(opt.get("id") or f"opt-{i}"), "label": label, "ops": ops})

    if not options:
        return None
    return {"id": str(raw.get("id") or "c1"), "question": question, "options": options}


def _validate_command(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "")
    if action not in COMMANDS:
        return None
    args = raw.get("args")
    return {"action": action, "args": args if isinstance(args, dict) else {}}


def _build_result(payload: dict, known_block_ids: set[str]) -> VoiceResult:
    intent = str(payload.get("intent") or "noise")
    if intent not in INTENTS:
        logger.warning("voice service returned unknown intent %r; treating as noise", intent)
        intent = "noise"

    ops, dropped = _validate_ops(payload.get("ops"), known_block_ids)
    clarification = _validate_clarification(payload.get("clarification"), known_block_ids)
    command = _validate_command(payload.get("command"))

    if dropped:
        logger.warning(
            "voice service returned %s unusable op(s): %s",
            len(dropped),
            "; ".join(reason for _, reason in dropped),
        )

    # A clarify turn that lost its clarification has nothing left to say. Fall
    # back to noise rather than showing an empty card.
    if intent == "clarify" and not clarification:
        intent = "noise"

    return VoiceResult(
        transcript=str(payload.get("transcript") or ""),
        language=str(payload.get("language") or ""),
        intent=intent,
        ops=ops,
        command=command,
        answer=str(payload.get("answer") or ""),
        clarification=clarification,
        model=str(payload.get("model") or ""),
        usage=payload.get("usage") or {},
        dropped=dropped,
    )


def transcribe_and_interpret(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str,
    language: str,
    context: dict[str, Any],
    timeout: float | None = None,
) -> VoiceResult:
    """Send one utterance and get back the editor's next move.

    Never raises for a bad *generation* -- an unusable or empty result comes back
    as a `VoiceResult` with ``intent`` "noise", because with an open microphone
    that is the ordinary case and must stay silent in the UI. Raises `VoiceError`
    only when the service itself refuses or is unreachable.
    """
    base_url = (current_app.config.get("OCR_SERVICE_URL") or "").rstrip("/")
    if not base_url:
        raise VoiceError("OCR_SERVICE_URL is not configured.")

    if timeout is None:
        timeout = float(current_app.config.get("VOICE_SERVICE_TIMEOUT") or REQUEST_TIMEOUT)

    url = f"{base_url}/v1/voice-edit"
    headers = {"X-API-Key": current_app.config.get("OCR_SERVICE_API_KEY", "")}
    files = {"audio": (filename, audio_bytes, content_type)}
    data = {"language": language, "context": json.dumps(context, ensure_ascii=False)}

    known_block_ids = {b["id"] for b in context.get("blocks", []) if b.get("id")}

    try:
        # trust_env=False for the same reason as the llm-gemma translation
        # client: institutional squid proxies intercept direct IPs.
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(url, files=files, data=data, headers=headers)
    except httpx.HTTPError as e:
        # No retry: by the time a second attempt returned, the user would have
        # said something else and this answer would apply to stale text.
        raise VoiceError(f"voice service unreachable: {e}") from e

    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message, code = _error_message(payload)
        raise VoiceError(message, status=response.status_code, code=code)

    try:
        payload = response.json()
    except ValueError as e:
        raise VoiceError(f"voice service returned non-JSON body: {e}") from e

    if not isinstance(payload, dict):
        raise VoiceError("voice service returned a non-object body")

    return _build_result(payload, known_block_ids)
