"""Tests for PageDocument utilities."""

from kalanjiyam.utils.ocr_types import OcrResponse
from kalanjiyam.utils.page_document import PageDocument, find_block_for_bbox


def test_from_ocr_response_with_blocks():
    ocr = OcrResponse(
        text_content="hello",
        bounding_boxes=[],
        blocks=[
            {
                "id": "b1",
                "type": "paragraph",
                "bbox": [0, 0, 100, 20],
                "content": "hello",
                "reading_order": 1,
            }
        ],
        content_format="blocks",
        page_width=1000,
        page_height=1400,
        pipeline="vlm",
    )
    doc = PageDocument.from_ocr_response(ocr)
    assert len(doc.blocks) == 1
    assert doc.to_plain_text() == "hello"
    assert "hello" in doc.to_tei_fragment()


def test_blocks_from_bounding_boxes():
    ocr = OcrResponse(
        text_content="",
        bounding_boxes=[(0, 0, 50, 10, "word"), (60, 0, 120, 10, "two")],
    )
    doc = PageDocument.from_ocr_response(ocr)
    assert len(doc.blocks) >= 1


def test_legacy_content_wrap():
    doc = PageDocument.from_legacy_content("line one\n\nline two")
    assert "line one" in doc.to_plain_text()
    assert "line two" in doc.to_plain_text()


def test_find_block_for_bbox():
    blocks = [
        PageDocument.from_dict(
            {
                "blocks": [
                    {
                        "id": "b1",
                        "type": "paragraph",
                        "bbox": [0, 0, 100, 50],
                        "content": "x",
                        "reading_order": 1,
                    }
                ]
            }
        ).blocks[0]
    ]
    hit = find_block_for_bbox(blocks, [10, 10, 90, 40])
    assert hit is not None
    assert hit.id == "b1"


def test_enrich_document_from_surya_boxes():
    from kalanjiyam.utils.page_document import enrich_document_from_page_ocr

    class FakePage:
        ocr_bounding_boxes = (
            '[{"x1": 0, "y1": 0, "x2": 100, "y2": 20, "text": "word one"},'
            ' {"x1": 110, "y1": 0, "x2": 200, "y2": 20, "text": "two"}]'
        )
        page_width = 1000
        page_height = 1400

    doc = enrich_document_from_page_ocr(PageDocument.empty(), FakePage())
    assert len(doc.blocks) == 2
    assert doc.blocks[0].bbox == [0, 0, 100, 20]
