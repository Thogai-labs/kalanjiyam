"""Surya OCR utilities for proofing projects."""
import logging
import subprocess
import tempfile
import json
import os
import gc
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from PIL import Image

from kalanjiyam.utils import google_ocr
OcrResponse = google_ocr.OcrResponse


def get_gpu_config() -> Dict[str, Any]:
    """
    Get GPU configuration from environment variables.
    
    Returns:
        Dictionary with GPU configuration settings
    """
    config = {
        'device': os.environ.get('SURYA_GPU_DEVICE', 'auto'),  # 'auto', 'cuda:0', 'cuda:1', 'cpu'
        'memory_fraction': float(os.environ.get('SURYA_GPU_MEMORY_FRACTION', '0.8')),  # Use 80% of GPU memory by default
        'max_memory_mb': int(os.environ.get('SURYA_GPU_MAX_MEMORY_MB', '0')),  # 0 = no limit
        'allow_growth': os.environ.get('SURYA_GPU_ALLOW_GROWTH', 'true').lower() == 'true',
    }
    
    # Auto-detect GPU if not specified
    if config['device'] == 'auto':
        if os.environ.get('CUDA_VISIBLE_DEVICES'):
            # Use the first available GPU
            gpu_id = os.environ.get('CUDA_VISIBLE_DEVICES').split(',')[0]
            config['device'] = f'cuda:{gpu_id}'
        else:
            # Check if CUDA is available
            try:
                import torch
                if torch.cuda.is_available():
                    config['device'] = 'cuda:0'
                else:
                    config['device'] = 'cpu'
            except ImportError:
                config['device'] = 'cpu'
    
    return config


# Import the setup function from the config module
from kalanjiyam.utils.surya_gpu_config import setup_gpu_environment


def post_process(text: str) -> str:
    """Post-process OCR text."""
    if not text:
        return ""
    
    # Clean up common OCR artifacts
    text = text.strip()
    # Remove excessive whitespace
    text = ' '.join(text.split())
    return text


def serialize_bounding_boxes(boxes: List[Tuple[int, int, int, int, str]]) -> str:
    """Serialize bounding boxes to JSON string."""
    return json.dumps([{
        'x1': box[0], 'y1': box[1], 'x2': box[2], 'y2': box[3], 'text': box[4]
    } for box in boxes])


