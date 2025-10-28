"""Chandra OCR engine implementation with GPU-first, CPU fallback."""

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import torch
from PIL import Image

@dataclass
class OcrResponse:
    """Response from OCR engine."""
    text_content: str
    bounding_boxes: List[Tuple[int, int, int, int, str]]
    confidence: Optional[float] = None


class ChandraOcrEngine:
    """Chandra OCR engine implementation with GPU-first, CPU fallback."""
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, device: str = 'auto'):
        """Initialize Chandra OCR engine.
        
        :param device: Device preference ('auto', 'cuda', 'cpu')
        """
        if not hasattr(self, '_initialized'):
            self.device = device
            self.device_preference = device
            self.inference_manager = None
            self.actual_device = None
            self._check_availability()
            self._load_model()
            self._initialized = True
    
    def _check_availability(self):
        """Check if required dependencies are available."""
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
    
    def _load_model(self):
        """Load the Chandra OCR model with GPU-first, CPU fallback."""
        try:
            from chandra.model import InferenceManager
            
            logging.info("Loading Chandra OCR model")
            
            # Determine device with GPU-first approach
            if self.device_preference == 'auto':
                if torch.cuda.is_available():
                    device = 'cuda'
                    logging.info("CUDA available - using GPU")
                else:
                    device = 'cpu'
                    logging.warning("CUDA not available - falling back to CPU (slower performance)")
            elif self.device_preference == 'cuda':
                if torch.cuda.is_available():
                    device = 'cuda'
                    logging.info("Using CUDA GPU as requested")
                else:
                    raise RuntimeError(
                        "CUDA requested but not available. Please ensure you have a compatible GPU with CUDA drivers installed."
                    )
            elif self.device_preference == 'cpu':
                device = 'cpu'
                logging.info("Using CPU as requested")
            else:
                device = self.device_preference
            
            self.actual_device = device
            
            # Initialize inference manager with Hugging Face method
            self.inference_manager = InferenceManager(method='hf')
            
            if device == 'cuda':
                logging.info("Chandra OCR model loaded on CUDA GPU")
            else:
                logging.info("Chandra OCR model loaded on CPU")
            
            logging.info(f"Chandra OCR model loaded successfully on {device}")
            
        except Exception as e:
            raise RuntimeError(f"Failed to load Chandra OCR model: {e}")
    
    def run(self, file_path: Path, language: str = 'sa', prompt: Optional[str] = None, **kwargs) -> OcrResponse:
        """Run Chandra OCR on the given image file.
        
        :param file_path: Path to the image file
        :param language: Language code (for compatibility, not used by Chandra)
        :param prompt: Optional custom prompt
        :param kwargs: Additional arguments
        :return: OCR response with text content and bounding boxes
        """
        if self.inference_manager is None:
            self._load_model()  # Ensure model is loaded

        try:
            from chandra.model.schema import BatchInputItem
            
            # Load image
            image = Image.open(file_path).convert("RGB")
            
            # Use default prompt if none provided
            if prompt is None:
                prompt = 'ocr_layout'  # Use layout-aware OCR prompt
            
            # Create batch input item
            batch_item = BatchInputItem(image=image, prompt=prompt)
            
            # Run inference
            results = self.inference_manager.generate([batch_item])
            
            if not results or len(results) == 0:
                raise RuntimeError("Chandra OCR returned no results")
            
            result = results[0]
            
            # Extract text content (prefer markdown output)
            text_content = result.markdown if result.markdown else result.raw
            
            # Generate bounding boxes from chunks
            bounding_boxes = self._generate_bounding_boxes_from_chunks(result.chunks, image.size)
            
            return OcrResponse(
                text_content=text_content,
                bounding_boxes=bounding_boxes,
                confidence=None  # Chandra OCR doesn't provide confidence scores
            )
        except Exception as e:
            logging.error(f"Chandra OCR inference failed: {e}")
            raise

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], language: str = 'sa', prompt: Optional[str] = None, **kwargs) -> OcrResponse:
        """Run Chandra OCR on a specific selection of the image."""
        try:
            original_image = Image.open(file_path).convert("RGB")

            x, y, width, height = selection['x'], selection['y'], selection['width'], selection['height']
            cropped_image = original_image.crop((x, y, x + width, y + height))

            with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as temp_img_file:
                temp_path = Path(temp_img_file.name)
                cropped_image.save(temp_path)

                # Run OCR on cropped image
                result = self.run(temp_path, language=language, prompt=prompt, **kwargs)

                # Adjust bounding boxes to original image coordinates
                adjusted_boxes = []
                for box in result.bounding_boxes:
                    # box is a tuple (x1, y1, x2, y2, text)
                    adjusted_box = (box[0] + x, box[1] + y, box[2] + x, box[3] + y, box[4])
                    adjusted_boxes.append(adjusted_box)

                return OcrResponse(
                    text_content=result.text_content,
                    bounding_boxes=adjusted_boxes,
                    confidence=result.confidence,
                )
        except Exception as e:
            logging.error(f"Chandra OCR with selection failed: {e}")
            raise

    def _generate_bounding_boxes_from_chunks(self, chunks: list, image_size: Tuple[int, int]) -> List[Tuple[int, int, int, int, str]]:
        """Generate bounding boxes from Chandra OCR chunks.
        
        Chandra OCR provides chunks with layout information that can be used
        to generate bounding boxes.
        """
        if not chunks or not isinstance(chunks, list):
            return []

        boxes = []
        image_width, image_height = image_size
        
        # Process each chunk
        for chunk in chunks:
            if isinstance(chunk, dict) and 'bbox' in chunk and 'content' in chunk:
                bbox = chunk['bbox']
                content = chunk['content']
                
                # Convert bbox to our format (x1, y1, x2, y2, text)
                if len(bbox) >= 4:
                    x1, y1, x2, y2 = bbox[:4]
                    
                    # Convert normalized coordinates to pixel coordinates
                    x1 = int(x1 * image_width)
                    y1 = int(y1 * image_height)
                    x2 = int(x2 * image_width)
                    y2 = int(y2 * image_height)
                    
                    # Ensure coordinates are within image bounds
                    x1 = max(0, min(x1, image_width))
                    y1 = max(0, min(y1, image_height))
                    x2 = max(0, min(x2, image_width))
                    y2 = max(0, min(y2, image_height))
                    
                    if x1 < x2 and y1 < y2 and content.strip():
                        # Extract text content from HTML if present
                        text_content = self._extract_text_from_html(content)
                        if text_content.strip():
                            boxes.append((x1, y1, x2, y2, text_content.strip()))
        
        return boxes

    def _extract_text_from_html(self, html_content: str) -> str:
        """Extract plain text from HTML content."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            return soup.get_text()
        except ImportError:
            # Fallback: simple regex-based HTML tag removal
            import re
            return re.sub(r'<[^>]+>', '', html_content)

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Chandra OCR."""
        # Chandra OCR supports 40+ languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms', 'zh-cn', 'zh-tw', 'ja', 'ko', 'th', 'vi', 'id', 'ms', 'tl']
