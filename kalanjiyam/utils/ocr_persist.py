"""Persist OCR results on pages and build API payloads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kalanjiyam import database as db
from kalanjiyam.utils.ocr_types import OcrResponse, serialize_bounding_boxes
from kalanjiyam.utils.page_document import PageDocument


def _image_size(path: Path) -> tuple[int, int] | None:
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
    page.ocr_bounding_boxes = serialize_bounding_boxes(engine, ocr.bounding_boxes)
    if ocr.page_width:
        page.page_width = int(ocr.page_width)
    if ocr.page_height:
        page.page_height = int(ocr.page_height)
    if image_path and (not page.page_width or not page.page_height):
        size = _image_size(image_path)
        if size:
            page.page_width = page.page_width or size[0]
            page.page_height = page.page_height or size[1]
    return PageDocument.from_ocr_response(ocr)


def ocr_response_to_api_dict(ocr: OcrResponse, engine: str) -> dict[str, Any]:
    """JSON-serializable OCR result for the editor API."""
    doc = PageDocument.from_ocr_response(ocr)
    return {
        "text": doc.to_plain_text() or ocr.text_content,
        "bounding_boxes": serialize_bounding_boxes(engine, ocr.bounding_boxes),
        "layout_html": ocr.layout_html,
        "content_format": doc.content_format,
        "page_width": doc.page_width,
        "page_height": doc.page_height,
        "pipeline": doc.pipeline,
        "blocks": [b.to_dict() for b in doc.blocks],
        "document": doc.to_dict(),
    }
