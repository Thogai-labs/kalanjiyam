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


def test_import_summary_skipped_books_default():
    from kalanjiyam.services.jsonl_import import ImportSummary

    summary = ImportSummary()
    assert summary.skipped_books == 0


def test_import_summary_skipped_books_default():
    from kalanjiyam.services.jsonl_import import ImportSummary

    summary = ImportSummary()
    assert summary.skipped_books == 0


def test_run_import_skips_existing_books_when_allow_duplicate_false():
    from unittest.mock import MagicMock
    from kalanjiyam.services.jsonl_import import run_import
    from kalanjiyam.models.group import Group
    import kalanjiyam.database as db

    session = MagicMock()
    mock_group = MagicMock(id=1, slug="my-org")
    # Existing project has mixed case
    mock_project = MagicMock(source_book_id="Book_ABC", display_title="Book_ABC", slug="import-book_abc")

    # Setup session queries
    def query_side_effect(model):
        q = MagicMock()
        if model == Group:
            q.filter_by.return_value.first.return_value = mock_group
        elif model == db.Project:
            q.join.return_value.filter.return_value.all.return_value = [mock_project]
        return q

    session.query.side_effect = query_side_effect

    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": "imports/book_abc.jsonl"}]}
    ]
    mock_s3.get_paginator.return_value = paginator

    # Discovered record has lowercase "book_abc" to test case-insensitivity
    record = json.dumps({
        "id": "book_abc↳1",
        "generated_text": json.dumps([{"bbox": [0, 0, 1, 1], "category": "text", "text": "Hello"}]),
    })
    mock_s3.get_object.return_value = {
        "Body": MagicMock(iter_lines=lambda: [record.encode("utf-8")])
    }

    # Test default allow_duplicate=False
    summary = run_import(
        session,
        jsonl_uri="s3://bucket/imports/",
        pdf_uri="s3://bucket/pdfs/",
        org_slug="my-org",
        dry_run=True,
        client=mock_s3,
    )

    assert summary.books == 1
    assert summary.skipped_books == 1
    assert summary.importable_books == 0


def test_run_import_allows_duplicate_books_when_allow_duplicate_true():
    from unittest.mock import MagicMock
    from kalanjiyam.services.jsonl_import import run_import
    from kalanjiyam.models.group import Group
    import kalanjiyam.database as db

    session = MagicMock()
    mock_group = MagicMock(id=1, slug="my-org")
    mock_project = MagicMock(source_book_id="book1", display_title="book1", slug="import-book1")

    def query_side_effect(model):
        q = MagicMock()
        if model == Group:
            q.filter_by.return_value.first.return_value = mock_group
        elif model == db.Project:
            q.join.return_value.filter.return_value.all.return_value = [mock_project]
        return q

    session.query.side_effect = query_side_effect

    mock_s3 = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": "imports/book1.jsonl"}]}
    ]
    mock_s3.get_paginator.return_value = paginator

    record = json.dumps({
        "id": "book1↳1",
        "generated_text": json.dumps([{"bbox": [0, 0, 1, 1], "category": "text", "text": "Hello"}]),
    })
    mock_s3.get_object.return_value = {
        "Body": MagicMock(iter_lines=lambda: [record.encode("utf-8")])
    }

    # pdf_index returns a match for book1
    mock_s3.get_object.return_value = {
        "Body": MagicMock(iter_lines=lambda: [record.encode("utf-8")])
    }

    summary = run_import(
        session,
        jsonl_uri="s3://bucket/imports/",
        pdf_uri="s3://bucket/pdfs/",
        org_slug="my-org",
        dry_run=True,
        allow_duplicate=True,
        client=mock_s3,
    )

    assert summary.books == 1
    assert summary.skipped_books == 0


