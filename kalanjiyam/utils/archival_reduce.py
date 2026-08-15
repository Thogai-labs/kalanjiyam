"""Reduce per-window extractions into one description, and score them.

The map step (one model call per window) is the expensive half; this is the
cheap half, and almost all of it is deterministic. Only SCOPE CONTENT needs a
second model call, which the task layer makes -- everything here is arithmetic
and set union over what the windows already returned.

Two things carry the weight:

**Evidence verification.** `verify_evidence` checks that a quote the model
attributed to a page actually appears in the text we sent it. The service cannot
influence this number, which matters more than it sounds: three of the OCR
engines in service produce no confidence signal at all, so for documents OCR'd by
those engines this is the *only* objective quality measure available.

**Nulls stay null.** Confidence is absent, not zero, whenever an engine could not
measure it. `mean()` here averages over the values that exist and reports how many
did not, rather than treating a missing score as a bad one.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from kalanjiyam.utils import archival_taxonomy as at

#: Field confidences below this are surfaced for review. Mirrors the 0.7 floor
#: the OCR metrics use for `low_conf_page_count`.
LOW_CONFIDENCE = 0.7

#: Tags whose value is a single string; everything else is a list.
_SCALAR_KINDS = (at.KIND_TEXT, at.KIND_PROSE)

_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Evidence verification
# --------------------------------------------------------------------------


def normalize_quote(text: str) -> str:
    """Fold a quote for comparison.

    Whitespace, case and Unicode composition are all things OCR text and a
    model's transcription of it differ on harmlessly. Anything beyond that --
    different words, invented names -- must still fail.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    return _WS.sub(" ", text).strip().casefold()


def build_page_index(pages: list[dict]) -> dict:
    """Page slug -> normalised text of everything we sent for that page."""
    index = {}
    for page in pages or []:
        slug = str(page.get("page_slug") or "")
        joined = " ".join(
            str(block.get("text") or "") for block in page.get("blocks") or []
        )
        index[slug] = normalize_quote(joined)
    return index


def verify_span(span: dict, page_index: dict) -> bool | None:
    """Was this span's quote really in the text we sent?

    Returns True/False for a checkable span, and None when verification does not
    apply -- a `derived` value cites a page range rather than words, and there is
    nothing to look up.
    """
    quote = normalize_quote(span.get("quote") or "")
    if not quote:
        return None

    slug = str(span.get("page_slug") or "")
    if slug:
        haystack = page_index.get(slug)
        # A quote attributed to a page we did not send is unverifiable *and*
        # wrong about its own provenance, so it fails rather than abstaining.
        return bool(haystack) and quote in haystack

    # No page cited: accept it if it appears anywhere in the window, but this is
    # weaker evidence and the missing slug is recorded by the caller.
    return any(quote in text for text in page_index.values())


def verify_evidence(fields: dict, pages: list[dict]) -> dict:
    """Verify every span in a window's fields, in place, and count the results.

    A `record` value with no evidence at all is a claim with no support: its
    confidence is forced to 0.0 rather than left at whatever the model asserted.
    That is the rule that makes declining a tag cheaper than inventing one.
    """
    page_index = build_page_index(pages)
    spans = verified = unsupported = 0

    for code, blob in (fields or {}).items():
        tag = at.BY_CODE.get(code)
        if tag is None:
            continue

        if tag.kind in _SCALAR_KINDS:
            carriers = [blob]
        else:
            carriers = [v for v in (blob.get("value") or []) if isinstance(v, dict)]
            # Spans hung off the field rather than its values: attribute them to
            # the first value so they are still checked rather than discarded.
            if blob.get("evidence") and carriers:
                carriers[0].setdefault("evidence", [])
                carriers[0]["evidence"] = list(carriers[0]["evidence"]) + list(
                    blob["evidence"]
                )
                blob["evidence"] = []

        checkable = False
        for carrier in carriers:
            source = carrier.get("source") or blob.get("source") or at.SOURCE_RECORD
            for span in carrier.get("evidence") or []:
                spans += 1
                if source != at.SOURCE_RECORD:
                    span["verified"] = None
                    continue
                checkable = True
                ok = verify_span(span, page_index)
                span["verified"] = ok
                if ok:
                    verified += 1

        source = blob.get("source") or at.SOURCE_RECORD
        has_any = any(carrier.get("evidence") for carrier in carriers)
        if source == at.SOURCE_RECORD and not has_any:
            unsupported += 1
            blob["confidence"] = 0.0
            blob["unsupported"] = True
        elif checkable and not _any_verified(carriers):
            # Every quote it offered was absent from the source.
            unsupported += 1
            blob["confidence"] = 0.0
            blob["unsupported"] = True

    return {
        "evidence_spans": spans,
        "evidence_verified": verified,
        "unsupported_fields": unsupported,
    }


