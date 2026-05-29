"""Shared OCR types and helpers for the Kalanjiyam OCR client."""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class OcrResponse:
    text_content: str
    bounding_boxes: list[tuple[int, int, int, int, str]]


SUPPORTED_ENGINES = [
    "google",
    "tesseract",
    "surya",
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


def build_engine_choices(available_engines: list[str], is_super_admin: bool) -> list[dict]:
    """Build the list of engine choices for the OCR form.

    Regular users see "OCR 1", "OCR 2" etc. Super-admins see the real name too.
    Only engines returned by the OCR service ping are included.
    Engine names are normalised to lowercase before matching.
    """
    choices = []
    for raw_name in available_engines:
        engine_name = raw_name.lower().strip()
        number = REVERSE_ENGINE_MAP.get(engine_name)
        if number is None:
            continue
        label = f"OCR {number}" if not is_super_admin else f"OCR {number} ({engine_name})"
        choices.append({"value": number, "label": label})
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
    engine: str, boxes: list[tuple[int, int, int, int, str]]
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