def run(file_path: Path, language: str = 'sa', additional_languages: Optional[List[str]] = None, gpu_config: Optional[Dict[str, Any]] = None) -> OcrResponse:
    """
    Run Surya OCR on the given image file.
    
    Args:
        file_path: Path to the image file
        language: Primary language code (e.g., 'sa', 'en', 'hi')
        additional_languages: Optional list of additional language codes for bilingual/multilingual OCR
        gpu_config: Optional GPU configuration dictionary
    
    Returns:
        OcrResponse with text content and bounding boxes
    """
    logging.debug(f"Starting Surya OCR: {file_path} with language {language}")
    
    if not file_path.exists():
        raise RuntimeError(f"File does not exist: {file_path}")
    
    if not file_path.is_file():
        raise RuntimeError(f"Path is not a file: {file_path}")
    
    file_size = file_path.stat().st_size
    if file_size == 0:
        raise RuntimeError(f"File is empty: {file_path}")
    
    logging.info(f"Processing image file: {file_path}, size: {file_size} bytes")
    
    # Get and setup GPU configuration
    if gpu_config is None:
        gpu_config = get_gpu_config()
    
    logging.info(f"Surya OCR GPU configuration: {gpu_config}")
    setup_gpu_environment(gpu_config)
    
    # Force GPU usage if CUDA is available
    if gpu_config['device'].startswith('cuda'):
        try:
            import torch
            if torch.cuda.is_available():
                # Ensure we're using the correct GPU
                torch.cuda.set_device(0)  # Use first GPU
                logging.info(f"Force-set PyTorch to use GPU: {torch.cuda.get_device_name(0)}")
            else:
                logging.warning("CUDA not available despite GPU config - falling back to CPU")
                gpu_config['device'] = 'cpu'
        except ImportError:
            logging.warning("PyTorch not available - falling back to CPU")
            gpu_config['device'] = 'cpu'
    
    # Set conservative environment variables for Surya OCR
    os.environ.setdefault('COMPILE_DETECTOR', 'false')  # Disable compilation to save memory
    os.environ.setdefault('COMPILE_LAYOUT', 'false')    # Disable compilation to save memory
    os.environ.setdefault('COMPILE_TABLE_REC', 'false') # Disable compilation to save memory
    
    try:
        # Import Surya modules
        from surya.common.surya.schema import TaskNames
        from surya.detection import DetectionPredictor
        from surya.foundation import FoundationPredictor
        from surya.recognition import RecognitionPredictor
        
        # Load image with memory optimization
        image = Image.open(file_path)
        image = image.convert('RGB')
        
        # Resize large images to prevent memory issues (max 2048px on longest side)
        max_size = int(os.environ.get('SURYA_MAX_IMAGE_SIZE', '2048'))
        if max(image.size) > max_size:
            ratio = max_size / max(image.size)
            new_size = tuple(int(dim * ratio) for dim in image.size)
            image = image.resize(new_size, Image.Resampling.LANCZOS)
            logging.info(f"Resized image from {image.size} to {new_size} to save memory")
        
        # Initialize predictors with conservative settings
        foundation_predictor = FoundationPredictor()
        det_predictor = DetectionPredictor()
        rec_predictor = RecognitionPredictor(foundation_predictor)
        
        # Log device information and clear GPU memory
        try:
            import torch
            if torch.cuda.is_available():
                current_device = torch.cuda.current_device()
                device_name = torch.cuda.get_device_name(current_device)
                memory_allocated = torch.cuda.memory_allocated(current_device) / 1024**3  # GB
                memory_reserved = torch.cuda.memory_reserved(current_device) / 1024**3  # GB
                logging.info(f"Surya OCR using GPU: {device_name} (Device {current_device})")
                logging.info(f"GPU Memory - Allocated: {memory_allocated:.2f}GB, Reserved: {memory_reserved:.2f}GB")
                
                # Clear GPU memory before starting OCR
                torch.cuda.empty_cache()
                logging.info("Cleared GPU cache before OCR")
            else:
                logging.info("Surya OCR using CPU")
        except ImportError:
            logging.info("Surya OCR using CPU (PyTorch not available)")
        
        # Run OCR with the new API and conservative settings
        logging.info(f"Running Surya OCR with automatic language detection on {gpu_config['device']}")
        
        # Add timeout and better error handling
        try:
            predictions_by_image = rec_predictor(
                [image],
                task_names=[TaskNames.ocr_with_boxes],
                det_predictor=det_predictor,
                highres_images=[image],
                math_mode=os.environ.get('SURYA_MATH_MODE', 'false').lower() == 'true',  # Configurable math recognition
            )
        except Exception as e:
            logging.error(f"Surya OCR inference failed: {e}")
            # Clear GPU memory and try again
            if gpu_config['device'].startswith('cuda'):
                try:
                    import torch
                    torch.cuda.empty_cache()
                    logging.info("Cleared GPU cache after error")
                except ImportError:
                    pass
            raise
        
        # Extract text and bounding boxes from the first image result
        if not predictions_by_image:
            raise RuntimeError("No OCR results generated")
        
        prediction = predictions_by_image[0]
        text_content = ""
        bounding_boxes = []
        
        # Extract text lines and their bounding boxes
        for line in prediction.text_lines:
            line_text = post_process(line.text)
            if line_text:
                text_content += line_text + "\n"
                
                # Extract bounding box coordinates (already in x1, y1, x2, y2 format)
                if hasattr(line, 'bbox') and line.bbox:
                    bbox = line.bbox
                    if len(bbox) >= 4:
                        # bbox is already in [x1, y1, x2, y2] format
                        x1, y1, x2, y2 = bbox[0], bbox[1], bbox[2], bbox[3]
                        bounding_boxes.append((x1, y1, x2, y2, line_text))
        
        text_content = text_content.strip()
        logging.info(f"Surya OCR completed successfully. Extracted {len(bounding_boxes)} text lines")
        
        # Clean up memory
        del predictions_by_image, prediction, foundation_predictor, det_predictor, rec_predictor
        gc.collect()
        
        # Clear GPU cache if using CUDA
        if gpu_config['device'].startswith('cuda'):
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    logging.debug("Cleared GPU cache")
            except ImportError:
                pass
        
    except ImportError as e:
        import sys
        raise RuntimeError(
            f"Surya OCR is not installed in the current Python environment.\n"
            f"Python executable: {sys.executable}\n"
            f"Please install it with: pip install surya-ocr\n"
            f"For more information, see: https://github.com/datalab-to/surya\n"
            f"Import error: {e}"
        )
    except Exception as e:
        logging.error(f"Surya OCR failed: {e}")
        logging.warning("Falling back to Tesseract OCR")
        
        # Fallback to Tesseract OCR
        try:
            from kalanjiyam.utils import tesseract_ocr
            return tesseract_ocr.run(file_path, language=language)
        except Exception as fallback_error:
            logging.error(f"Tesseract fallback also failed: {fallback_error}")
            raise RuntimeError(f"Surya OCR failed: {e}. Fallback to Tesseract also failed: {fallback_error}")
    
    return OcrResponse(text_content=text_content, bounding_boxes=bounding_boxes)