def _any_verified(carriers: list[dict]) -> bool:
    return any(
        span.get("verified") is True
        for carrier in carriers
        for span in carrier.get("evidence") or []
    )


# --------------------------------------------------------------------------
# Null-safe statistics
# --------------------------------------------------------------------------


def mean(values) -> float | None:
    """Average over the values that exist. None when none do."""
    present = [v for v in values if isinstance(v, (int, float))]
    if not present:
        return None
    return sum(present) / len(present)


def minimum(values) -> float | None:
    present = [v for v in values if isinstance(v, (int, float))]
    return min(present) if present else None


def count_missing(values) -> int:
    return sum(1 for v in values if not isinstance(v, (int, float)))


def window_metrics(fields: dict, pages: list[dict], evidence: dict) -> dict:
    """Per-window metrics Kalanjiyam derives rather than trusting the service."""
    confidences = [blob.get("confidence") for blob in (fields or {}).values()]
    ocr = [page.get("ocr_confidence") for page in pages or []]

    return {
        "fields_returned": len(fields or {}),
        "avg_field_confidence": mean(confidences),
        "min_field_confidence": minimum(confidences),
        "low_conf_field_count": sum(
            1 for c in confidences if isinstance(c, (int, float)) and c < LOW_CONFIDENCE
        ),
        "evidence_spans": evidence.get("evidence_spans", 0),
        "evidence_verified": evidence.get("evidence_verified", 0),
        # NULL, not 0, when no page in the window carried a score at all.
        "source_ocr_confidence": mean(ocr),
        "pages_without_confidence": count_missing(ocr),
        "chars_in": sum(
            len(block.get("text") or "")
            for page in pages or []
            for block in page.get("blocks") or []
        ),
    }


# --------------------------------------------------------------------------
# The reduce
# --------------------------------------------------------------------------


def _fold_key(label: str) -> str:
    """Key two spellings of the same name onto each other."""
    return normalize_quote(label).rstrip(".,;:")


def merge_entities(values: list[list[dict]]) -> list[dict]:
    """Union entity lists across windows, folding variant spellings.

    Ranked by how many windows mentioned each entity, so the names that recur
    through a file sort above a single passing reference. Every evidence span is
    kept: a person named on folios 4, 17 and 61 should cite all three.
    """
    merged: dict[str, dict] = {}
    counts: Counter = Counter()

    for window_values in values:
        seen_here = set()
        for entity in window_values or []:
            label = str(entity.get("label") or "").strip()
            if not label:
                continue
            key = _fold_key(label)
            if key not in merged:
                merged[key] = {
                    k: v for k, v in entity.items() if k not in ("evidence", "variants")
                }
                merged[key]["label"] = label
                merged[key]["variants"] = []
                merged[key]["evidence"] = []
            target = merged[key]

            for variant in entity.get("variants") or []:
                variant = str(variant).strip()
                if (
                    variant
                    and _fold_key(variant) != key
                    and variant not in target["variants"]
                ):
                    target["variants"].append(variant)
            # A differently-spelled label from another window is itself a variant.
            if label != target["label"] and label not in target["variants"]:
                target["variants"].append(label)

            for span in entity.get("evidence") or []:
                if span not in target["evidence"]:
                    target["evidence"].append(span)

            for key_name in ("dates", "auth_id", "note", "kind"):
                if not target.get(key_name) and entity.get(key_name):
                    target[key_name] = entity[key_name]

            if key not in seen_here:
                counts[key] += 1
                seen_here.add(key)

    out = []
    for key, entity in merged.items():
        entity["mentions"] = counts[key]
        if not entity["variants"]:
            entity.pop("variants")
        out.append(entity)
    out.sort(key=lambda e: (-e["mentions"], _fold_key(e["label"])))
    return out


