"""Tests for structure_extract.py.

Covers:
- JSON parsing and validation (schema correctness, hierarchy, Unicode)
- CSV serialisation (escaping, UTF-8 BOM, formula injection mitigation)
- Error paths (empty input, bad JSON, unknown service)
- _normalise_entry edge cases

No Flask app context is required — all tested functions are pure Python.
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from kalanjiyam.utils.structure_extract import (
    CSV_COLUMNS,
    ExtractionError,
    VALID_ENTRY_TYPES,
    _normalise_entry,
    _strip_fences,
    assign_stable_ids,
    to_csv_bytes,
    validate_entries,
)


# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def _entry(**kwargs):
    """Build a minimal valid raw entry dict."""
    base = {
        "entry_id": "e1",
        "parent_entry_id": None,
        "level": 1,
        "entry_type": "chapter",
        "heading": "Introduction",
        "description": None,
        "author": None,
        "page_start": 1,
        "page_end": None,
        "page_label_raw": "1",
        "source_pdf_page": None,
        "raw_ocr_text": "Introduction 1",
        "needs_review": False,
    }
    base.update(kwargs)
    return base


# ────────────────────────────────────────────────────────────────
# _strip_fences
# ────────────────────────────────────────────────────────────────

def test_strip_fences_removes_json_fence():
    raw = "```json\n[{\"a\": 1}]\n```"
    assert "```" not in _strip_fences(raw)
    assert "[{" in _strip_fences(raw)


def test_strip_fences_leaves_plain_json():
    raw = '[{"a": 1}]'
    assert _strip_fences(raw) == raw


# ────────────────────────────────────────────────────────────────
# validate_entries — happy path
# ────────────────────────────────────────────────────────────────

def test_validate_entries_parses_valid_json():
    raw = json.dumps([_entry()])
    entries = validate_entries(raw)
    assert len(entries) == 1
    assert entries[0]["entry_id"] == "e1"
    assert entries[0]["entry_type"] == "chapter"


def test_validate_entries_empty_array_is_ok():
    entries = validate_entries("[]")
    assert entries == []


def test_validate_entries_fills_missing_optional_fields():
    # Only mandatory fields supplied; optional ones should be filled as None.
    raw = json.dumps([{"entry_id": "e1", "entry_type": "part", "level": 1}])
    entries = validate_entries(raw)
    assert entries[0]["heading"] is None
    assert entries[0]["author"] is None
    assert entries[0]["needs_review"] is False


def test_validate_entries_strips_fences_automatically():
    raw = "```json\n" + json.dumps([_entry()]) + "\n```"
    entries = validate_entries(raw)
    assert len(entries) == 1


def test_validate_entries_tolerates_prose_around_array():
    raw = "Here are the entries:\n" + json.dumps([_entry()]) + "\nDone."
    entries = validate_entries(raw)
    assert len(entries) == 1


# ────────────────────────────────────────────────────────────────
# validate_entries — error paths
# ────────────────────────────────────────────────────────────────

def test_validate_entries_raises_on_empty():
    with pytest.raises(ExtractionError, match="empty"):
        validate_entries("")


def test_validate_entries_raises_on_malformed_json():
    with pytest.raises(ExtractionError, match="valid JSON"):
        validate_entries("[{broken json")


def test_validate_entries_raises_on_non_array():
    with pytest.raises(ExtractionError, match="array"):
        validate_entries('{"entries": []}')


def test_validate_entries_raises_on_non_object_element():
    with pytest.raises(ExtractionError, match="not an object"):
        validate_entries('["just a string"]')


# ────────────────────────────────────────────────────────────────
# _normalise_entry
# ────────────────────────────────────────────────────────────────

def test_normalise_entry_generates_id_when_missing():
    entry = _normalise_entry({"entry_type": "chapter", "level": 1}, 0)
    assert entry["entry_id"] == "e1"  # index 0 → "e1"


def test_normalise_entry_clamps_level_to_minimum_1():
    entry = _normalise_entry({"level": -5, "entry_type": "chapter"}, 0)
    assert entry["level"] == 1


def test_normalise_entry_maps_unknown_type_to_other():
    entry = _normalise_entry({"entry_type": "nonsense_type"}, 0)
    assert entry["entry_type"] == "other"


def test_normalise_entry_accepts_all_valid_types():
    for et in VALID_ENTRY_TYPES:
        entry = _normalise_entry({"entry_type": et}, 0)
        assert entry["entry_type"] == et


def test_normalise_entry_converts_page_numbers_to_int():
    entry = _normalise_entry({"page_start": "23", "page_end": "25.0"}, 0)
    assert entry["page_start"] == 23
    assert entry["page_end"] == 25


def test_normalise_entry_nulls_unparseable_page_numbers():
    entry = _normalise_entry({"page_start": "xiv"}, 0)
    assert entry["page_start"] is None


# ────────────────────────────────────────────────────────────────
# Hierarchy: parent_entry_id validation
# ────────────────────────────────────────────────────────────────

def test_validate_entries_flags_dangling_parent():
    raw = json.dumps([
        _entry(entry_id="e1", parent_entry_id=None),
        _entry(entry_id="e2", parent_entry_id="nonexistent"),
    ])
    entries = validate_entries(raw)
    # e2 has a dangling parent → should be flagged for review
    assert entries[1]["needs_review"] is True


def test_validate_entries_valid_hierarchy_not_flagged():
    raw = json.dumps([
        _entry(entry_id="e1", parent_entry_id=None, level=1),
        _entry(entry_id="e2", parent_entry_id="e1", level=2, heading="Child"),
    ])
    entries = validate_entries(raw)
    assert entries[1]["needs_review"] is False
    assert entries[1]["parent_entry_id"] == "e1"


# ────────────────────────────────────────────────────────────────
# Unicode / diacritics preservation
# ────────────────────────────────────────────────────────────────

def test_validate_entries_preserves_unicode_heading():
    heading = "Rāmāyaṇa — Part I (ராமாயணம்)"
    raw = json.dumps([_entry(heading=heading)])
    entries = validate_entries(raw)
    assert entries[0]["heading"] == heading


def test_validate_entries_preserves_roman_numeral_page_label():
    raw = json.dumps([_entry(page_label_raw="xii–xiv")])
    entries = validate_entries(raw)
    assert entries[0]["page_label_raw"] == "xii–xiv"


# ────────────────────────────────────────────────────────────────
# assign_stable_ids
# ────────────────────────────────────────────────────────────────

def test_assign_stable_ids_generates_ids_for_blanks():
    entries = [{"entry_id": "", "parent_entry_id": None}]
    result = assign_stable_ids(entries)
    assert result[0]["entry_id"].startswith("e-")


def test_assign_stable_ids_preserves_existing_ids():
    entries = [{"entry_id": "e1", "parent_entry_id": None}]
    result = assign_stable_ids(entries)
    assert result[0]["entry_id"] == "e1"


def test_assign_stable_ids_fixes_parent_refs_when_id_generated():
    entries = [
        {"entry_id": "", "parent_entry_id": None},
        {"entry_id": "e2", "parent_entry_id": ""},  # references the blank parent
    ]
    result = assign_stable_ids(entries)
    parent_id = result[0]["entry_id"]
    assert result[1]["parent_entry_id"] == parent_id


# ────────────────────────────────────────────────────────────────
# to_csv_bytes — structure and escaping
# ────────────────────────────────────────────────────────────────

def _parse_csv(csv_bytes: bytes) -> list[list[str]]:
    """Strip BOM and parse CSV bytes to rows."""
    text = csv_bytes.decode("utf-8-sig")  # utf-8-sig strips BOM automatically
    return list(csv.reader(io.StringIO(text)))


def test_to_csv_bytes_has_bom():
    data = to_csv_bytes([_entry()], document_id="doc1")
    assert data[:3] == b"\xef\xbb\xbf"


def test_to_csv_bytes_header_matches_columns():
    data = to_csv_bytes([], document_id="doc1")
    rows = _parse_csv(data)
    assert rows[0] == CSV_COLUMNS


def test_to_csv_bytes_one_row_per_entry():
    entries = [_entry(entry_id="e1"), _entry(entry_id="e2", heading="Part II")]
    data = to_csv_bytes(entries, document_id="42")
    rows = _parse_csv(data)
    assert len(rows) == 3  # header + 2 data rows


def test_to_csv_bytes_document_id_in_first_column():
    data = to_csv_bytes([_entry()], document_id="doc-99")
    rows = _parse_csv(data)
    assert rows[1][0] == "doc-99"


def test_to_csv_bytes_escapes_commas_in_heading():
    entry = _entry(heading="Part I, Chapter 1")
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    assert rows[1][CSV_COLUMNS.index("heading")] == "Part I, Chapter 1"


def test_to_csv_bytes_escapes_internal_quotes():
    entry = _entry(heading='He said "hello"')
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    assert rows[1][CSV_COLUMNS.index("heading")] == 'He said "hello"'


def test_to_csv_bytes_multiline_raw_ocr_text():
    entry = _entry(raw_ocr_text="line one\nline two\nline three")
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    assert "line one" in rows[1][CSV_COLUMNS.index("raw_ocr_text")]


def test_to_csv_bytes_unicode_diacritics_preserved():
    entry = _entry(heading="Rāmāyaṇa", author="Vālmīki")
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    assert rows[1][CSV_COLUMNS.index("heading")] == "Rāmāyaṇa"
    assert rows[1][CSV_COLUMNS.index("author")] == "Vālmīki"


def test_to_csv_bytes_null_values_are_empty_strings():
    entry = _entry(author=None, description=None)
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    assert rows[1][CSV_COLUMNS.index("author")] == ""
    assert rows[1][CSV_COLUMNS.index("description")] == ""


def test_to_csv_bytes_formula_injection_mitigated():
    """Cell values starting with = must not be able to trigger spreadsheet macros."""
    entry = _entry(heading="=SUM(A1:A10)")
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    cell = rows[1][CSV_COLUMNS.index("heading")]
    assert not cell.startswith("="), f"Formula injection not mitigated: {cell!r}"
    assert "SUM" in cell  # content preserved, just prefixed


def test_to_csv_bytes_empty_entries_gives_header_only():
    data = to_csv_bytes([], document_id="d")
    rows = _parse_csv(data)
    assert len(rows) == 1
    assert rows[0] == CSV_COLUMNS


def test_to_csv_bytes_needs_review_field():
    entry = _entry(needs_review=True)
    data = to_csv_bytes([entry], document_id="d")
    rows = _parse_csv(data)
    assert rows[1][CSV_COLUMNS.index("needs_review")] == "True"


# ────────────────────────────────────────────────────────────────
# Truncation & Chunking tests
# ────────────────────────────────────────────────────────────────

def test_validate_entries_repairs_truncated_json_array():
    """Simulate exact error when model generation is cut off mid-string/object."""
    truncated_raw = (
        '[\n'
        '  {"entry_id": "e1", "parent_entry_id": null, "level": 1, "entry_type": "heading", "heading": "CONTENTS"},\n'
        '  {"entry_id": "e2", "parent_entry_id": "e1", "level": 2, "entry_type": "chapter", "heading": "XXI. EDUCATION IN THE FIFTH CENTURY A.D.",\n'
    )
    entries = validate_entries(truncated_raw)
    assert len(entries) == 1
    assert entries[0]["entry_id"] == "e1"
    assert entries[0]["heading"] == "CONTENTS"
    assert entries[0]["needs_review"] is True  # flagged because truncation occurred


def test_validate_entries_repairs_multi_entry_truncation():
    truncated_raw = (
        '[\n'
        '  {"entry_id": "e1", "level": 1, "heading": "Part I", "entry_type": "part"},\n'
        '  {"entry_id": "e2", "level": 2, "heading": "Chapter 1", "entry_type": "chapter"},\n'
        '  {"entry_id": "e3", "level": 2, "heading": "Chapter 2", "raw_ocr_text": "Chap'
    )
    entries = validate_entries(truncated_raw)
    assert len(entries) == 2
    assert entries[0]["heading"] == "Part I"
    assert entries[1]["heading"] == "Chapter 1"


def test_split_into_chunks():
    from kalanjiyam.utils.structure_extract import _split_into_chunks

    # Short text remains a single chunk
    short = "Line 1\nLine 2\nLine 3"
    assert len(_split_into_chunks(short, max_lines=10)) == 1

    # Text exceeding line limit splits cleanly
    lines = [f"Entry line {i}" for i in range(1, 150)]
    long_text = "\n".join(lines)
    chunks = _split_into_chunks(long_text, max_lines=50)
    assert len(chunks) == 3
    assert "".join(chunks) == long_text
