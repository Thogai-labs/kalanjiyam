"""Unified OCR engine interface for proofing projects."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from kalanjiyam.utils import google_ocr, tesseract_ocr, surya_ocr, nanonets_ocr, deepseek_ocr, chandra_ocr, qwen3_ocr


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


class NanonetsOcrEngine(OcrEngine):
    """Nanonets OCR engine with GPU-first, CPU fallback."""

    def __init__(self, device: str = 'auto'):
        """Initialize Nanonets OCR engine and check availability."""
        self.device = device
        self._check_availability()
        self._engine_instance = None

    def _check_availability(self):
        """Check if Nanonets OCR is available."""
        try:
            import transformers
            import torch
        except ImportError as e:
            import sys
            logging.error(f"Nanonets OCR import failed. Python executable: {sys.executable}")
            logging.error(f"Import error: {e}")
            raise RuntimeError(
                f"Nanonets OCR dependencies are not installed in the current Python environment.\n"
                f"Python executable: {sys.executable}\n"
                f"Please install them with: pip install torch>=2.0.0 transformers>=4.30.0 flash-attn\n"
                f"For more information, see: https://huggingface.co/nanonets/Nanonets-OCR2-3B"
            )

    def _get_engine_instance(self):
        """Get or create Nanonets OCR engine instance."""
        if self._engine_instance is None:
            from kalanjiyam.utils.nanonets_ocr import NanonetsOcrEngine as NanonetsEngine
            self._engine_instance = NanonetsEngine(device=self.device)
        return self._engine_instance

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run Nanonets OCR on the given image file."""
        engine = self._get_engine_instance()
        return engine.run(file_path, **kwargs)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run Nanonets OCR on a specific selection of the image."""
        engine = self._get_engine_instance()
        return engine.run_with_selection(file_path, selection, **kwargs)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Nanonets OCR."""
        # Nanonets OCR supports many languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms']


class DeepSeekOcrEngine(OcrEngine):
    """DeepSeek OCR engine with GPU-first, CPU fallback."""

    def __init__(self, device: str = 'auto'):
        """Initialize DeepSeek OCR engine and check availability."""
        self.device = device
        self._check_availability()
        self._engine_instance = None

    def _check_availability(self):
        """Check if DeepSeek OCR is available."""
        try:
            import transformers
            import torch
        except ImportError as e:
            import sys
            logging.error(f"DeepSeek OCR import failed. Python executable: {sys.executable}")
            logging.error(f"Import error: {e}")
            raise RuntimeError(
                f"DeepSeek OCR dependencies are not installed in the current Python environment.\n"
                f"Python executable: {sys.executable}\n"
                f"Please install them with: pip install torch>=2.0.0 transformers>=4.30.0\n"
                f"For more information, see: https://huggingface.co/deepseek-ai/DeepSeek-OCR"
            )

    def _get_engine_instance(self):
        """Get or create DeepSeek OCR engine instance."""
        if self._engine_instance is None:
            from kalanjiyam.utils.deepseek_ocr import DeepSeekOcrEngine as DeepSeekEngine
            self._engine_instance = DeepSeekEngine(device=self.device)
        return self._engine_instance

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run DeepSeek OCR on the given image file."""
        engine = self._get_engine_instance()
        return engine.run(file_path, **kwargs)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run DeepSeek OCR on a specific selection of the image."""
        engine = self._get_engine_instance()
        return engine.run_with_selection(file_path, selection, **kwargs)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for DeepSeek OCR."""
        # DeepSeek OCR supports ~100 languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms']


class ChandraOcrEngine(OcrEngine):
    """Chandra OCR engine with GPU-first, CPU fallback."""

    def __init__(self, device: str = 'auto'):
        """Initialize Chandra OCR engine and check availability."""
        self.device = device
        self._check_availability()
        self._engine_instance = None

    def _check_availability(self):
        """Check if Chandra OCR is available."""
        try:
            import chandra
            import torch
            import transformers
        except ImportError as e:
            import sys
            logging.error(f"Chandra OCR import failed. Python executable: {sys.executable}")
            logging.error(f"Import error: {e}")
            raise RuntimeError(
                f"Chandra OCR dependencies are not installed in the current Python environment.\n"
                f"Python executable: {sys.executable}\n"
                f"Please install them with: pip install chandra-ocr\n"
                f"For more information, see: https://github.com/datalab-to/chandra"
            )

    def _get_engine_instance(self):
        """Get or create Chandra OCR engine instance."""
        if self._engine_instance is None:
            from kalanjiyam.utils.chandra_ocr import ChandraOcrEngine as ChandraEngine
            self._engine_instance = ChandraEngine(device=self.device)
        return self._engine_instance

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run Chandra OCR on the given image file."""
        engine = self._get_engine_instance()
        return engine.run(file_path, **kwargs)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run Chandra OCR on a specific selection of the image."""
        engine = self._get_engine_instance()
        return engine.run_with_selection(file_path, selection, **kwargs)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Chandra OCR."""
        # Chandra OCR supports 40+ languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms', 'zh-cn', 'zh-tw', 'ja', 'ko', 'th', 'vi', 'id', 'ms', 'tl']


