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


def normalize_engine(engine: str) -> str:
    return ENGINE_MAP.get(engine, engine)


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
