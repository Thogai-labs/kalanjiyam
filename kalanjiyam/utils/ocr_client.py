"""HTTP client for the external Kalanjiyam OCR service."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx
from flask import current_app

from kalanjiyam.utils.ocr_types import (
    SUPPORTED_ENGINES,
    OcrResponse,
    calculate_p05_confidence,
    engine_for_service,
    normalize_service_engine,
    post_process,
)
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


def _clamp_confidence(value) -> float | None:
    """Coerce a contract confidence value to a float in [0, 1], or None."""
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, score))


def _sanitize_block(block: dict) -> dict:
    """Normalize confidence/words on a contract block (see ocr-service-contract)."""
    item = dict(block)
    if "confidence" in item:
        item["confidence"] = _clamp_confidence(item["confidence"])
    words = item.get("words")
    if isinstance(words, list):
        clean_words = []
        for word in words:
            if not isinstance(word, dict) or not word.get("text"):
                continue
            confidence = _clamp_confidence(word.get("confidence"))
            # The contract requires per-word confidence; geometry-only
            # words are useless to the editor.
            if confidence is None:
                continue
            clean: dict = {"text": str(word["text"]), "confidence": confidence}
            bbox = word.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                clean["bbox"] = [float(x) for x in bbox]
            clean_words.append(clean)
        item["words"] = clean_words or None
    elif "words" in item:
        item["words"] = None
    return item


def _normalize_base_url(url: str) -> str:
    """Normalize OCR base URL by stripping trailing slash and /v1 suffix."""
    u = (url or "").rstrip("/")
    if u.endswith("/v1"):
        return u[:-3]
    return u


def _get_ocr_service_targets() -> list[tuple[str, str]]:
    """Returns list of (base_url, api_key) pairs for primary and fallback OCR services."""
    import os
    targets = []
    url1 = (current_app.config.get("OCR_SERVICE_URL") or os.environ.get("OCR_SERVICE_URL") or "").rstrip("/")
    key1 = current_app.config.get("OCR_SERVICE_API_KEY") or os.environ.get("OCR_SERVICE_API_KEY") or ""
    if url1:
        targets.append((url1, key1))

    url2 = (current_app.config.get("OCR_SERVICE_URL_2") or os.environ.get("OCR_SERVICE_URL_2") or "").rstrip("/")
    key2 = current_app.config.get("OCR_SERVICE_API_KEY_2") or os.environ.get("OCR_SERVICE_API_KEY_2") or key1
    if url2 and url2 != url1:
        targets.append((url2, key2))

    return targets


def get_available_engines() -> dict:
    """Ping the OCR service and return which engines are ready.

    Tries primary OCR_SERVICE_URL first, falling back to OCR_SERVICE_URL_2
    if primary is unreachable or returns an error.

    Returns a dict with:
      status: "ok" | "unavailable" | "no_engines"
      engines: list of engine name strings
    """
    targets = _get_ocr_service_targets()
    if not targets:
        return {"status": "unavailable", "engines": []}

    for base_url, api_key in targets:
        clean_base = _normalize_base_url(base_url)
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        try:
            with httpx.Client(timeout=5.0, trust_env=False) as client:
                response = client.get(f"{clean_base}/v1/engines", headers=headers)
                if response.status_code == 200:
                    raw = response.json().get("engines", [])
                    engines = [normalize_service_engine(e) for e in raw]
                    status = "ok" if engines else "no_engines"
                    return {"status": status, "engines": engines}

                if response.status_code in (404, 405):
                    models_resp = client.get(f"{clean_base}/v1/models", headers=headers)
                    if models_resp.status_code == 200:
                        data = models_resp.json().get("data", [])
                        raw_ids = [m.get("id", "") for m in data if isinstance(m, dict)]
                        engines = []
                        for mid in raw_ids:
                            norm = normalize_service_engine(mid)
                            if norm in SUPPORTED_ENGINES and norm not in engines:
                                engines.append(norm)
                        if engines:
                            return {"status": "ok", "engines": engines}

                logger.warning("OCR service at %s returned status %s for engines ping. Falling back...", base_url, response.status_code)
                continue
        except Exception as ex:
            logger.warning("Failed to ping OCR service at %s: %s. Falling back...", base_url, ex)
            continue

    return {"status": "unavailable", "engines": []}


def _scale_vlm_coords(
    v0: float,
    v1: float,
    v2: float,
    v3: float,
    page_width: int | None,
    page_height: int | None,
    is_ymin_first: bool = False,
) -> list[int]:
    """Convert normalized (0-1 or 0-1000) or pixel coordinates to integer [x1, y1, x2, y2]."""
    if is_ymin_first:
        y0, x0, y1, x1 = v0, v1, v2, v3
    else:
        x0, y0, x1, y1 = v0, v1, v2, v3

    max_c = max(x0, y0, x1, y1)
    if max_c <= 1.5 and page_width and page_height:
        rx1, ry1 = round(x0 * page_width), round(y0 * page_height)
        rx2, ry2 = round(x1 * page_width), round(y1 * page_height)
    elif max_c <= 1000.0 and page_width and page_height:
        rx1 = round(x0 * page_width / 1000.0)
        ry1 = round(y0 * page_height / 1000.0)
        rx2 = round(x1 * page_width / 1000.0)
        ry2 = round(y1 * page_height / 1000.0)
    else:
        rx1, ry1, rx2, ry2 = int(x0), int(y0), int(x1), int(y1)

    bx1, bx2 = min(rx1, rx2), max(rx1, rx2)
    by1, by2 = min(ry1, ry2), max(ry1, ry2)
    return [int(bx1), int(by1), int(bx2), int(by2)]


def _parse_chandra_html(
    html_text: str, page_width: int | None, page_height: int | None
) -> tuple[list[dict] | None, list[tuple[float, float, float, float, str]], str]:
    """Parse layout blocks from Chandra OCR HTML containing data-bbox attributes."""
    if not html_text or "data-bbox" not in html_text:
        return None, [], html_text

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")
        elements = soup.find_all(attrs={"data-bbox": True})
        if not elements:
            return None, [], html_text

        blocks: list[dict] = []
        boxes: list[tuple[float, float, float, float, str]] = []
        text_parts: list[str] = []

        for i, el in enumerate(elements):
            bbox_str = el.get("data-bbox", "").strip()
            parts = [float(p) for p in bbox_str.split() if p]
            if len(parts) != 4:
                continue

            bbox = _scale_vlm_coords(parts[0], parts[1], parts[2], parts[3], page_width, page_height, is_ymin_first=False)
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue

            label = (el.get("data-label") or "").lower()
            if "header" in label or "title" in label:
                btype = "heading"
            elif "subheading" in label:
                btype = "subheading"
            elif "table" in label:
                btype = "table"
            elif any(w in label for w in ("image", "figure", "picture")):
                btype = "figure"
            elif "caption" in label:
                btype = "caption"
            elif "footnote" in label:
                btype = "footnote"
            elif "formula" in label or "equation" in label:
                btype = "equation"
            elif "page-header" in label:
                btype = "running-header"
            elif "page-footer" in label or "page-number" in label:
                btype = "page-number"
            else:
                btype = "paragraph"

            if btype == "table":
                tbl = el.find("table")
                content = str(tbl) if tbl else "".join(str(c) for c in el.children).strip()
            elif btype == "figure":
                img_tag = el.find("img")
                content = str(img_tag) if img_tag else el.get_text().strip()
            else:
                for br in el.find_all(["br", "hr"]):
                    br.replace_with("\n")
                content = el.get_text().strip()

            block_id = f"b{i+1:03d}"
            blocks.append({
                "id": block_id,
                "type": btype,
                "bbox": bbox,
                "reading_order": i + 1,
                "content": content,
                "confidence": 1.0,
            })
            boxes.append((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]), content))
            if content:
                text_parts.append(content)

        if not blocks:
            return None, [], html_text

        full_text = "\n\n".join(text_parts)
        return blocks, boxes, full_text
    except Exception as ex:
        logger.warning("Failed to parse Chandra HTML bboxes: %s", ex)
        return None, [], html_text


def _parse_vlm_json_blocks(
    raw_text: str, page_width: int | None, page_height: int | None
) -> tuple[list[dict] | None, list[tuple[float, float, float, float, str]], str]:
    """Parse JSON blocks containing bbox coordinates from vision LLMs."""
    if not raw_text:
        return None, [], ""

    import re
    trimmed = raw_text.strip()
    if trimmed.startswith("```"):
        lines = trimmed.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        trimmed = "\n".join(lines).strip()

    data = None
    if trimmed.startswith("[") or trimmed.startswith("{"):
        try:
            data = json.loads(trimmed)
        except Exception:
            pass

    if data is None:
        m = re.search(r"(\[\s*\{.*\}\s*\])", trimmed, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
            except Exception:
                pass

    if data is None:
        return None, [], raw_text

    items = data.get("blocks") if isinstance(data, dict) and "blocks" in data else data
    if not isinstance(items, list):
        return None, [], raw_text

    blocks: list[dict] = []
    boxes: list[tuple[float, float, float, float, str]] = []
    text_parts: list[str] = []

    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw_box = (
            item.get("bbox")
            or item.get("box_2d")
            or item.get("box")
        )
        is_ymin_first = True
        if not raw_box and all(k in item for k in ("ymin", "xmin", "ymax", "xmax")):
            raw_box = [item["ymin"], item["xmin"], item["ymax"], item["xmax"]]
            is_ymin_first = True
        elif not raw_box and all(k in item for k in ("x1", "y1", "x2", "y2")):
            raw_box = [item["x1"], item["y1"], item["x2"], item["y2"]]
            is_ymin_first = False

        if not isinstance(raw_box, (list, tuple)) or len(raw_box) < 4:
            continue

        try:
            bbox = _scale_vlm_coords(
                float(raw_box[0]), float(raw_box[1]), float(raw_box[2]), float(raw_box[3]),
                page_width, page_height, is_ymin_first=is_ymin_first,
            )
        except (TypeError, ValueError):
            continue

        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue

        content = str(item.get("text") or item.get("content") or item.get("text_content") or "").strip()
        raw_type = str(item.get("type") or item.get("label") or "paragraph").lower()
        if "head" in raw_type or "title" in raw_type:
            btype = "heading"
        elif "table" in raw_type:
            btype = "table"
        elif any(w in raw_type for w in ("image", "figure", "picture")):
            btype = "figure"
        elif "caption" in raw_type:
            btype = "caption"
        elif "footnote" in raw_type:
            btype = "footnote"
        else:
            btype = "paragraph"

        block_id = f"b{i+1:03d}"
        blocks.append({
            "id": block_id,
            "type": btype,
            "bbox": bbox,
            "reading_order": i + 1,
            "content": content,
            "confidence": 1.0,
        })
        boxes.append((float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]), content))
        if content:
            text_parts.append(content)

    if not blocks:
        return None, [], raw_text

    full_text = "\n\n".join(text_parts)
    return blocks, boxes, full_text


def _run_ocr_chat_completions(
    clean_base: str,
    headers: dict,
    file_path: Path,
    engine_name: str,
    language: str,
    timeout: float,
    start_time: float,
) -> OcrResponse:
    """Fallback runner for vision LLMs (e.g. llm-gemma, chandra) via /v1/chat/completions."""
    import base64

    from PIL import Image

    mime = "image/jpeg"
    page_width = None
    page_height = None
    try:
        with Image.open(file_path) as img:
            page_width, page_height = img.size
            if img.format and img.format.lower() in ("png", "jpeg", "webp"):
                mime = f"image/{img.format.lower()}"
    except Exception as e:
        logger.warning("Could not read image dimensions for %s: %s", file_path, e)

    img_bytes = file_path.read_bytes()
    b64_img = base64.b64encode(img_bytes).decode("utf-8")
    data_url = f"data:{mime};base64,{b64_img}"

    norm = normalize_service_engine(engine_name)
    if norm == "chandra":
        model_id = "chandra"
    elif norm == "gemma_ocr":
        model_id = "llm-gemma"
    elif norm == "dots_ocr":
        model_id = "dots-ocr"
    else:
        model_id = engine_name.replace("_", "-")

    if model_id == "llm-gemma":
        ocr_prompt = (
            "Perform optical character recognition (OCR) on this image. Detect all text blocks and layout elements. "
            "Extract all text verbatim preserving original layout, headings, and line breaks. "
            "Output as a JSON array of objects with keys: \"type\" (paragraph, heading, or table), "
            "\"bbox\" ([ymin, xmin, ymax, xmax] in 0-1000 normalized scale), and \"text\". "
            "Output ONLY valid JSON without any markdown formatting or commentary."
        )
    else:
        ocr_prompt = (
            "Perform optical character recognition (OCR) on this image. "
            "Extract all text verbatim, preserving original layout, headings, and line breaks. "
            "Output ONLY the extracted text. Do not add explanations, conversational comments, or formatting notes."
        )

    chat_url = f"{clean_base}/v1/chat/completions"
    chat_headers = dict(headers)
    chat_headers["Content-Type"] = "application/json"

    chat_payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ocr_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.0,
    }

    with httpx.Client(timeout=timeout, trust_env=False) as client:
        res = client.post(chat_url, headers=chat_headers, json=chat_payload)

    latency_ms = round((time.time() - start_time) * 1000, 2)
    if res.status_code >= 400:
        detail = res.text
        try:
            res_json = res.json()
            detail = res_json.get("detail") or res_json.get("error", {}).get("message") or detail
        except Exception:
            pass

        # If the requested model is not permitted on this virtual key, fall back to llm-gemma
        if res.status_code == 403 and "key not allowed to access model" in str(detail).lower() and model_id != "llm-gemma":
            logger.warning(
                "OCR model %s is not permitted on this API key (%s). Falling back to llm-gemma...",
                model_id,
                detail,
            )
            chat_payload["model"] = "llm-gemma"
            with httpx.Client(timeout=timeout, trust_env=False) as client:
                res = client.post(chat_url, headers=chat_headers, json=chat_payload)
            if res.status_code < 400:
                engine_name = "gemma_ocr"
                model_id = "llm-gemma"
            else:
                raise RuntimeError(f"OCR chat completion error ({res.status_code}): {detail}")
        else:
            raise RuntimeError(f"OCR chat completion error ({res.status_code}): {detail}")

    res_json = res.json()
    choices = res_json.get("choices") or []
    extracted_text = ""
    if choices and isinstance(choices, list):
        msg = choices[0].get("message") or {}
        extracted_text = msg.get("content") or ""

    parsed_blocks, parsed_boxes, clean_text = _parse_chandra_html(extracted_text, page_width, page_height)
    if not parsed_blocks:
        parsed_blocks, parsed_boxes, clean_text = _parse_vlm_json_blocks(extracted_text, page_width, page_height)

    if parsed_blocks:
        extracted_text = clean_text
    else:
        import re
        # Unpack <extract>[{"text": "..."}]</extract> if model (e.g. Chandra) outputs XML wrapper
        if "<extract>" in extracted_text:
            match = re.search(r"<extract>([\s\S]*?)</extract>", extracted_text)
            if match:
                inner = match.group(1).strip()
                try:
                    parsed_inner = json.loads(inner)
                    if isinstance(parsed_inner, list):
                        extracted_text = "\n".join(
                            str(item.get("text") or item.get("text_content") or item)
                            for item in parsed_inner
                            if isinstance(item, dict)
                        )
                    else:
                        extracted_text = inner
                except Exception:
                    extracted_text = inner

        # Unpack JSON array of blocks [{"text_content": ...}] or [{"text": ...}] if returned as JSON
        trimmed = extracted_text.strip()
        if trimmed.startswith("[") and trimmed.endswith("]"):
            try:
                parsed_arr = json.loads(trimmed)
                if isinstance(parsed_arr, list) and all(isinstance(x, dict) for x in parsed_arr):
                    lines = [str(x.get("text_content") or x.get("text") or "") for x in parsed_arr]
                    if any(lines):
                        extracted_text = "\n".join(l for l in lines if l)
            except Exception:
                pass

    from kalanjiyam.utils.translation_engine import clean_translation_preambles
    extracted_text = clean_translation_preambles(extracted_text) if extracted_text else ""
    extracted_text = post_process(extracted_text)

    try:
        from kalanjiyam.utils.metrics import record_metric
        record_metric(
            category="ocr",
            name=f"ocr.{engine_name}",
            latency_ms=latency_ms,
            status="SUCCESS",
            details={"engine": engine_name, "language": language, "url": clean_base, "mode": "chat_completions"},
        )
    except Exception:
        pass

    return OcrResponse(
        text_content=extracted_text,
        bounding_boxes=parsed_boxes,
        blocks=parsed_blocks,
        content_format="blocks" if parsed_blocks else "plain",
        page_width=page_width,
        page_height=page_height,
        pipeline="standard",
        source_type="scan",
        coordinate_space="pixel",
        model={"name": model_id, "version": "vllm"},
        page_confidence=1.0 if parsed_blocks else None,
        contract_version="2.2",
        engine=engine_name,
        p05=None,
        blocks_count=len(parsed_blocks) if parsed_blocks else len(parsed_boxes),
        chars_count=len(extracted_text),
        engine_latency_ms=latency_ms,
    )


def run_ocr_remote(file_path: Path, engine_name: str, language: str) -> OcrResponse:
    targets = _get_ocr_service_targets()
    if not targets:
        raise RuntimeError("OCR_SERVICE_URL is not configured")

    timeout = float(current_app.config.get("OCR_SERVICE_TIMEOUT", 300))
    service_engine = engine_for_service(engine_name)
    last_exception: Exception | None = None

    for idx, (base_url, api_key) in enumerate(targets):
        clean_base = _normalize_base_url(base_url)
        url = f"{clean_base}/v1/ocr"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key

        logger.info("Calling OCR service engine=%s language=%s url=%s (target %d/%d)", engine_name, language, url, idx + 1, len(targets))
        start_time = time.time()

        try:
            with file_path.open("rb") as image_file:
                files = {"image": (file_path.name, image_file, "image/jpeg")}
                data = {"engine": service_engine, "language": language}
                with httpx.Client(timeout=timeout, trust_env=False) as client:
                    response = client.post(url, files=files, data=data, headers=headers)
            latency_ms = round((time.time() - start_time) * 1000, 2)

            is_chat_fallback_needed = (
                response.status_code in (404, 405)
                or (
                    response.status_code >= 400
                    and (
                        "not supported for provider" in response.text
                        or "Multipart OCR request" in response.text
                        or "Method Not Allowed" in response.text
                        or "litellm" in response.text.lower()
                    )
                )
            )
            if is_chat_fallback_needed:
                logger.info("OCR service at %s does not support /v1/ocr multipart (%s). Falling back to /v1/chat/completions prompt-based OCR...", clean_base, response.status_code)
                try:
                    return _run_ocr_chat_completions(
                        clean_base=clean_base,
                        headers=headers,
                        file_path=file_path,
                        engine_name=engine_name,
                        language=language,
                        timeout=timeout,
                        start_time=start_time,
                    )
                except Exception as chat_ex:
                    logger.warning("Prompt-based OCR via chat/completions failed at %s: %s", clean_base, chat_ex)
                    if idx < len(targets) - 1:
                        last_exception = chat_ex
                        continue
                    raise chat_ex

            if response.status_code >= 400:
                detail = response.text
                try:
                    detail = response.json().get("detail", detail)
                except Exception:
                    pass
                err_msg = f"OCR service error ({response.status_code}): {detail}"
                if (response.status_code >= 500 or response.status_code == 404) and idx < len(targets) - 1:
                    logger.warning("OCR service at %s failed with %s. Falling back to next endpoint...", base_url, err_msg)
                    last_exception = RuntimeError(err_msg)
                    continue
                raise RuntimeError(err_msg)

            try:
                from kalanjiyam.utils.metrics import record_metric
                record_metric(
                    category="ocr",
                    name=f"ocr.{engine_name}",
                    latency_ms=latency_ms,
                    status="SUCCESS",
                    details={"engine": engine_name, "language": language, "url": base_url},
                )
            except Exception:
                pass

            payload = response.json()
            blocks = payload.get("blocks")
            if blocks is not None and not isinstance(blocks, list):
                blocks = None
            if blocks:
                blocks = [_sanitize_block(b) for b in blocks if isinstance(b, dict)]
            # Legacy fields: may be absent in new contract — default gracefully
            text = payload.get("text", "") or ""
            boxes = _parse_bounding_boxes(payload.get("bounding_boxes"), engine_name)

            if not blocks:
                if "data-bbox" in text:
                    p_blocks, p_boxes, clean_t = _parse_chandra_html(text, payload.get("page_width"), payload.get("page_height"))
                    if p_blocks:
                        blocks = p_blocks
                        boxes = p_boxes
                        text = clean_t
                elif text.strip().startswith("[") or text.strip().startswith("{"):
                    p_blocks, p_boxes, clean_t = _parse_vlm_json_blocks(text, payload.get("page_width"), payload.get("page_height"))
                    if p_blocks:
                        blocks = p_blocks
                        boxes = p_boxes
                        text = clean_t

            model = payload.get("model")
            if not isinstance(model, dict):
                model = None
            coordinate_space = payload.get("coordinate_space") or "pixel"
            if coordinate_space not in ("pixel", "normalized"):
                coordinate_space = "pixel"
            page_confidence = _clamp_confidence(payload.get("page_confidence"))
            p05 = calculate_p05_confidence(blocks, page_confidence)
            blocks_count = len(blocks) if blocks else len(boxes)
            chars_count = len(text)
            engine_latency = payload.get("engine_latency_ms") or payload.get("latency_ms") or latency_ms
            try:
                engine_latency = float(engine_latency)
            except (TypeError, ValueError):
                engine_latency = latency_ms

            return OcrResponse(
                text_content=text,
                bounding_boxes=boxes,
                blocks=blocks,
                content_format="blocks" if blocks else "plain",
                page_width=payload.get("page_width"),
                page_height=payload.get("page_height"),
                pipeline="standard",
                source_type=payload.get("source_type", "scan"),
                coordinate_space=coordinate_space,
                model=model,
                page_confidence=page_confidence,
                contract_version=payload.get("contract_version"),
                engine=engine_name,
                p05=p05,
                blocks_count=blocks_count,
                chars_count=chars_count,
                engine_latency_ms=engine_latency,
            )

        except Exception as ex:
            latency_ms = round((time.time() - start_time) * 1000, 2)
            try:
                from kalanjiyam.utils.metrics import record_metric
                record_metric(
                    category="ocr",
                    name=f"ocr.{engine_name}",
                    latency_ms=latency_ms,
                    status="FAILED",
                    error_level="ERROR",
                    error_message=str(ex),
                    details={"engine": engine_name, "language": language, "url": base_url},
                )
            except Exception:
                pass

            last_exception = ex
            if idx < len(targets) - 1:
                logger.warning("OCR service at %s failed: %s. Retrying with fallback OCR target...", base_url, ex)
                continue
            raise ex

    if last_exception:
        raise last_exception
    raise RuntimeError("All OCR service targets failed")