class Qwen3OcrEngine(OcrEngine):
    """Qwen 3 OCR engine with GPU-first, CPU fallback."""

    def __init__(self, device: str = 'auto'):
        """Initialize Qwen 3 OCR engine and check availability."""
        self.device = device
        self._check_availability()
        self._engine_instance = None

    def _check_availability(self):
        """Check if Qwen 3 OCR is available."""
        try:
            from transformers import Qwen2VLForConditionalGeneration, Qwen2VLProcessor
            import torch
            import transformers
        except ImportError as e:
            import sys
            logging.error(f"Qwen 3 OCR import failed. Python executable: {sys.executable}")
            logging.error(f"Import error: {e}")
            raise RuntimeError(
                f"Qwen 3 OCR dependencies are not installed in the current Python environment.\n"
                f"Python executable: {sys.executable}\n"
                f"Please install them with: pip install transformers>=4.57.1 torch qwen-omni-utils\n"
                f"For more information, see: https://huggingface.co/Qwen/Qwen2-VL-2B-Instruct"
            )

    def _get_engine_instance(self):
        """Get or create Qwen 3 OCR engine instance."""
        if self._engine_instance is None:
            from kalanjiyam.utils.qwen3_ocr import Qwen3OcrEngine as Qwen3Engine
            self._engine_instance = Qwen3Engine(device=self.device)
        return self._engine_instance

    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run Qwen 3 OCR on the given image file."""
        engine = self._get_engine_instance()
        return engine.run(file_path, **kwargs)

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run Qwen 3 OCR on a specific selection of the image."""
        engine = self._get_engine_instance()
        return engine.run_with_selection(file_path, selection, **kwargs)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Qwen 3 OCR."""
        # Qwen 3 supports 100+ languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms', 'zh-cn', 'zh-tw', 'ja', 'ko', 'th', 'vi', 'id', 'ms', 'tl', 'my', 'km', 'lo', 'ne', 'si', 'dz', 'bo', 'ug', 'mn', 'kk', 'ky', 'uz', 'tg', 'az', 'tk', 'ka', 'hy', 'am', 'ti', 'om', 'so', 'sw', 'zu', 'xh', 'af', 'sq', 'eu', 'be', 'bg', 'hr', 'cs', 'da', 'et', 'fi', 'gl', 'hu', 'is', 'ga', 'lv', 'lt', 'mk', 'mt', 'no', 'ro', 'sk', 'sl', 'sv', 'uk', 'cy', 'he', 'yi', 'jv', 'su', 'ceb', 'haw', 'mg', 'mi', 'sm', 'to', 'ty', 've', 'wo', 'yo', 'zu']


class SuryaOcrEngine(OcrEngine):
    
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
            logging.error(f"Surya OCR import failed. Python executable: {sys.executable}")
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


class OcrEngineFactory:
    """Factory for creating OCR engines."""
    
    _engines = {
        'google': GoogleOcrEngine,
        'tesseract': TesseractOcrEngine,
        'surya': SuryaOcrEngine,
        'nanonets': NanonetsOcrEngine,
        'deepseek': DeepSeekOcrEngine,
        'chandra': ChandraOcrEngine,
        'qwen3': Qwen3OcrEngine,
    }
    
    @classmethod
    def create(cls, engine_name: str, gpu_config: Optional[Dict[str, Any]] = None) -> OcrEngine:
        """Create an OCR engine instance.

        :param engine_name: Name of the engine ('google', 'tesseract', 'surya', 'nanonets', 'deepseek', 'chandra', or 'qwen3')
        :param gpu_config: Optional GPU configuration for Surya OCR, Nanonets OCR, DeepSeek OCR, Chandra OCR, and Qwen 3 OCR
        :return: OCR engine instance
        :raises: ValueError if engine name is not supported
        :raises: RuntimeError if Surya OCR, Nanonets OCR, DeepSeek OCR, Chandra OCR, or Qwen 3 OCR is not installed (when engine_name is 'surya', 'nanonets', 'deepseek', 'chandra', or 'qwen3')
        """
        if engine_name not in cls._engines:
            raise ValueError(f"Unsupported OCR engine: {engine_name}. Supported engines: {list(cls._engines.keys())}")
        
        try:
            if engine_name == 'surya' and gpu_config:
                return cls._engines[engine_name](gpu_config=gpu_config)
            elif engine_name == 'nanonets':
                # Nanonets OCR with GPU-first, CPU fallback
                device = gpu_config.get('device', 'auto') if gpu_config else 'auto'
                return cls._engines[engine_name](device=device)
            elif engine_name == 'deepseek':
                # DeepSeek OCR with GPU-first, CPU fallback
                device = gpu_config.get('device', 'auto') if gpu_config else 'auto'
                return cls._engines[engine_name](device=device)
            elif engine_name == 'chandra':
                # Chandra OCR with GPU-first, CPU fallback
                device = gpu_config.get('device', 'auto') if gpu_config else 'auto'
                return cls._engines[engine_name](device=device)
            elif engine_name == 'qwen3':
                # Qwen 3 OCR with GPU-first, CPU fallback
                device = gpu_config.get('device', 'auto') if gpu_config else 'auto'
                return cls._engines[engine_name](device=device)
            else:
                return cls._engines[engine_name]()
        except RuntimeError as e:
            # Re-raise RuntimeError with clear message
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
    :param engine_name: name of the OCR engine to use ('google', 'tesseract', 'surya', or 'deepseek').
    :param gpu_config: optional GPU configuration for Surya OCR
    :param kwargs: additional arguments to pass to the OCR engine.
    :return: an OCR response containing the image's text content and bounding boxes.
    """
    engine = OcrEngineFactory.create(engine_name, gpu_config=gpu_config)
    return engine.run(file_path, **kwargs) 