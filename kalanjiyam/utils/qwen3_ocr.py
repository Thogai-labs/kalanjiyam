"""Qwen 3 OCR engine implementation with GPU-first, CPU fallback."""

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from PIL import Image
import re
from bs4 import BeautifulSoup


@dataclass
class OcrResponse:
    """Response from OCR engine."""
    text_content: str
    bounding_boxes: List[Tuple[int, int, int, int, str]]
    confidence: Optional[float] = None


class Qwen3OcrEngine:
    """Qwen 3 OCR engine implementation with GPU-first, CPU fallback."""
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, model_name: str = 'Qwen/Qwen2-VL-2B-Instruct', device: str = 'auto'):
        """Initialize Qwen 3 OCR engine.
        
        :param model_name: Hugging Face model name for Qwen 3 OCR
        :param device: Device preference ('auto', 'cuda', 'cpu')
        """
        if not hasattr(self, '_initialized'):
            self.model_name = model_name
            self.device_preference = device
            self.model = None
            self.processor = None
            self.actual_device = None
            self._check_availability()
            self._load_model()
            self._initialized = True
    
    def _check_availability(self):
        """Check if required dependencies are available."""
        try:
            import transformers
            import torch
            from transformers import Qwen2VLForConditionalGeneration, Qwen2VLProcessor
        except ImportError as e:
            import sys
            logging.error(f"Qwen 3 OCR import failed. Python executable: {sys.executable}")
            logging.error(f"Import error: {e}")
            raise RuntimeError(
                f"Qwen 3 OCR dependencies are not installed in the current Python environment.\n"
                f"Python executable: {sys.executable}\n"
                f"Please install them with: pip install transformers>=4.57.1 torch qwen-omni-utils\n"
                f"Original error: {e}"
            )
    
    def _load_model(self):
        """Load the Qwen 3 OCR model and processor with GPU-first, CPU fallback."""
        try:
            import torch
            from transformers import Qwen2VLForConditionalGeneration, Qwen2VLProcessor
            
            logging.info(f"Loading Qwen 3 OCR model: {self.model_name}")
            
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
            
            # Load processor
            self.processor = Qwen2VLProcessor.from_pretrained(self.model_name)
            
            # Load model with appropriate configuration
            model_kwargs = {
                'torch_dtype': torch.float16 if device == 'cuda' else torch.float32,
                'device_map': 'auto' if device == 'cuda' else None,
                'trust_remote_code': True,
            }
            
            # Add flash attention for GPU if available
            if device == 'cuda':
                # Flash Attention 2 disabled due to GLIBC compatibility issue (requires GLIBC 2.32+, system has 2.31)
                logging.info("GPU acceleration enabled (Flash Attention 2 disabled due to GLIBC compatibility)")
                # try:
                #     model_kwargs['attn_implementation'] = 'flash_attention_2'
                #     logging.info("Using Flash Attention 2 for GPU acceleration")
                # except Exception:
                #     logging.warning("Flash Attention 2 not available, using default attention")
            
            self.model = Qwen2VLForConditionalGeneration.from_pretrained(self.model_name, **model_kwargs)
            self.model.eval()
            
            if device == 'cuda':
                logging.info("Qwen 3 OCR model loaded on CUDA GPU")
            else:
                logging.info("Qwen 3 OCR model loaded on CPU")
            
            logging.info(f"Qwen 3 OCR model loaded successfully on {device}")
            
        except Exception as e:
            raise RuntimeError(f"Failed to load Qwen 3 OCR model: {e}")
    
    def run(self, file_path: Path, language: str = 'sa', prompt: Optional[str] = None, **kwargs) -> OcrResponse:
        """Run Qwen 3 OCR on the given image file.
        
        :param file_path: Path to the image file
        :param language: Language code (for compatibility, not directly used by Qwen 3)
        :param prompt: Optional custom prompt
        :param kwargs: Additional arguments
        :return: OCR response with text content and bounding boxes
        """
        if self.model is None or self.processor is None:
            self._load_model()  # Ensure model is loaded

        try:
            import torch
            
            # Load image
            image = Image.open(file_path).convert("RGB")
            
            # Default prompt for OCR
            if prompt is None:
                prompt = "Please extract all text from this image. Return the text content clearly and accurately."
            
            # Prepare messages for Qwen 3
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            
            # Process inputs
            inputs = self.processor(messages, return_tensors="pt")
            
            # Move inputs to the same device as model
            if self.actual_device == 'cuda':
                inputs = {k: v.cuda() if isinstance(v, torch.Tensor) else v for k, v in inputs.items()}
            
            # Generate response
            with torch.no_grad():
                outputs = self.model.generate(
                    **inputs,
                    max_new_tokens=2048,
                    do_sample=False,
                    temperature=0.1,
                    top_p=0.9,
                    pad_token_id=self.processor.tokenizer.eos_token_id
                )
            
            # Decode response
            response = self.processor.batch_decode(outputs, skip_special_tokens=True)[0]
            
            # Extract text content (remove the prompt from response)
            text_content = self._extract_text_from_response(response, prompt)
            
            # Generate bounding boxes (approximate line-level boxes)
            bounding_boxes = self._generate_bounding_boxes_from_text(text_content, image.size)

            return OcrResponse(
                text_content=text_content,
                bounding_boxes=bounding_boxes,
                confidence=None  # Qwen 3 doesn't provide confidence scores directly
            )
        except Exception as e:
            logging.error(f"Qwen 3 OCR inference failed: {e}")
            raise

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], language: str = 'sa', prompt: Optional[str] = None, **kwargs) -> OcrResponse:
        """Run Qwen 3 OCR on a specific selection of the image."""
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
            logging.error(f"Qwen 3 OCR with selection failed: {e}")
            raise

    def _extract_text_from_response(self, response: str, prompt: str) -> str:
        """Extract text content from Qwen 3 response."""
        # Remove the prompt from the response
        if prompt in response:
            text_content = response.replace(prompt, "").strip()
        else:
            text_content = response.strip()
        
        # Clean up any remaining artifacts
        text_content = re.sub(r'^[^a-zA-Z\u0900-\u097F\u0A00-\u0A7F\u0B00-\u0B7F\u0C00-\u0C7F\u0D00-\u0D7F\u0E00-\u0E7F\u0F00-\u0F7F\u1000-\u109F\u1100-\u11FF\u1200-\u137F\u13A0-\u13FF\u1400-\u167F\u1680-\u169F\u16A0-\u16FF\u1700-\u171F\u1720-\u173F\u1740-\u175F\u1760-\u177F\u1780-\u17FF\u1800-\u18AF\u1900-\u194F\u1950-\u197F\u1980-\u19DF\u19E0-\u19FF\u1A00-\u1A1F\u1A20-\u1AAF\u1AB0-\u1AFF\u1B00-\u1B7F\u1B80-\u1BBF\u1BC0-\u1BFF\u1C00-\u1C4F\u1C50-\u1C7F\u1C80-\u1CBF\u1CC0-\u1CFF\u1CD0-\u1CFF\u1D00-\u1D7F\u1D80-\u1DBF\u1DC0-\u1DFF\u1E00-\u1EFF\u1F00-\u1FFF\u2000-\u206F\u2070-\u209F\u20A0-\u20CF\u20D0-\u20FF\u2100-\u214F\u2150-\u218F\u2190-\u21FF\u2200-\u22FF\u2300-\u23FF\u2400-\u243F\u2440-\u245F\u2460-\u24FF\u2500-\u257F\u2580-\u259F\u25A0-\u25FF\u2600-\u26FF\u2700-\u27BF\u27C0-\u27EF\u27F0-\u27FF\u2800-\u28FF\u2900-\u297F\u2980-\u29FF\u2A00-\u2AFF\u2B00-\u2BFF\u2C00-\u2C5F\u2C60-\u2C7F\u2C80-\u2CFF\u2D00-\u2D2F\u2D30-\u2D7F\u2D80-\u2DDF\u2DE0-\u2DFF\u2E00-\u2E7F\u2E80-\u2EFF\u2F00-\u2FDF\u2FF0-\u2FFF\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\u3100-\u312F\u3130-\u318F\u3190-\u319F\u31A0-\u31BF\u31C0-\u31EF\u31F0-\u31FF\u3200-\u32FF\u3300-\u33FF\u3400-\u4DBF\u4DC0-\u4DFF\u4E00-\u9FFF\uA000-\uA48F\uA490-\uA4CF\uA4D0-\uA4FF\uA500-\uA63F\uA640-\uA69F\uA6A0-\uA6FF\uA700-\uA71F\uA720-\uA7FF\uA800-\uA82F\uA830-\uA83F\uA840-\uA87F\uA880-\uA8DF\uA8E0-\uA8FF\uA900-\uA92F\uA930-\uA95F\uA960-\uA97F\uA980-\uA9DF\uA9E0-\uA9FF\uAA00-\uAA5F\uAA60-\uAA7F\uAA80-\uAADF\uAAE0-\uAAFF\uAB00-\uAB2F\uAB30-\uABBF\uABC0-\uABFF\uABD0-\uABDF\uABE0-\uABFF\uAC00-\uD7AF\uD7B0-\uD7FF\uD800-\uDB7F\uDB80-\uDBFF\uDC00-\uDFFF\uE000-\uF8FF\uF900-\uFAFF\uFB00-\uFB4F\uFB50-\uFDFF\uFE00-\uFE0F\uFE10-\uFE1F\uFE20-\uFE2F\uFE30-\uFE4F\uFE50-\uFE6F\uFE70-\uFEFF\uFF00-\uFFEF\uFFF0-\uFFFF]+', '', text_content, flags=re.MULTILINE)
        
        return text_content.strip()

    def _generate_bounding_boxes_from_text(self, text_content: str, image_size: Tuple[int, int]) -> List[Tuple[int, int, int, int, str]]:
        """Generate bounding boxes from text content.
        
        This is a simplified approach that creates line-level bounding boxes.
        """
        if not text_content.strip():
            return []

        lines = [line.strip() for line in text_content.split('\n') if line.strip()]
        boxes = []
        image_width, image_height = image_size
        line_height = image_height // max(len(lines), 1)

        for i, line in enumerate(lines):
            # Approximate box for each line (x1, y1, x2, y2, text)
            box = (0, i * line_height, image_width, (i + 1) * line_height, line)
            boxes.append(box)
        
        return boxes

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Qwen 3 OCR."""
        # Qwen 3 supports 100+ languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms', 'zh-cn', 'zh-tw', 'ja', 'ko', 'th', 'vi', 'id', 'ms', 'tl', 'my', 'km', 'lo', 'ne', 'si', 'dz', 'bo', 'ug', 'mn', 'kk', 'ky', 'uz', 'tg', 'az', 'tk', 'ka', 'hy', 'am', 'ti', 'om', 'so', 'sw', 'zu', 'xh', 'af', 'sq', 'eu', 'be', 'bg', 'hr', 'cs', 'da', 'et', 'fi', 'gl', 'hu', 'is', 'ga', 'lv', 'lt', 'mk', 'mt', 'no', 'ro', 'sk', 'sl', 'sv', 'uk', 'cy', 'he', 'yi', 'jv', 'su', 'ceb', 'haw', 'mg', 'mi', 'sm', 'to', 'ty', 've', 'wo', 'yo', 'zu']
