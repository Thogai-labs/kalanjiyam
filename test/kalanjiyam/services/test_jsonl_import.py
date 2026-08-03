import json

import pytest

from kalanjiyam.services.jsonl_import import (
    ImportValidationError,
    normalize_blocks,
    parse_jsonl_record,
    parse_record_id,
)


def test_parses_one_based_book_page_identifier():
    assert parse_record_id("68296786254908673226483264135655461283↳153") == (
        "68296786254908673226483264135655461283", 153
    )


def test_accepts_legacy_mojibake_identifier_delimiter():
    assert parse_record_id("bookâ†³1") == ("book", 1)


@pytest.mark.parametrize("value", [None, "book", "book↳0", "↳1", "book↳one"])
def test_rejects_invalid_identifier(value):
    with pytest.raises(ImportValidationError):
        parse_record_id(value)


def test_double_parses_generated_text_and_normalizes_blocks():
    record = {
        "id": "book↳1",
        "generated_text": json.dumps([{"bbox": [1, 2, 3, 4], "category": "Page-header", "text": "Header"}]),
    }
    book, number, blocks = parse_jsonl_record(json.dumps(record))
    assert (book, number) == ("book", 1)
    assert blocks[0]["type"] == "running-header"
    assert blocks[0]["reading_order"] == 1
    assert blocks[0]["children"] == []


def test_unknown_category_is_safe_paragraph():
    assert normalize_blocks([{"bbox": [0, 0, 1, 1], "category": "unknown", "text": "x"}], "b", 1)[0]["type"] == "paragraph"


@pytest.mark.parametrize("line", ["{", json.dumps({"id": "book↳1", "generated_text": "{"})])
def test_rejects_malformed_jsonl(line):
    with pytest.raises(ImportValidationError):
        parse_jsonl_record(line)
