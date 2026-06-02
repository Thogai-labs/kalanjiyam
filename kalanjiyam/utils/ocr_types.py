"""Shared OCR types and helpers for the Kalanjiyam OCR client."""

from __future__ import annotations

import json
from dataclasses import dataclass


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


SUPPORTED_ENGINES = [
    "google",
    "tesseract",
    "surya",
    "surya_table",
    "nanonets",
    "deepseek",
    "chandra",
    "qwen3",
]

ENGINE_MAP = {
    "1": "google",
    "2": "tesseract",
    "3": "surya",
    "4": "nanonets",
    "5": "deepseek",
    "6": "chandra",
    "7": "qwen3",
}


# Reverse: engine name → numeric key shown to users
REVERSE_ENGINE_MAP = {v: k for k, v in ENGINE_MAP.items()}


def normalize_engine(engine: str) -> str:
    return ENGINE_MAP.get(engine, engine)


ENGINE_LABELS = {
    "google": "Google",
    "tesseract": "Tesseract",
    "surya": "Surya",
    "surya_table": "Surya Table",
    "nanonets": "Nanonets",
    "deepseek": "DeepSeek",
    "chandra": "Chandra",
    "qwen3": "Qwen 2VL",
}

# Engines that return HTML (not plain text or Markdown)
HTML_ENGINES = {"nanonets", "chandra"}

# Engines that return Markdown
MARKDOWN_ENGINES = {"deepseek", "qwen3"}


def build_engine_choices(available_engines: list[str], is_super_admin: bool) -> list[dict]:
    """Build the list of engine choices for the OCR form.

    Value is always the engine name (for correct backend calls).
    Label is "OCR N" for regular users, real name for super admins.
    Only engines returned by the OCR service ping are included.
    """
    choices = []
    for i, raw_name in enumerate(available_engines, start=1):
        engine_name = raw_name.lower().strip()
        if engine_name not in SUPPORTED_ENGINES:
            continue
        real_name = ENGINE_LABELS.get(engine_name, engine_name.capitalize())
        label = real_name if is_super_admin else f"OCR {i}"
        choices.append({"value": engine_name, "label": label})
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
