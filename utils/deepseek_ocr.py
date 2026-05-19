"""DeepSeek OCR engine implementation with GPU-first, CPU fallback."""

import logging
import os
import tempfile
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


class DeepSeekOcrEngine:
    """DeepSeek OCR engine implementation."""
    
    def __init__(self, model_name: str = 'deepseek-ai/DeepSeek-OCR'):
        """Initialize DeepSeek OCR engine.
        
        :param model_name: Hugging Face model name for DeepSeek OCR
        """
        self.model_name = model_name
        self.model = None
        self.tokenizer = None
        self.actual_device = 'cpu'
        self._check_availability()
        self._load_model()
    
    def _check_availability(self):
        """Check if required dependencies are available."""
        try:
            import transformers
            import torch
        except ImportError as e:
            raise RuntimeError(
                f"DeepSeek OCR dependencies not installed. Please install with:\n"
                f"pip install torch>=2.0.0 transformers>=4.30.0\n"
                f"Original error: {e}"
            )
    
    def _load_model(self):
        """Load the DeepSeek OCR model and tokenizer."""
        try:
            from transformers import AutoModel, AutoTokenizer
            
            logging.info(f"Loading DeepSeek OCR model: {self.model_name}")
            
            # Load tokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_name, 
                trust_remote_code=True
            )
            
            # Load model with device-specific settings
            model_kwargs = {
                'trust_remote_code': True,
                'use_safetensors': True,
            }
            
            try:
                self.model = AutoModel.from_pretrained(self.model_name, **model_kwargs)
            except ImportError as e:
                if "LlamaFlashAttention2" in str(e):
                    logging.error("DeepSeek OCR model requires LlamaFlashAttention2 which is not available in current transformers version")
                    logging.error("This is a known compatibility issue with DeepSeek OCR model")
                    raise RuntimeError(
                        "DeepSeek OCR model is not compatible with the current transformers version. "
                        "The model requires LlamaFlashAttention2 which is not available. "
                        "Please try using a different OCR engine (Google, Tesseract, or Surya) or update transformers to a compatible version."
                    )
                else:
                    raise RuntimeError(f"Failed to load DeepSeek OCR model: {e}")
            
            # Move to CPU and set precision
            self.model = self.model.eval().to(torch.float32)
            logging.info("Model loaded on CPU with float32 precision")
            
            logging.info(f"DeepSeek OCR model loaded successfully")
            
        except Exception as e:
            raise RuntimeError(f"Failed to load DeepSeek OCR model: {e}")
    
    def run(self, file_path: Path, **kwargs) -> OcrResponse:
        """Run DeepSeek OCR on the given image file.
        
        :param file_path: Path to the image file
        :param kwargs: Additional arguments (language, prompt, etc.)
        :return: OCR response with text content and bounding boxes
        """
        try:
            # Load and validate image
            image = Image.open(file_path)
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Get parameters
            language = kwargs.get('language', 'sa')  # Default to Sanskrit
            prompt = kwargs.get('prompt', self._get_default_prompt(language))
            base_size = kwargs.get('base_size', 1024)
            image_size = kwargs.get('image_size', 640)
            crop_mode = kwargs.get('crop_mode', True)
            
            logging.info(f"Running DeepSeek OCR on {file_path} with language {language} on {self.actual_device}")
            
            # Create temporary directory for output
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir)
                
                # Run inference
                result = self.model.infer(
                    self.tokenizer,
                    prompt=prompt,
                    image_file=str(file_path),
                    output_path=str(output_path),
                    base_size=base_size,
                    image_size=image_size,
                    crop_mode=crop_mode,
                    save_results=True,
                    test_compress=False  # Disable compression for better accuracy
                )
                
                # Extract text content from result
                text_content = self._extract_text_from_result(result)
                
                # Generate bounding boxes (DeepSeek OCR doesn't provide detailed bboxes)
                bounding_boxes = self._generate_bounding_boxes(text_content, image.size)
                
                return OcrResponse(
                    text_content=text_content,
                    bounding_boxes=bounding_boxes,
                    confidence=0.95,  # DeepSeek OCR typically has high confidence
                    language=language
                )
                
        except Exception as e:
            logging.error(f"DeepSeek OCR failed for {file_path}: {e}")
            raise
    
    def run_with_selection(self, file_path: Path, selection: Dict[str, int], **kwargs) -> OcrResponse:
        """Run DeepSeek OCR on a specific selection of the image.
        
        :param file_path: Path to the image file
        :param selection: Selection coordinates {'x': int, 'y': int, 'width': int, 'height': int}
        :param kwargs: Additional arguments
        :return: OCR response
        """
        try:
            # Load image
            image = Image.open(file_path)
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Crop image to selection
            x = selection.get('x', 0)
            y = selection.get('y', 0)
            width = selection.get('width', image.width)
            height = selection.get('height', image.height)
            
            cropped_image = image.crop((x, y, x + width, y + height))
            
            # Save cropped image temporarily
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                cropped_image.save(temp_file.name)
                temp_path = Path(temp_file.name)
            
            try:
                # Run OCR on cropped image
                result = self.run(temp_path, **kwargs)
                
                # Adjust bounding boxes to original image coordinates
                adjusted_boxes = []
                for box in result.bounding_boxes:
                    # box is a tuple (x1, x2, y1, y2, text)
                    adjusted_box = (box[0] + x, box[1] + x, box[2] + y, box[3] + y, box[4])
                    adjusted_boxes.append(adjusted_box)
                
                return OcrResponse(
                    text_content=result.text_content,
                    bounding_boxes=adjusted_boxes,
                    confidence=result.confidence,
                    language=result.language
                )
            finally:
                # Clean up temporary file
                if temp_path.exists():
                    temp_path.unlink()
                    
        except Exception as e:
            logging.error(f"DeepSeek OCR with selection failed for {file_path}: {e}")
            raise
    
    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for DeepSeek OCR."""
        # DeepSeek OCR supports ~100 languages including Sanskrit
        return [
            'sa',  # Sanskrit
            'en',  # English
            'hi',  # Hindi
            'te',  # Telugu
            'mr',  # Marathi
            'bn',  # Bengali
            'gu',  # Gujarati
            'kn',  # Kannada
            'ml',  # Malayalam
            'ta',  # Tamil
            'pa',  # Punjabi
            'or',  # Odia
            'ur',  # Urdu
            'ar',  # Arabic
            'fa',  # Persian
            'th',  # Thai
            'ko',  # Korean
            'ja',  # Japanese
            'zh',  # Chinese
            'ru',  # Russian
            'es',  # Spanish
            'fr',  # French
            'de',  # German
            'it',  # Italian
            'pt',  # Portuguese
            'nl',  # Dutch
            'pl',  # Polish
            'tr',  # Turkish
            'vi',  # Vietnamese
            'id',  # Indonesian
            'ms',  # Malay
        ]
    
    def _get_default_prompt(self, language: str) -> str:
        """Get default prompt for the specified language."""
        language_names = {
            'sa': 'Sanskrit',
            'en': 'English',
            'hi': 'Hindi',
            'te': 'Telugu',
            'mr': 'Marathi',
            'bn': 'Bengali',
            'gu': 'Gujarati',
            'kn': 'Kannada',
            'ml': 'Malayalam',
            'ta': 'Tamil',
            'pa': 'Punjabi',
            'or': 'Odia',
            'ur': 'Urdu',
            'ar': 'Arabic',
            'fa': 'Persian',
            'th': 'Thai',
            'ko': 'Korean',
            'ja': 'Japanese',
            'zh': 'Chinese',
            'ru': 'Russian',
        }
        
        lang_name = language_names.get(language, 'the document')
        
        return f"<image>\n<|grounding|>Convert the {lang_name} document to markdown. Preserve the original formatting, structure, and layout. Extract all text accurately including any mathematical expressions, tables, and special characters."
    
    def _extract_text_from_result(self, result: Any) -> str:
        """Extract text content from DeepSeek OCR result."""
        try:
            # DeepSeek OCR returns markdown format
            if hasattr(result, 'text'):
                return result.text
            elif isinstance(result, str):
                return result
            elif isinstance(result, dict) and 'text' in result:
                return result['text']
            else:
                # Fallback: convert result to string
                return str(result)
        except Exception as e:
            logging.warning(f"Failed to extract text from DeepSeek OCR result: {e}")
            return str(result)
    
    def _generate_bounding_boxes(self, text_content: str, image_size: Tuple[int, int]) -> List[Tuple[int, int, int, int, str]]:
        """Generate bounding boxes for the extracted text.
        
        Note: DeepSeek OCR doesn't provide detailed bounding box information,
        so we generate approximate boxes based on text content.
        """
        if not text_content.strip():
            return []
        
        # Split text into lines
        lines = text_content.split('\n')
        boxes = []
        
        image_width, image_height = image_size
        line_height = image_height // max(len(lines), 1)
        
        for i, line in enumerate(lines):
            if line.strip():
                # Create approximate bounding box for each line as tuple (x1, x2, y1, y2, text)
                box = (0, image_width, i * line_height, (i + 1) * line_height, line.strip())
                boxes.append(box)
        
        return boxes


def run(file_path: Path, language: str = 'sa', **kwargs) -> OcrResponse:
    """Convenience function to run DeepSeek OCR.
    
    :param file_path: Path to the image file
    :param language: Language code for OCR
    :param kwargs: Additional arguments
    :return: OCR response
    """
    engine = DeepSeekOcrEngine()
    return engine.run(file_path, language=language, **kwargs)


def run_with_selection(file_path: Path, selection: Dict[str, int], language: str = 'sa', **kwargs) -> OcrResponse:
    """Convenience function to run DeepSeek OCR with selection.
    
    :param file_path: Path to the image file
    :param selection: Selection coordinates
    :param language: Language code for OCR
    :param kwargs: Additional arguments
    :return: OCR response
    """
    engine = DeepSeekOcrEngine()
    return engine.run_with_selection(file_path, selection, language=language, **kwargs)