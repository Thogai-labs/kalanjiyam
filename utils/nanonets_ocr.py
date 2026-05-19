"""Nanonets OCR engine implementation with GPU-first, CPU fallback."""

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


class NanonetsOcrEngine:
    """Nanonets OCR engine implementation."""
    
    def __init__(self, model_name: str = 'nanonets/Nanonets-OCR2-3B'):
        """Initialize Nanonets OCR engine.
        
        :param model_name: Hugging Face model name for Nanonets OCR
        """
        self.model_name = model_name
        self.model = None
        self.processor = None
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
                f"Nanonets OCR dependencies not installed. Please install with:\n"
                f"pip install torch>=2.0.0 transformers>=4.30.0 flash-attn\n"
                f"Original error: {e}"
            )
    
    def _load_model(self):
        """Load the Nanonets OCR model and processor with GPU-first, CPU fallback."""
        try:
            from transformers import AutoProcessor, AutoModelForImageTextToText
            
            logging.info(f"Loading Nanonets OCR model: {self.model_name}")
            
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
            self.processor = AutoProcessor.from_pretrained(self.model_name)
            logging.info("Nanonets OCR processor loaded successfully")
            
            # Load model with device-specific settings
            model_kwargs = {
                'dtype': 'auto',
                'device_map': 'auto' if device == 'cuda' else None,
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
            
            self.model = AutoModelForImageTextToText.from_pretrained(self.model_name, **model_kwargs)
            self.model.eval()
            
            if device == 'cuda':
                logging.info("Model loaded on CUDA GPU")
            else:
                logging.info("Model loaded on CPU")
            
            logging.info(f"Nanonets OCR model loaded successfully on {device}")
            
        except Exception as e:
            raise RuntimeError(f"Failed to load Nanonets OCR model: {e}")
    
    def run(self, file_path: Path, language: str = 'sa', prompt: Optional[str] = None, **kwargs) -> OcrResponse:
        """Run Nanonets OCR on the given image file.
        
        :param file_path: Path to the image file
        :param language: Language code (for compatibility, not used by Nanonets)
        :param prompt: Optional custom prompt
        :param kwargs: Additional arguments
        :return: OCR response with text content and bounding boxes
        """
        if self.model is None or self.processor is None:
            self._load_model()  # Ensure model is loaded

        try:
            image = Image.open(file_path).convert("RGB")

            # Default prompt for Nanonets OCR
            if prompt is None:
                prompt = """Extract all text from the document, preserving the original layout and formatting as much as possible.
1. Formatting: Return the output in HTML format. Use <b> or <strong> for bold text, <i> or <em> for italics, and correct heading tags (<h1>, <h2>, etc.) for titles.
2. Tables: Return tables in standard HTML format (<table>, <tr>, <td>).
3. Equations: Return equations in LaTeX format.
4. Images: For images, provide a description in an <img alt="description" /> tag.
5. Metadata: Wrap page numbers in <page_number>...</page_number> and watermarks in <watermark>...</watermark>.
6. Checkboxes: Use ☐ and ☑."""

            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": prompt},
                ]},
            ]

            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            inputs = self.processor(text=[text], images=[image], padding=True, return_tensors="pt").to(self.model.device)

            output_ids = self.model.generate(**inputs, max_new_tokens=4096, do_sample=False)
            generated_ids = [output_ids[len(input_ids):] for input_ids, output_ids in zip(inputs.input_ids, output_ids)]
            output_text = self.processor.batch_decode(generated_ids, skip_special_tokens=True, clean_up_tokenization_spaces=True)[0]

            # Generate bounding boxes from HTML output
            bounding_boxes = self._generate_bounding_boxes_from_html(output_text, image.size)

            return OcrResponse(
                text_content=output_text,
                bounding_boxes=bounding_boxes,
                confidence=None  # Nanonets OCR doesn't provide confidence scores
            )
        except Exception as e:
            logging.error(f"Nanonets OCR inference failed: {e}")
            raise

    def run_with_selection(self, file_path: Path, selection: Dict[str, int], language: str = 'sa', prompt: Optional[str] = None, **kwargs) -> OcrResponse:
        """Run Nanonets OCR on a specific selection of the image."""
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
            logging.error(f"Nanonets OCR with selection failed: {e}")
            raise

    def _generate_bounding_boxes_from_html(self, html_content: str, image_size: Tuple[int, int]) -> List[Tuple[int, int, int, int, str]]:
        """Generate bounding boxes from HTML output.
        
        Note: This is a simplified approach. For full fidelity, an HTML parser would be needed.
        """
        if not html_content.strip():
            return []

        # Extract text content from HTML (simplified)
        import re
        
        # Remove HTML tags and extract text
        text_content = re.sub(r'<[^>]+>', '', html_content)
        
        # Split into lines
        lines = text_content.split('\n')
        boxes = []

        image_width, image_height = image_size
        line_height = image_height // max(len(lines), 1)

        for i, line in enumerate(lines):
            if line.strip():
                # Create approximate bounding box for each line
                box = (0, image_width, i * line_height, (i + 1) * line_height, line.strip())
                boxes.append(box)

        return boxes

    def get_supported_languages(self) -> List[str]:
        """Get supported language codes for Nanonets OCR."""
        # Nanonets OCR supports many languages including Sanskrit
        return ['sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'pl', 'tr', 'vi', 'id', 'ms']