def merge_relations(values: list[list[dict]]) -> list[dict]:
    """Union relation triples, deduped on (subject, type, object)."""
    merged: dict[tuple, dict] = {}
    for window_values in values:
        for triple in window_values or []:
            key = (
                _fold_key(triple.get("subject") or ""),
                _fold_key(triple.get("type") or ""),
                _fold_key(triple.get("object") or ""),
            )
            if key not in merged:
                merged[key] = dict(triple)
                merged[key]["evidence"] = list(triple.get("evidence") or [])
                continue
            for span in triple.get("evidence") or []:
                if span not in merged[key]["evidence"]:
                    merged[key]["evidence"].append(span)
    return list(merged.values())


#: Four-digit years, the only date form worth extracting deterministically.
#: Anything subtler (Hijri conversion, regnal years) belongs to the model, which
#: is asked to normalise and keep the original in parentheses.
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def merge_dates(values: list[str]) -> str | None:
    """Collapse per-window dates into one inclusive span.

    The client's schema asks for inclusive dates, which is a min/max over the
    whole document -- the single clearest reason a sampled read cannot serve this
    schema, since one unread page can move either end.
    """
    years = []
    for value in values:
        years.extend(int(y) for y in _YEAR.findall(str(value or "")))
    if not years:
        # No parseable year: keep the longest raw string rather than losing it.
        raw = [str(v).strip() for v in values if str(v or "").strip()]
        return max(raw, key=len) if raw else None

    low, high = min(years), max(years)
    return str(low) if low == high else f"{low}/{high}"


def reduce_windows(window_fields: list[dict]) -> dict:
    """Combine per-window field dicts into one description.

    SCOPE CONTENT is left to the caller: it is the one tag that needs a second
    model call (a summary of the window summaries) rather than a set operation.
    """
    by_tag: dict[str, list[dict]] = {}
    for fields in window_fields:
        for code, blob in (fields or {}).items():
            by_tag.setdefault(code, []).append(blob)

    out: dict[str, dict] = {}
    for code, blobs in by_tag.items():
        tag = at.BY_CODE.get(code)
        if tag is None:
            continue

        confidences = [b.get("confidence") for b in blobs]
        if tag.kind == at.KIND_ENTITIES:
            value = merge_entities([b.get("value") or [] for b in blobs])
        elif tag.kind == at.KIND_RELATIONS:
            value = merge_relations([b.get("value") or [] for b in blobs])
        elif code == "DATE":
            value = merge_dates([b.get("value") for b in blobs])
        else:
            # Text and prose: the most confident window wins. Earlier windows
            # break ties, which favours the title page for TITLE and CREATOR.
            best = max(
                range(len(blobs)),
                key=lambda i: (
                    (
                        blobs[i].get("confidence")
                        if isinstance(blobs[i].get("confidence"), (int, float))
                        else -1
                    ),
                    -i,
                ),
            )
            value = blobs[best].get("value")

        if at.is_empty(value):
            continue

        sources = {b.get("source") for b in blobs if b.get("source")}
        out[code] = {
            "value": value,
            "confidence": mean(confidences),
            "source": (sources.pop() if len(sources) == 1 else at.SOURCE_RECORD),
            "windows": len(blobs),
        }
    return out


def run_metrics(window_stats: list[dict], fields: dict, pages_total: int) -> dict:
    """Roll per-window metrics up to the document."""
    confidences = [blob.get("confidence") for blob in (fields or {}).values()]
    spans = sum(w.get("evidence_spans", 0) or 0 for w in window_stats)
    verified = sum(w.get("evidence_verified", 0) or 0 for w in window_stats)
    ocr = [w.get("source_ocr_confidence") for w in window_stats]

    return {
        "fields_filled": len(fields or {}),
        "fields_total": len(at.TAGS),
        "pages_total": pages_total,
        "avg_field_confidence": mean(confidences),
        "min_field_confidence": minimum(confidences),
        "low_conf_field_count": sum(
            1 for c in confidences if isinstance(c, (int, float)) and c < LOW_CONFIDENCE
        ),
        "evidence_spans": spans,
        "evidence_verified": verified,
        "evidence_verified_rate": (verified / spans) if spans else None,
        # NULL when no window had any OCR confidence to average.
        "avg_source_ocr_confidence": mean(ocr),
        "pages_without_confidence": sum(
            w.get("pages_without_confidence", 0) or 0 for w in window_stats
        ),
        "total_prompt_tokens": sum(
            w.get("prompt_tokens", 0) or 0 for w in window_stats
        ),
        "total_completion_tokens": sum(
            w.get("completion_tokens", 0) or 0 for w in window_stats
        ),
        "total_engine_latency_ms": sum(
            w.get("engine_latency_ms", 0) or 0 for w in window_stats
        ),
    }
