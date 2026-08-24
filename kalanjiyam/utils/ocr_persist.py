"""Persist OCR results on pages and build API payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kalanjiyam import database as db
from kalanjiyam.utils.ocr_types import OcrResponse, serialize_bounding_boxes
from kalanjiyam.utils.page_document import PageDocument, normalize_geometry


def _stamp_provenance(doc: PageDocument, engine: str, model: dict | None) -> None:
    """Record which engine/model produced each block and when.

    Engines never send `source` themselves (platform-owned, see
    docs/ocr-service-contract.rst), so a fresh OCR run stamps every block.
    """
    source: dict[str, Any] = {
        "engine": engine,
        "ocr_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if model and model.get("name"):
        version = model.get("version")
        source["model"] = f"{model['name']}/{version}" if version else str(model["name"])
    for block in doc.blocks:
        block.source = dict(source)
        block.manually_edited = False


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
        coordinate_space=ocr.coordinate_space,
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
        source_type=ocr.source_type,
        coordinate_space="pixel",
        model=ocr.model,
        contract_version=ocr.contract_version,
    )
    doc = PageDocument.from_ocr_response(
        normalized,
        image_width=pw or image_w,
        image_height=ph or image_h,
    )
    _stamp_provenance(doc, engine, ocr.model)
    return doc


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
        coordinate_space=ocr.coordinate_space,
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
        coordinate_space="pixel",
        contract_version=ocr.contract_version,
    )
    doc = PageDocument.from_ocr_response(
        normalized,
        image_width=pw or image_width,
        image_height=ph or image_height,
    )
    _stamp_provenance(doc, engine, ocr.model)
    blocks_count = ocr.blocks_count if ocr.blocks_count is not None else len(doc.blocks)
    chars_count = ocr.chars_count if ocr.chars_count is not None else len(doc.to_plain_text() or "")
    result: dict[str, Any] = {
        "engine": engine or ocr.engine,
        "source_type": ocr.source_type,
        "page_width": doc.page_width,
        "page_height": doc.page_height,
        "coordinate_space": "pixel",
        "page_confidence": ocr.page_confidence,
        "confidence": ocr.page_confidence,
        "p05": ocr.p05,
        "blocks_count": blocks_count,
        "chars_count": chars_count,
        "engine_latency_ms": ocr.engine_latency_ms,
        "blocks": [b.to_dict() for b in doc.blocks],
    }
    if ocr.contract_version:
        result["contract_version"] = ocr.contract_version
    if ocr.model:
        result["model"] = ocr.model
    if getattr(ocr, "ocr_mode", "standard") == "enhanced":
        result["ocr_mode"] = "enhanced"
        version_str = getattr(ocr, "enhancement_version", "1.0") or "1.0"
        result["enhancement_version"] = version_str
        if getattr(ocr, "enhancement_profile", None):
            result["enhancement"] = {
                "profile": ocr.enhancement_profile,
                "version": version_str,
            }
            result["preprocessing"] = {
                "profile": ocr.enhancement_profile,
                "version": version_str,
            }
        if getattr(ocr, "preprocessing_latency_ms", None) is not None:
            result["preprocessing_latency_ms"] = ocr.preprocessing_latency_ms
    return result
