"""HTTP client for the external Kalanjiyam OCR service."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
from flask import current_app

from kalanjiyam.utils.ocr_types import OcrResponse

logger = logging.getLogger(__name__)


def _parse_bounding_boxes(blob: str | None, engine: str) -> list[tuple[int, int, int, int, str]]:
    if not blob:
        return []
    if engine == "surya":
        try:
            items = json.loads(blob)
        except json.JSONDecodeError:
            return []
        return [
            (item["x1"], item["y1"], item["x2"], item["y2"], item["text"])
            for item in items
            if isinstance(item, dict) and "text" in item
        ]
    boxes: list[tuple[int, int, int, int, str]] = []
    for line in blob.splitlines():
        parts = line.split("\t")
        if len(parts) >= 5:
            try:
                boxes.append((int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), parts[4]))
            except ValueError:
                continue
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
    return OcrResponse(
        text_content=payload.get("text", ""),
        bounding_boxes=_parse_bounding_boxes(payload.get("bounding_boxes"), engine_name),
    )
