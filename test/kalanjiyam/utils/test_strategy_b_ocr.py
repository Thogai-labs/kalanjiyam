import json
from unittest.mock import MagicMock

from kalanjiyam.utils.document_storage import (
    _derive_bounding_boxes_from_document,
    load_page_ocr,
)


def test_derive_bounding_boxes_from_document_block_level():
    doc_dict = {
        "page_width": 800,
        "page_height": 1000,
        "blocks": [
            {
                "id": "b1",
                "type": "paragraph",
                "bbox": [10, 20, 100, 50],
                "content": "First line text",
            },
            {
                "id": "b2",
                "type": "heading",
                "bbox": [10, 60, 200, 90],
                "content": "Second line heading",
            },
        ],
    }
    raw_json = _derive_bounding_boxes_from_document(doc_dict)
    assert raw_json is not None
    boxes = json.loads(raw_json)
    assert len(boxes) == 2
    assert boxes[0] == {"x1": 10.0, "y1": 20.0, "x2": 100.0, "y2": 50.0, "text": "First line text"}
    assert boxes[1] == {"x1": 10.0, "y1": 60.0, "x2": 200.0, "y2": 90.0, "text": "Second line heading"}


def test_derive_bounding_boxes_from_document_word_level():
    doc_dict = {
        "blocks": [
            {
                "id": "b1",
                "type": "paragraph",
                "bbox": [0, 0, 0, 0],
                "content": "Hello World",
                "words": [
                    {"text": "Hello", "bbox": [10, 20, 50, 40]},
                    {"text": "World", "bbox": [55, 20, 95, 40]},
                ],
            }
        ]
    }
    raw_json = _derive_bounding_boxes_from_document(doc_dict)
    assert raw_json is not None
    boxes = json.loads(raw_json)
    assert len(boxes) == 2
    assert boxes[0] == {"x1": 10.0, "y1": 20.0, "x2": 50.0, "y2": 40.0, "text": "Hello"}
    assert boxes[1] == {"x1": 55.0, "y1": 20.0, "x2": 95.0, "y2": 40.0, "text": "World"}


def test_load_page_ocr_derives_from_latest_revision(monkeypatch):
    mock_page = MagicMock()
    mock_rev = MagicMock()
    mock_page.revisions = [mock_rev]
    mock_page.slug = "page-1"

    doc_dict = {
        "blocks": [
            {"id": "1", "type": "paragraph", "bbox": [5, 10, 50, 30], "content": "Sample"}
        ]
    }

    monkeypatch.setattr(
        "kalanjiyam.utils.document_storage.load_revision_document",
        lambda rev: doc_dict,
    )

    result = load_page_ocr(mock_page)
    assert result is not None
    boxes = json.loads(result)
    assert len(boxes) == 1
    assert boxes[0]["text"] == "Sample"
    assert boxes[0]["x1"] == 5.0


def test_load_page_ocr_fallback_to_legacy_column():
    mock_page = MagicMock()
    mock_page.revisions = []
    mock_page.project = None
    mock_page.ocr_bounding_boxes = '[{"x1": 1, "y1": 2, "x2": 3, "y2": 4, "text": "fallback"}]'

    result = load_page_ocr(mock_page)
    assert result == mock_page.ocr_bounding_boxes
