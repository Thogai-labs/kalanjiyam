"""Unified OCR engine interface for proofing projects."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from kalanjiyam.utils import google_ocr, tesseract_ocr, surya_ocr, docling_ocr


# Use the OcrResponse from google_ocr as the common interface
OcrResponse = google_ocr.OcrResponse


class OcrEngine(ABC):
    """Abstract base class for OCR engines."""

    @abstractmethod
    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run OCR on the given image file."""
        pass

    @abstractmethod
    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run OCR on a specific selection of the image."""
        pass

    @abstractmethod
    def get_supported_languages(self) -> List[str]:
        """Get list of supported language codes."""
        pass


class GoogleOcrEngine(OcrEngine):
    """Google Cloud Vision OCR engine."""

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run Google OCR on the given image file."""
        language = kwargs.get('language', 'sa')  # Default to Sanskrit
        return google_ocr.run(file_path, language=language)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run Google OCR on a specific selection of the image."""
        language = kwargs.get('language', 'sa')  # Default to Sanskrit
        return google_ocr.run_with_selection(file_path, selection, language=language)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Google OCR."""
        # Google Cloud Vision supports many languages, but we'll focus on the most relevant ones
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur']


class TesseractOcrEngine(OcrEngine):
    """Tesseract OCR engine."""

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run Tesseract OCR on the given image file."""
        language = kwargs.get('language', 'san')  # Default to Sanskrit
        return tesseract_ocr.run(file_path, language=language)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run Tesseract OCR on a specific selection of the image."""
        language = kwargs.get('language', 'san')  # Default to Sanskrit
        return tesseract_ocr.run_with_selection(file_path, selection, language=language)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Tesseract OCR."""
        # Tesseract language codes (these need to be installed)
        return ['san', 'eng', 'hin', 'tel', 'mar', 'ben', 'guj', 'kan', 'mal', 'tam', 'pan', 'ori', 'urd']


class SuryaOcrEngine(OcrEngine):
    """Surya OCR engine."""

    def __init__(self, gpu_config: Optional[Dict[str, Any]] = None):
        """Initialize Surya OCR engine and check availability."""
        self.gpu_config = gpu_config
        self._check_availability()

    def _check_availability(self):
        """Check if Surya OCR is available."""
        try:
            import surya
        except ImportError as e:
            import sys
            logging.error(
                f"Surya OCR import failed. Python executable: {sys.executable}")
            logging.error(f"Import error: {e}")
            raise RuntimeError(
                f"Surya OCR is not installed in the current Python environment.\n"
                f"Python executable: {sys.executable}\n"
                f"Please install it with: pip install surya-ocr\n"
                f"For more information, see: https://github.com/datalab-to/surya"
            )

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run Surya OCR on the given image file."""
        language = kwargs.get('language', 'sa')  # Default to Sanskrit
        additional_languages = kwargs.get('additional_languages', None)
        gpu_config = kwargs.get('gpu_config', self.gpu_config)
        return surya_ocr.run(file_path, language=language, additional_languages=additional_languages, gpu_config=gpu_config)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run Surya OCR on a specific selection of the image."""
        language = kwargs.get('language', 'sa')  # Default to Sanskrit
        additional_languages = kwargs.get('additional_languages', None)
        gpu_config = kwargs.get('gpu_config', self.gpu_config)
        return surya_ocr.run_with_selection(file_path, selection, language=language, additional_languages=additional_languages, gpu_config=gpu_config)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Surya OCR."""
        # Surya supports 90+ languages, using similar codes to Google OCR
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms']


class DoclingOcrEngine(OcrEngine):
    """Docling OCR engine."""

    def __init__(self, gpu_config: Optional[Dict[str, Any]] = None):
        self.gpu_config = gpu_config

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        language = kwargs.get('language', 'sa')
        gpu_config = kwargs.get('gpu_config', self.gpu_config)
        return docling_ocr.run(file_path, language=language, gpu_config=gpu_config)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        language = kwargs.get('language', 'sa')
        gpu_config = kwargs.get('gpu_config', self.gpu_config)
        return docling_ocr.run_with_selection(file_path, selection, language=language, gpu_config=gpu_config)

    def get_supported_languages(self) -> List[str]:
        return docling_ocr.get_supported_languages()


class OcrEngineFactory:
    """Factory for creating OCR engines."""

    _engines = {
        'google': GoogleOcrEngine,
        'tesseract': TesseractOcrEngine,
        'surya': SuryaOcrEngine,
        'docling': DoclingOcrEngine
    }

    @classmethod
    def create(cls, engine_name: str, gpu_config: Optional[Dict[str, Any]] = None) -> OcrEngine:
        """Create an OCR engine instance.

        :param engine_name: Name of the engine ('google', 'tesseract', or 'surya')
        :param gpu_config: Optional GPU configuration for Surya OCR
        :return: OCR engine instance
        :raises: ValueError if engine name is not supported
        :raises: RuntimeError if Surya OCR is not installed (when engine_name is 'surya')
        """
        if engine_name not in cls._engines:
            raise ValueError(
                f"Unsupported OCR engine: {engine_name}. Supported engines: {list(cls._engines.keys())}")

        try:
            if engine_name == 'surya' and gpu_config:
                return cls._engines[engine_name](gpu_config=gpu_config)
            else:
                return cls._engines[engine_name]()
        except RuntimeError as e:
            # Re-raise RuntimeError (e.g., Surya not installed) with clear message
            raise RuntimeError(str(e))

    @classmethod
    def get_supported_engines(cls) -> List[str]:
        """Get list of supported OCR engines."""
        return list(cls._engines.keys())

    @classmethod
    def get_supported_languages(cls, engine_name: str) -> List[str]:
        """Get supported languages for a specific engine."""
        if engine_name not in cls._engines:
            raise ValueError(f"Unsupported OCR engine: {engine_name}")

        engine = cls._engines[engine_name]()
        return engine.get_supported_languages()


def run_ocr(file_path: Path, engine_name: str = 'google', gpu_config: Optional[Dict[str, Any]] = None, **kwargs) -> OcrResponse:
    """Run OCR on the given image file using the specified engine.

    :param file_path: path to the image we'll process with OCR.
    :param engine_name: name of the OCR engine to use ('google', 'tesseract', or 'surya').
    :param gpu_config: optional GPU configuration for Surya OCR
    :param kwargs: additional arguments to pass to the OCR engine.
    :return: an OCR response containing the image's text content and bounding boxes.
    """
    engine = OcrEngineFactory.create(engine_name, gpu_config=gpu_config)
    return engine.run(file_path, **kwargs)
