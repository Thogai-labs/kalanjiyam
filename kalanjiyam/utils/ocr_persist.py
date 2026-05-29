"""Persist OCR results on pages and build API payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kalanjiyam import database as db
from kalanjiyam.utils.ocr_types import OcrResponse, serialize_bounding_boxes
from kalanjiyam.utils.page_document import PageDocument, normalize_geometry


def image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return int(img.size[0]), int(img.size[1])
    except Exception:
        return None


def apply_ocr_to_page(
    page: db.Page,
    ocr: OcrResponse,
    engine: str,
    *,
    image_path: Path | None = None,
) -> PageDocument:
    """Update page geometry/boxes from OCR response."""
    image_w = image_h = None
    if image_path:
        size = image_size(image_path)
        if size:
            image_w, image_h = size

    boxes, blocks_data, pw, ph = normalize_geometry(
        ocr.bounding_boxes,
        ocr.blocks,
        ocr_width=ocr.page_width,
        ocr_height=ocr.page_height,
        image_width=image_w or page.page_width,
        image_height=image_h or page.page_height,
    )
    page.ocr_bounding_boxes = serialize_bounding_boxes(engine, boxes)
    if pw:
        page.page_width = int(pw)
    elif image_w:
        page.page_width = image_w
    if ph:
        page.page_height = int(ph)
    elif image_h:
        page.page_height = image_h

    normalized = OcrResponse(
        text_content=ocr.text_content,
        bounding_boxes=boxes,
        layout_html=ocr.layout_html,
        blocks=blocks_data if blocks_data is not None else ocr.blocks,
        content_format=ocr.content_format,
        page_width=pw or ocr.page_width or image_w,
        page_height=ph or ocr.page_height or image_h,
        pipeline=ocr.pipeline,
    )
    return PageDocument.from_ocr_response(
        normalized,
        image_width=pw or image_w,
        image_height=ph or image_h,
    )


def ocr_response_to_api_dict(
    ocr: OcrResponse,
    engine: str,
    *,
    image_width: int | None = None,
    image_height: int | None = None,
) -> dict[str, Any]:
    """JSON-serializable OCR result for the editor API."""
    boxes, blocks_data, pw, ph = normalize_geometry(
        ocr.bounding_boxes,
        ocr.blocks,
        ocr_width=ocr.page_width,
        ocr_height=ocr.page_height,
        image_width=image_width,
        image_height=image_height,
    )
    normalized = OcrResponse(
        text_content=ocr.text_content,
        bounding_boxes=boxes,
        layout_html=ocr.layout_html,
        blocks=blocks_data if blocks_data is not None else ocr.blocks,
        content_format=ocr.content_format,
        page_width=pw or ocr.page_width,
        page_height=ph or ocr.page_height,
        pipeline=ocr.pipeline,
    )
    doc = PageDocument.from_ocr_response(
        normalized,
        image_width=pw or image_width,
        image_height=ph or image_height,
    )
    return {
        "text": doc.to_plain_text() or ocr.text_content,
        "bounding_boxes": serialize_bounding_boxes(engine, boxes),
        "layout_html": ocr.layout_html,
        "content_format": doc.content_format,
        "page_width": doc.page_width,
        "page_height": doc.page_height,
        "pipeline": doc.pipeline,
        "blocks": [b.to_dict() for b in doc.blocks],
        "document": doc.to_dict(),
    }
