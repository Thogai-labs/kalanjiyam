"""Shared OCR types and helpers for the Kalanjiyam OCR client."""

from __future__ import annotations

import json
from dataclasses import dataclass


BLOCK_TYPES = {
    "paragraph", "heading", "subheading", "table", "figure",
    "caption", "footnote", "running-header", "page-number",
    "column-header", "equation",
}

# Block types the UI skips in flow mode (layout chrome, not content)
DECORATIVE_BLOCK_TYPES = {"running-header", "page-number", "figure"}


# Confidence bands shared by the editor UI and the service contract
# (see docs/ocr-service-contract.rst). Scores below CONFIDENCE_LOW render
# red ("likely error"); below CONFIDENCE_REVIEW render amber ("review").
CONFIDENCE_LOW = 0.5
CONFIDENCE_REVIEW = 0.75


@dataclass
class OcrResponse:
    text_content: str
    bounding_boxes: list[tuple[float, float, float, float, str]]
    layout_html: str | None = None
    blocks: list[dict] | None = None
    content_format: str = "plain"
    page_width: int | None = None
    page_height: int | None = None
    pipeline: str = "standard"
    source_type: str = "scan"  # "scan" | "pdf" | "digital"
    coordinate_space: str = "pixel"  # "pixel" | "normalized"
    #: Model identity for provenance, e.g. {"name": "surya-rec", "version": "0.6.1"}.
    model: dict | None = None
    #: Aggregate page confidence in [0, 1], if the engine provides one.
    page_confidence: float | None = None


SUPPORTED_ENGINES = [
    "google",
    "tesseract",
    "surya",
    "surya_table",
    "nanonets",
    "deepseek",
    "chandra",
    "qwen3",
    "paddle_table",
    "glm_ocr",
    "tesseract_manuscript",
]

# OCR service ids (hyphenated) ↔ Kalanjiyam internal ids (underscored).
SERVICE_ENGINE_ALIASES = {
    "surya-table": "surya_table",
    "glm-ocr": "glm_ocr",
}

ENGINE_MAP = {
    "1": "google",
    "2": "tesseract",
    "3": "surya",
    "4": "nanonets",
    "5": "deepseek",
    "6": "chandra",
    "7": "qwen3",
    "8": "surya_table",
    "9": "paddle_table",
    "10": "glm_ocr",
    "11": "tesseract_manuscript",
}


# Reverse: engine name → numeric key shown to users
REVERSE_ENGINE_MAP = {v: k for k, v in ENGINE_MAP.items()}


def normalize_engine(engine: str) -> str:
    return normalize_service_engine(ENGINE_MAP.get(engine, engine))


def normalize_service_engine(engine: str) -> str:
    """Map an OCR service engine id to the Kalanjiyam internal id."""
    name = (engine or "").lower().strip().replace("-", "_")
    for service_id, app_id in SERVICE_ENGINE_ALIASES.items():
        if name == service_id.replace("-", "_"):
            return app_id
    return name


def engine_for_service(engine: str) -> str:
    """Map a Kalanjiyam internal engine id to the OCR service id."""
    name = normalize_service_engine(engine)
    for service_id, app_id in SERVICE_ENGINE_ALIASES.items():
        if name == app_id:
            return service_id
    return name.replace("_", "-")


ENGINE_LABELS = {
    "google": "Google",
    "tesseract": "Tesseract",
    "surya": "Surya",
    "surya_table": "Surya Table",
    "nanonets": "Nanonets",
    "deepseek": "DeepSeek",
    "chandra": "Chandra",
    "qwen3": "Qwen 2VL",
    "paddle_table": "Paddle Table OCR",
    "glm_ocr": "GLM OCR",
    "tesseract_manuscript": "Sanskrit Manuscript OCR",
}

# Engines that return HTML (not plain text or Markdown)
HTML_ENGINES = {"nanonets", "chandra"}

# Engines that return Markdown
MARKDOWN_ENGINES = {"deepseek", "qwen3"}


def build_engine_choices(available_engines: list[str], is_super_admin: bool) -> list[dict]:
    """Build the list of engine choices for the OCR form.

    Value is the stable numeric key (matches JS ocrEngines / decodeEngine).
    Label is "OCR N" for regular users, real name for super admins.
    Only engines returned by the OCR service ping are included.
    """
    choices = []
    seq = 1
    for raw_name in available_engines:
        engine_name = normalize_service_engine(raw_name)
        if engine_name not in SUPPORTED_ENGINES:
            continue
        numeric_value = REVERSE_ENGINE_MAP.get(engine_name, str(seq))
        real_name = ENGINE_LABELS.get(engine_name, engine_name.capitalize())
        label = real_name if is_super_admin else f"OCR {seq}"
        choices.append({"value": numeric_value, "label": label})
        seq += 1
    return choices


def post_process(text: str) -> str:
    return (
        text.replace("||", "॥")
        .replace("|", "।")
        .replace("।।", "॥")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )


def serialize_bounding_boxes(
    engine: str, boxes: list[tuple[float, float, float, float, str]]
) -> str:
    if not boxes:
        return ""
    if engine == "surya":
        return json.dumps(
            [
                {"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3], "text": b[4]}
                for b in boxes
            ]
        )
    return "\n".join("\t".join(str(x) for x in row) for row in boxes)