def run_with_selection(file_path: Path, selection: dict, language: str = 'sa', additional_languages: Optional[List[str]] = None) -> OcrResponse:
    """
    Run Surya OCR on a specific selection of the image.
    
    Args:
        file_path: Path to the image file
        selection: Dictionary with 'x1', 'y1', 'x2', 'y2' coordinates
        language: Primary language code
        additional_languages: Optional list of additional language codes
    
    Returns:
        OcrResponse with text content and bounding boxes
    """
    logging.debug(f"Starting Surya OCR with selection: {file_path}")
    
    if not file_path.exists():
        raise RuntimeError(f"File does not exist: {file_path}")
    
    try:
        # Load image and crop to selection
        image = Image.open(file_path)
        image = image.convert('RGB')
        
        # Crop to selection area
        x1 = selection.get('x1', 0)
        y1 = selection.get('y1', 0)
        x2 = selection.get('x2', image.width)
        y2 = selection.get('y2', image.height)
        
        cropped_image = image.crop((x1, y1, x2, y2))
        
        # Save cropped image to temporary file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_file:
            cropped_image.save(temp_file.name)
            temp_path = Path(temp_file.name)
        
        try:
            # Run OCR on cropped image
            result = run(temp_path, language=language, additional_languages=additional_languages)
            
            # Adjust bounding box coordinates back to original image
            adjusted_boxes = []
            for box in result.bounding_boxes:
                adjusted_boxes.append((
                    box[0] + x1,  # x1
                    box[1] + y1,  # y1
                    box[2] + x1,  # x2
                    box[3] + y1,  # y2
                    box[4]        # text
                ))
            
            return OcrResponse(text_content=result.text_content, bounding_boxes=adjusted_boxes)
            
        finally:
            # Clean up temporary file
            if temp_path.exists():
                temp_path.unlink()
                
    except Exception as e:
        logging.error(f"Surya OCR with selection failed: {e}")
        raise RuntimeError(f"Surya OCR with selection failed: {e}")


def get_supported_languages() -> List[str]:
    """Get supported language codes for Surya OCR."""
    # Surya supports 90+ languages automatically
    # Return common language codes that users might want to specify
    return [
        'sa', 'en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur',
        'ar', 'fa', 'th', 'ko', 'ja', 'zh', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'nl',
        'pl', 'tr', 'vi', 'id', 'ms'
    ]
