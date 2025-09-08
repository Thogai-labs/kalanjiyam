import logging
import os
import gc
from pathlib import Path
from typing import List, Optional, Dict
from PIL import Image

# Reuse the existing OCR response dataclass
from kalanjiyam.utils.google_ocr import OcrResponse

# Docling imports
try:
    from docling.document_converter import DocumentConverter
except ImportError as e:
    logging.error(
        "Docling is not installed. Please install it via `pip install docling`.")
    raise RuntimeError("Docling is not installed.") from e


def get_gpu_config() -> Dict:
    # Placeholder for GPU config if Docling requires it; for now return empty or CPU only
    return {
        # Docling currently runs on CPU or specific VLM devices with extra config if needed
        "device": "cpu",
    }


def post_process(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    return " ".join(text.split())  # Normalize whitespace


def run(file_path: Path, language: str = "sa", gpu_config: Optional[Dict] = None) -> OcrResponse:
    """
    Run Docling OCR on the given document file/image.

    Args:
        file_path: Path to local file or URL string.
        language: Primary language code (for now used for reference).
        gpu_config: GPU config if applicable (currently unused).

    Returns:
        OcrResponse with extracted text and bounding boxes.
    """
    logging.debug(
        f"Starting Docling OCR for {file_path} with language {language}")

    # Validate file
    if not file_path.exists() or not file_path.is_file():
        raise RuntimeError(f"Invalid file path: {file_path}")

    # Initialize converter
    converter = DocumentConverter()

    try:
        # Convert document using Docling
        result = converter.convert(str(file_path))

        # Extract text as markdown string
        text_content = result.document.export_to_markdown()

        # TODO: Docling does not return bounding boxes in typical usage;
        # for now, leave bounding_boxes empty or implement extraction if available.
        bounding_boxes = []

        # Post process extracted text
        text_content = post_process(text_content)

        return OcrResponse(text_content=text_content, bounding_boxes=bounding_boxes)

    except Exception as e:
        logging.error(f"Docling OCR failed: {e}")
        raise RuntimeError(f"Docling OCR failed: {e}")

    finally:
        gc.collect()


def run_with_selection(
    file_path: Path, selection: Dict[str, int], language: str = "sa", gpu_config: Optional[Dict] = None
) -> OcrResponse:
    """
    Run Docling OCR on a specific selection (crop) of the image/document.

    Args:
        file_path: Path to image file.
        selection: Dict with x1, y1, x2, y2 (pixel coordinates).
        language: Language code.
        gpu_config: Optional GPU config.

    Returns:
        OcrResponse with extracted text and bounding boxes.
    """
    from PIL import Image

    logging.debug(f"Starting Docling OCR with selection: {file_path}")

    if not file_path.exists() or not file_path.is_file():
        raise RuntimeError(f"Invalid file path: {file_path}")

    image = Image.open(file_path).convert("RGB")

    # Crop to selection area
    x1 = selection.get("x1", 0)
    y1 = selection.get("y1", 0)
    x2 = selection.get("x2", image.width)
    y2 = selection.get("y2", image.height)

    cropped_image = image.crop((x1, y1, x2, y2))

    # Save cropped to a temporary file for Docling API
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        cropped_image.save(temp_file.name)
        temp_path = Path(temp_file.name)

    try:
        ocr_response = run(temp_path, language=language, gpu_config=gpu_config)
        # Adjust bounding boxes by adding offset if bounding boxes are returned in the future
        return ocr_response
    finally:
        if temp_path.exists():
            temp_path.unlink()


def get_supported_languages() -> List[str]:
    # Docling supports many languages, but language parameter does not restrict output yet.
    # Provide general Indic + common languages as example.
    return [
        "sa", "en", "hi", "te", "mr", "bn", "gu", "kn", "ml", "ta", "pa", "or", "ur"
    ]


def serialize_bounding_boxes(boxes: List) -> str:
    # Docling currently does not provide bounding boxes; return empty list
    return "[]"
