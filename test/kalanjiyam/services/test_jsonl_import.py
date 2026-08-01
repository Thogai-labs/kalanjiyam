import json

import pytest

from kalanjiyam.services.jsonl_import import (
    ImportValidationError,
    _iter_lines,
    _list_objects,
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


def test_repairs_known_missing_generated_text_quote():
    malformed_nested = (
        '[{"bbox":[0,0,1,1],"category":"Text","text":"first},'
        ' {"bbox":[1,1,2,2],"category":"Text","text":"second"}]'
    )
    _, _, blocks = parse_jsonl_record(json.dumps({"id": "book↳1", "generated_text": malformed_nested}))
    assert [block["content"] for block in blocks] == ["first", "second"]


@pytest.mark.parametrize("line", ["{", json.dumps({"id": "book↳1", "generated_text": "{"})])
def test_rejects_malformed_jsonl(line):
    with pytest.raises(ImportValidationError):
        parse_jsonl_record(line)


def test_discovers_and_streams_local_jsonl_files(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    source = nested / "pages.JSONL"
    source.write_text('{"id": "book↳1"}\n', encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("ignore", encoding="utf-8")

    assert _list_objects(None, str(tmp_path), ".jsonl") == [str(source)]
    assert [line.rstrip() for line in _iter_lines(None, str(source))] == [
        '{"id": "book↳1"}'.encode("utf-8")
    ]
