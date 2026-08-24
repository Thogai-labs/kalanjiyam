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
    "run_enhanced_ocr",
]


def run_enhanced_ocr(
    file_path: Path,
    engine_name: str,
    profile: str = "background_clahe",
    language: str = "sa",
    gpu_config=None,
) -> OcrResponse:
    from kalanjiyam.utils.enhanced_ocr import run_enhanced_ocr as _run_enhanced_ocr

    return _run_enhanced_ocr(
        file_path=file_path,
        engine_name=engine_name,
        profile=profile,
        language=language,
        gpu_config=gpu_config,
    )


def run_ocr(
    file_path: Path,
    engine_name: str,
    language: str,
    gpu_config=None,
) -> OcrResponse:
    del gpu_config  # GPU config is owned by the OCR service.
    return run_ocr_remote(file_path, normalize_engine(engine_name), language)
