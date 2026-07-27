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
        project = None

    doc = enrich_document_from_page_ocr(PageDocument.empty(), FakePage())
    assert len(doc.blocks) == 1
    assert doc.blocks[0].bbox == [0, 0, 200, 20]


def test_table_blocks_not_merged_into_paragraphs():
    """Line-level reclustering must not destroy OCR table blocks."""
    lines = [
        {
            "id": f"b{i}",
            "type": "table" if i == 0 else "paragraph",
            "bbox": [10, 10 + i * 30, 500, 35 + i * 30],
            "content": "A\tB\n1\t2" if i == 0 else f"line {i}",
            "reading_order": i + 1,
        }
        for i in range(8)
    ]
    ocr = OcrResponse(
        text_content="",
        bounding_boxes=[(10, 10, 500, 40, "A\tB"), (10, 40, 500, 70, "1\t2")],
        blocks=lines,
        page_width=600,
        page_height=800,
        content_format="blocks",
    )
    doc = PageDocument.from_ocr_response(ocr)
    table_blocks = [b for b in doc.blocks if b.type == "table"]
    assert table_blocks, "expected at least one table block preserved"
    html = doc.to_html(replica=True)
    assert "ocr-detected-table" in html or "<table" in html


def test_normalize_geometry_scales_to_image():
    from kalanjiyam.utils.page_document import normalize_geometry

    boxes = [(0.0, 0.0, 0.5, 0.1, "hello")]
    scaled, _, pw, ph = normalize_geometry(
        boxes,
        None,
        ocr_width=1000,
        ocr_height=1400,
        image_width=1700,
        image_height=2200,
    )
    assert pw == 1700
    assert ph == 2200
    assert scaled[0][2] == 850


def test_block_round_trip_preserves_edit_and_provenance():
    """manually_edited, source, and words must survive save/reload."""
    from kalanjiyam.utils.page_document import Block

    original = {
        "id": "b1",
        "type": "paragraph",
        "bbox": [0, 0, 100, 20],
        "content": "hello",
        "reading_order": 1,
        "confidence": 0.62,
        "language": "sa",
        "manually_edited": True,
        "source": {"engine": "surya", "model": "surya-rec/0.6.1", "ocr_at": "2026-06-10T09:00:00+00:00"},
        "words": [{"text": "hello", "confidence": 0.62, "bbox": [0, 0, 100, 20]}],
    }
    block = Block.from_dict(original)
    assert block.manually_edited is True
    assert block.source["engine"] == "surya"
    assert block.words[0]["confidence"] == 0.62

    d = Block.from_dict(block.to_dict()).to_dict()
    assert d["manually_edited"] is True
    assert d["source"]["model"] == "surya-rec/0.6.1"
    assert d["words"] == original["words"]


def test_block_from_dict_clamps_confidence():
    from kalanjiyam.utils.page_document import Block

    assert Block.from_dict({"content": "x", "confidence": 1.4}).confidence == 1.0
    assert Block.from_dict({"content": "x", "confidence": -0.2}).confidence == 0.0
    assert Block.from_dict({"content": "x", "confidence": "bad"}).confidence is None
    assert Block.from_dict({"content": "x"}).confidence is None


def test_normalize_geometry_scales_word_bboxes():
    from kalanjiyam.utils.page_document import normalize_geometry

    _, blocks, _, _ = normalize_geometry(
        [],
        [
            {
                "id": "b1",
                "bbox": [0, 0, 100, 20],
                "content": "x",
                "words": [{"text": "x", "confidence": 0.9, "bbox": [0, 0, 50, 20]}],
            }
        ],
        ocr_width=100,
        ocr_height=100,
        image_width=200,
        image_height=200,
    )
    assert blocks[0]["bbox"] == [0, 0, 200, 40]
    assert blocks[0]["words"][0]["bbox"] == [0, 0, 100, 40]


def test_from_ocr_response_reclusters_line_blocks_without_bounding_boxes():
    """v2 contract is blocks-only; server must match client paragraph clustering."""
    lines = [
        {
            "id": f"b{i}",
            "type": "paragraph",
            "bbox": [120, 100 + i * 35, 980, 128 + i * 35],
            "content": f"line {i} text here",
            "reading_order": i + 1,
            "confidence": 0.9,
        }
        for i in range(10)
    ]
    ocr = OcrResponse(
        text_content="",
        bounding_boxes=[],
        blocks=lines,
        content_format="blocks",
        page_width=1200,
        page_height=1700,
    )
    doc = PageDocument.from_ocr_response(ocr, image_width=1200, image_height=1700)
    assert len(doc.blocks) < len(lines)
    assert "line 0" in doc.blocks[0].content
    assert "line 9" in doc.blocks[0].content


def test_normalize_geometry_honors_normalized_coordinate_space():
    from kalanjiyam.utils.page_document import normalize_geometry

    _, blocks, pw, ph = normalize_geometry(
        [],
        [
            {
                "id": "b1",
                "bbox": [0.1, 0.2, 0.5, 0.3],
                "content": "hello",
            }
        ],
        ocr_width=1000,
        ocr_height=2000,
        image_width=1000,
        image_height=2000,
        coordinate_space="normalized",
    )
    assert pw == 1000
    assert ph == 2000
    assert blocks[0]["bbox"] == [100, 400, 500, 600]


def test_ocr_response_to_api_dict_stamps_provenance():
    from kalanjiyam.utils.ocr_persist import ocr_response_to_api_dict

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
                "confidence": 0.9,
            }
        ],
        content_format="blocks",
        page_width=1000,
        page_height=1400,
        model={"name": "surya-rec", "version": "0.6.1"},
        page_confidence=0.88,
    )
    payload = ocr_response_to_api_dict(ocr, "surya", image_width=1000, image_height=1400)
    assert payload["page_confidence"] == 0.88
    block = payload["blocks"][0]
    assert block["source"]["engine"] == "surya"
    assert block["source"]["model"] == "surya-rec/0.6.1"
    assert "ocr_at" in block["source"]
    assert "manually_edited" not in block  # fresh OCR is not edited
