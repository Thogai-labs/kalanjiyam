"""Enhanced OCR runner — applies image enhancement profile before running OCR."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from kalanjiyam.utils.image_preprocessing import (
    SUPPORTED_ENHANCEMENT_PROFILES,
    preprocess_image_to_tempfile,
    validate_enhancement_profile,
)
from kalanjiyam.utils.ocr_runner import run_ocr
from kalanjiyam.utils.ocr_types import (
    SUPPORTED_ENGINES,
    OcrResponse,
    engine_for_service,
    normalize_engine,
)

logger = logging.getLogger(__name__)

ENHANCEMENT_VERSION = "1.0"

__all__ = [
    "ENHANCEMENT_VERSION",
    "SUPPORTED_ENHANCEMENT_PROFILES",
    "run_enhanced_ocr",
]


def run_enhanced_ocr(
    file_path: Path | str,
    engine_name: str,
    profile: str = "background_clahe",
    language: str = "sa",
    gpu_config=None,
) -> OcrResponse:
    """Run Enhanced OCR pipeline on an input page image.

    1. Validates source image path, engine, and enhancement profile.
    2. Applies the requested preprocessing profile (preserving image pixel dimensions).
    3. Executes OCR via run_ocr().
    4. Stamps enhanced OCR metadata (ocr_mode, enhancement_profile, enhancement_version).
    5. Preserves coordinate space and page dimensions.
    """
    del gpu_config

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Source image not found: {path}")

    # Validate enhancement profile
    valid_profile = validate_enhancement_profile(profile)

    # Validate engine
    normalized_engine = normalize_engine(engine_name)
    if normalized_engine not in SUPPORTED_ENGINES:
        raise ValueError(
            f"Unsupported OCR engine: {engine_name!r}. Supported engines: {SUPPORTED_ENGINES}"
        )

    t0_prep = time.time()
    logger.info(
        "Starting Enhanced OCR for %s: engine=%s, profile=%s, language=%s",
        path.name,
        normalized_engine,
        valid_profile,
        language,
    )

    with preprocess_image_to_tempfile(path, valid_profile) as preprocessed_path:
        prep_latency_ms = round((time.time() - t0_prep) * 1000, 2)

        # Execute OCR using the existing OCR infrastructure
        ocr_response = run_ocr(
            preprocessed_path,
            engine_name=normalized_engine,
            language=language,
        )

    # Stamp enhanced OCR provenance and metadata
    ocr_response.ocr_mode = "enhanced"
    ocr_response.enhancement_version = ENHANCEMENT_VERSION
    ocr_response.enhancement_profile = valid_profile
    ocr_response.preprocessing_latency_ms = prep_latency_ms
    ocr_response.engine = normalized_engine

    if not ocr_response.contract_version:
        ocr_response.contract_version = "2.2"

    if ocr_response.model is None:
        ocr_response.model = {
            "name": engine_for_service(normalized_engine),
            "version": "1.0.0",
        }

    logger.info(
        "Enhanced OCR completed for %s: engine=%s, profile=%s, prep_latency=%.2fms, engine_latency=%.2fms",
        path.name,
        normalized_engine,
        valid_profile,
        prep_latency_ms,
        ocr_response.engine_latency_ms or 0.0,
    )

    return ocr_response
