"""OCR entry point — delegates to the standalone OCR service."""

from __future__ import annotations

from pathlib import Path

from kalanjiyam.utils.ocr_client import run_ocr_remote
from kalanjiyam.utils.ocr_types import (
    ENGINE_MAP,
    OcrResponse,
    SUPPORTED_ENGINES,
    normalize_engine,
)

__all__ = [
    "ENGINE_MAP",
    "OcrResponse",
    "SUPPORTED_ENGINES",
    "normalize_engine",
    "run_ocr",
]


def run_ocr(
    file_path: Path,
    engine_name: str,
    language: str,
    gpu_config=None,
) -> OcrResponse:
    del gpu_config  # GPU config is owned by the OCR service.
    return run_ocr_remote(file_path, normalize_engine(engine_name), language)
