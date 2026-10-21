"""Structured extraction of document entries from OCR text.

Calls the configured AI service at ``/v1/chat/completions`` with the
Gemma 4 26B IT model (same endpoint the translation engine uses).
Returns a validated list of entry dicts that can be displayed as an
editable table in the proofing editor and later exported to CSV.

This module deliberately has no Flask imports so it can be unit-tested
without an application context.  Pass ``api_url``, ``api_key``, and
``model`` explicitly from the view.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

VALID_ENTRY_TYPES = frozenset(
    {
        "part",
        "chapter",
        "heading",
        "subheading",
        "topic",
        "subtopic",
        "article",
        "review",
        "author",
        "index_entry",
        "page_reference",
        "other",
    }
)

CSV_COLUMNS = [
    "document_id",
    "entry_id",
    "parent_entry_id",
    "level",
    "entry_type",
    "heading",
    "description",
    "author",
    "page_start",
    "page_end",
    "page_label_raw",
    "source_pdf_page",
    "raw_ocr_text",
    "needs_review",
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a document-structure extraction assistant specialised in historical and academic publications, including Sanskrit, Tamil, and other Indian-language texts.

Your task: convert OCR-extracted text into a JSON array of structured entries representing the document's logical organisation (table of contents, index, chapter list, etc.).

## Output format

Return ONLY a raw JSON array — no Markdown fences, no prose, no comments.

Each element must be an object with these fields:

- "entry_id": string — stable short ID you generate, e.g. "e1", "e2"
- "parent_entry_id": string | null — entry_id of the logical parent, or null for top-level
- "level": integer ≥ 1 — nesting depth (1 = top-level part/section)
- "entry_type": one of "part", "chapter", "heading", "subheading", "topic", "subtopic", "article", "review", "author", "index_entry", "page_reference", "other"
- "heading": string | null — title or heading text
- "description": string | null — additional descriptive text when present
- "author": string | null — author or editor name if explicitly listed for this entry
- "page_start": integer | null — first page number as an integer (null if not parseable)
- "page_end": integer | null — last page number if a range is given
- "page_label_raw": string | null — original page label as it appears in the source (e.g. "iv", "xii–xiv", "23-25")
- "source_pdf_page": integer | null — physical scan page number if distinguishable from content page numbers
- "raw_ocr_text": string — the raw OCR line(s) this entry was derived from
- "needs_review": boolean — true if the entry is ambiguous, unclear, or you are uncertain

## Rules

1. Preserve original wording, spelling, punctuation, and diacritics exactly.
2. Infer parent–child relationships from numbering, indentation, and context.
3. Merge wrapped lines that belong to the same logical entry.
4. Separate titles, descriptions, authors, and page references when the source supports it.
5. Preserve original page labels including Roman numerals and page ranges.
6. Do NOT invent information. Use null for unavailable fields.
7. Mark ambiguous or uncertain entries with needs_review: true.
8. Do NOT re-OCR; use only the text provided.
9. Return a flat array — hierarchy is expressed via parent_entry_id, not nesting.
10. If the input contains no recognisable structure, return an empty array [].
"""

# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _find_array(text: str) -> str:
    """Return the outermost [...] span from text, tolerating surrounding prose."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def extract(
    ocr_text: str,
    *,
    api_url: str,
    api_key: str,
    model: str = "gemma-4",
    timeout: float = 300.0,
) -> list[dict]:
    """Call the AI service and return a validated list of entry dicts.

    Raises ``ExtractionError`` if the service is unreachable or returns
    unparseable output.
    """
    if not ocr_text or not ocr_text.strip():
        raise ExtractionError("No OCR text provided.")

    if not api_url:
        raise ExtractionError("AI service URL is not configured.")

    # Normalise model name: map known Gemma 4 aliases to deployed engine ID
    if model in ("google/gemma-4-26b-it", "gemma-4-26b-it", "google/gemma-4", "gemma-4-it"):
        model = "gemma-4"

    # Normalise URL: ensure it ends at /v1/chat/completions
    base = api_url.rstrip("/")
    if base.endswith("/chat/completions"):
        url = base
    elif base.endswith("/v1"):
        url = f"{base}/chat/completions"
    else:
        url = f"{base}/v1/chat/completions"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        key = api_key.strip().strip("'\"")
        headers["Authorization"] = f"Bearer {key}"
        headers["X-API-Key"] = key

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract the document structure from the following OCR text:\n\n{ocr_text}"},
        ],
        "temperature": 0.1,
        "max_tokens": 8192,
    }

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                raw = _parse_response(resp)
                return validate_entries(raw)
            if resp.status_code in (502, 503):
                last_err = ExtractionError(f"Service returned {resp.status_code}")
                time.sleep(2 * attempt)
                continue
            # 4xx – don't retry
            detail = resp.text
            try:
                body = resp.json()
                detail = (
                    body.get("detail")
                    or (body.get("error") or {}).get("message")
                    or detail
                )
            except Exception:
                pass
            raise ExtractionError(
                f"AI service error ({resp.status_code}): {detail}"
            )
        except httpx.TimeoutException as exc:
            last_err = exc
            logger.warning("structure_extract attempt %d timed out", attempt)
            time.sleep(2 * attempt)
        except httpx.TransportError as exc:
            last_err = exc
            logger.warning("structure_extract attempt %d transport error: %s", attempt, exc)
            time.sleep(2 * attempt)

    raise ExtractionError(
        f"AI service unreachable after 3 attempts: {last_err}"
    )


def _parse_response(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception as exc:
        raise ExtractionError(f"Non-JSON response from AI service: {exc}") from exc
    choices = body.get("choices") or []
    if not choices:
        raise ExtractionError("AI service returned no choices.")
    raw: str = (choices[0].get("message") or {}).get("content") or ""
    return raw


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ExtractionError(RuntimeError):
    """Raised when extraction or validation fails in a user-visible way."""


def validate_entries(raw: str) -> list[dict]:
    """Parse and validate the model's JSON output.

    Returns a list of normalised entry dicts.
    Raises ``ExtractionError`` with a human-readable message on failure.
    """
    if not raw or not raw.strip():
        raise ExtractionError("The model returned an empty response.")

    cleaned = _strip_fences(raw)

    # Strategy: try the full cleaned text first (avoids pulling a nested []
    # out of an object wrapper like {"entries": [...]}). If the full text is
    # not valid JSON, fall back to extracting the outermost [...] span — this
    # handles the common case where the model wraps its array in a prose
    # sentence such as "Here are the entries: [...]".
    data = None
    parse_error: json.JSONDecodeError | None = None
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        parse_error = exc

    if data is None:
        # Full text didn't parse — try span extraction
        candidate = _find_array(cleaned)
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            # Re-raise original error for a more useful message
            raise ExtractionError(
                f"The model did not return valid JSON. Parse error: {parse_error}. "
                f"Raw output (first 500 chars): {raw[:500]!r}"
            ) from parse_error

    if not isinstance(data, list):
        raise ExtractionError(
            f"Expected a JSON array but got {type(data).__name__}."
        )

    # Build id set for parent reference validation
    ids_seen: set[str] = set()
    normalised: list[dict] = []

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ExtractionError(f"Entry #{i} is not an object.")
        entry = _normalise_entry(item, i)
        ids_seen.add(entry["entry_id"])
        normalised.append(entry)

    # Validate parent references
    for entry in normalised:
        pid = entry["parent_entry_id"]
        if pid is not None and pid not in ids_seen:
            entry["needs_review"] = True
            logger.warning(
                "Entry %s has unknown parent_entry_id %r — flagged for review",
                entry["entry_id"],
                pid,
            )

    return normalised


def _normalise_entry(raw: dict, index: int) -> dict:
    """Coerce one raw entry dict into a validated, fully-specified entry."""
    entry: dict[str, Any] = {}

    # entry_id: must be a non-empty string; generate one if missing/invalid
    eid = raw.get("entry_id")
    entry["entry_id"] = str(eid).strip() if eid and str(eid).strip() else f"e{index + 1}"

    # parent_entry_id
    pid = raw.get("parent_entry_id")
    entry["parent_entry_id"] = str(pid).strip() if pid and str(pid).strip() else None

    # level: positive integer, default 1
    try:
        level = int(raw.get("level") or 1)
        entry["level"] = max(1, level)
    except (TypeError, ValueError):
        entry["level"] = 1

    # entry_type: validated against known set
    et = str(raw.get("entry_type") or "other").strip().lower()
    entry["entry_type"] = et if et in VALID_ENTRY_TYPES else "other"

    # Text fields — preserve as-is, strip None-ish values
    for field_name in ("heading", "description", "author", "page_label_raw", "raw_ocr_text"):
        val = raw.get(field_name)
        entry[field_name] = str(val) if val is not None and str(val).strip() else None

    # Integer fields — accept both int and float strings (e.g. "25.0")
    for field_name in ("page_start", "page_end", "source_pdf_page"):
        val = raw.get(field_name)
        if val is None:
            entry[field_name] = None
        else:
            try:
                entry[field_name] = int(float(val))
            except (TypeError, ValueError):
                entry[field_name] = None

    # needs_review: boolean
    entry["needs_review"] = bool(raw.get("needs_review", False))

    return entry


# ---------------------------------------------------------------------------
# Stable ID assignment (for user-edited tables before save)
# ---------------------------------------------------------------------------


def assign_stable_ids(entries: list[dict]) -> list[dict]:
    """Assign UUID-based entry_ids to entries that lack them or have blank ones.

    Used when the user has added rows via the editable table before saving.
    Preserves existing IDs and updates parent_entry_id references accordingly.
    """
    id_map: dict[str, str] = {}
    result = []

    for entry in entries:
        old_id = entry.get("entry_id") or ""
        if not old_id.strip():
            new_id = f"e-{uuid.uuid4().hex[:8]}"
            id_map[old_id] = new_id
            entry = {**entry, "entry_id": new_id}
        result.append(entry)

    # Fix up parent references — use `is not None` so empty-string parent
    # IDs (which are falsy) still get remapped correctly.
    for entry in result:
        pid = entry.get("parent_entry_id")
        if pid is not None and str(pid) in id_map:
            entry["parent_entry_id"] = id_map[str(pid)]

    return result


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

# Characters that Excel / Google Sheets treat as formula starters
_FORMULA_START = re.compile(r"^[=+\-@|]")


def _safe_cell(value: Any) -> str:
    """Stringify a cell value, preventing spreadsheet formula injection."""
    if value is None:
        return ""
    text = str(value)
    if _FORMULA_START.match(text):
        text = "'" + text  # prepend apostrophe — standard injection mitigation
    return text


def to_csv_bytes(entries: list[dict], document_id: str) -> bytes:
    """Serialise validated entries to UTF-8 CSV bytes (with BOM for Excel compat).

    Uses Python's csv module — never manual string concatenation.
    Handles commas, quotes, newlines, and Unicode automatically.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    writer.writerow(CSV_COLUMNS)

    for entry in entries:
        row = [
            _safe_cell(document_id),
            _safe_cell(entry.get("entry_id")),
            _safe_cell(entry.get("parent_entry_id")),
            _safe_cell(entry.get("level")),
            _safe_cell(entry.get("entry_type")),
            _safe_cell(entry.get("heading")),
            _safe_cell(entry.get("description")),
            _safe_cell(entry.get("author")),
            _safe_cell(entry.get("page_start")),
            _safe_cell(entry.get("page_end")),
            _safe_cell(entry.get("page_label_raw")),
            _safe_cell(entry.get("source_pdf_page")),
            _safe_cell(entry.get("raw_ocr_text")),
            _safe_cell(entry.get("needs_review")),
        ]
        writer.writerow(row)

    # UTF-8 BOM so Excel opens Unicode characters correctly
    return "\ufeff".encode("utf-8") + buf.getvalue().encode("utf-8")
