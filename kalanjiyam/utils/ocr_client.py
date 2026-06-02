"""HTTP client for the external Kalanjiyam OCR service."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from flask import current_app

from kalanjiyam.utils.ocr_types import OcrResponse
from kalanjiyam.utils.text_utils import normalize_unicode_text

logger = logging.getLogger(__name__)


def _box_from_item(item: dict) -> tuple[float, float, float, float, str] | None:
    """Parse one Surya/OCR box dict (x1/y1 keys or bbox array)."""
    text = str(normalize_unicode_text(item.get("text") or item.get("label") or ""))
    if "x1" in item and "y1" in item and "x2" in item and "y2" in item:
        return (
            float(item["x1"]),
            float(item["y1"]),
            float(item["x2"]),
            float(item["y2"]),
            text,
        )
    bbox = item.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]), text)
    polygon = item.get("polygon")
    if isinstance(polygon, (list, tuple)) and len(polygon) >= 4:
        xs = polygon[0::2]
        ys = polygon[1::2]
        if xs and ys:
            return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)), text)
    return None


def _parse_bounding_boxes(
    blob: str | list | None, engine: str
) -> list[tuple[float, float, float, float, str]]:
    if not blob:
        return []
    items: list | None = None
    if isinstance(blob, list):
        items = blob
    elif isinstance(blob, str):
        trimmed = blob.strip()
        if not trimmed:
            return []
        if trimmed.startswith("["):
            try:
                items = json.loads(trimmed)
            except json.JSONDecodeError:
                return []
        else:
            boxes: list[tuple[float, float, float, float, str]] = []
            for line in trimmed.splitlines():
                parts = line.split("\t")
                if len(parts) >= 5:
                    try:
                        boxes.append(
                            (
                                float(parts[0]),
                                float(parts[1]),
                                float(parts[2]),
                                float(parts[3]),
                                normalize_unicode_text(parts[4]),
                            )
                        )
                    except ValueError:
                        continue
            return boxes
    if not isinstance(items, list):
        return []
    boxes: list[tuple[float, float, float, float, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        parsed = _box_from_item(item)
        if parsed is not None:
            x1, y1, x2, y2, text = parsed
            boxes.append((x1, y1, x2, y2, normalize_unicode_text(text)))
    return boxes


def get_available_engines() -> dict:
    """Ping the OCR service and return which engines are ready.

    Returns a dict with:
      status: "ok" | "unavailable" | "no_engines"
      engines: list of engine name strings
    """
    base_url = current_app.config.get("OCR_SERVICE_URL", "").rstrip("/")
    if not base_url:
        return {"status": "unavailable", "engines": []}

    api_key = current_app.config.get("OCR_SERVICE_API_KEY") or ""
    headers = {"X-API-Key": api_key} if api_key else {}

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{base_url}/v1/engines", headers=headers)
        if response.status_code == 200:
            engines = response.json().get("engines", [])
            status = "ok" if engines else "no_engines"
            return {"status": status, "engines": engines}
        return {"status": "unavailable", "engines": []}
    except Exception:
        return {"status": "unavailable", "engines": []}


def run_ocr_remote(file_path: Path, engine_name: str, language: str) -> OcrResponse:
    base_url = current_app.config.get("OCR_SERVICE_URL", "").rstrip("/")
    if not base_url:
        raise RuntimeError("OCR_SERVICE_URL is not configured")

    url = f"{base_url}/v1/ocr"
    api_key = current_app.config.get("OCR_SERVICE_API_KEY") or ""
    timeout = float(current_app.config.get("OCR_SERVICE_TIMEOUT", 300))

    headers = {"X-API-Key": api_key} if api_key else {}

    logger.info("Calling OCR service engine=%s language=%s url=%s", engine_name, language, url)

    with file_path.open("rb") as image_file:
        files = {"image": (file_path.name, image_file, "image/jpeg")}
        data = {"engine": engine_name, "language": language}
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, files=files, data=data, headers=headers)

    if response.status_code >= 400:
        detail = response.text
        try:
            detail = response.json().get("detail", detail)
        except Exception:
            pass
        raise RuntimeError(f"OCR service error ({response.status_code}): {detail}")

    payload = response.json()
    text = payload.get("text", "") or ""
    boxes = _parse_bounding_boxes(payload.get("bounding_boxes"), engine_name)
    blocks = payload.get("blocks")
    if blocks is not None and not isinstance(blocks, list):
        blocks = None
    return OcrResponse(
        text_content=text,
        bounding_boxes=boxes,
        layout_html=payload.get("layout_html"),
        blocks=blocks,
        content_format=payload.get("content_format") or ("blocks" if blocks else "plain"),
        page_width=payload.get("page_width"),
        page_height=payload.get("page_height"),
        pipeline=payload.get("pipeline") or "standard",
    )
