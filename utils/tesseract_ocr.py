"""Tesseract OCR utilities for proofing projects."""

import logging
from pathlib import Path
from typing import List, Tuple

import pytesseract
from PIL import Image

from kalanjiyam.utils import google_ocr

# Use the OcrResponse from google_ocr as the common interface
OcrResponse = google_ocr.OcrResponse


def post_process(text: str) -> str:
    """Post process OCR text."""
    return (
        text
        # Danda and double danda
        .replace("||", "॥")
        .replace("|", "।")
        .replace("।।", "॥")
        # Remove curly quotes
        .replace("'", "'")
        .replace("'", "'")
        .replace(""", '"')
        .replace(""", '"')
    )


def serialize_bounding_boxes(boxes: List[Tuple[int, int, int, int, str]]) -> str:
    """Serialize a list of bounding boxes as a TSV."""
    return "\n".join("\t".join(str(x) for x in row) for row in boxes)


def run(file_path: Path, language: str = 'san') -> OcrResponse:
    """Run Tesseract OCR over the given image.

    :param file_path: path to the image we'll process with OCR.
    :param language: language code for Tesseract (default: 'san' for Sanskrit).
    :return: an OCR response containing the image's text content and
        bounding boxes.
    """
    logging.debug(f"Starting Tesseract OCR: {file_path} with language {language}")

    # Open the image
    image = Image.open(file_path)
    
    # Get text content
    text_content = pytesseract.image_to_string(image, lang=language)
    text_content = post_process(text_content)
    
    # Get bounding boxes for words
    bounding_boxes = []
    try:
        # Get word-level bounding boxes
        data = pytesseract.image_to_data(image, lang=language, output_type=pytesseract.Output.DICT)
        
        for i, text in enumerate(data['text']):
            if text.strip():  # Only process non-empty text
                x = data['left'][i]
                y = data['top'][i]
                width = data['width'][i]
                height = data['height'][i]
                
                # Convert to (x1, y1, x2, y2, text) format
                x1, y1 = x, y
                x2, y2 = x + width, y + height
                bounding_boxes.append((x1, y1, x2, y2, text))
    except Exception as e:
        logging.warning(f"Failed to get bounding boxes from Tesseract: {e}")
        # If bounding boxes fail, we still have the text content
    
    return OcrResponse(text_content=text_content, bounding_boxes=bounding_boxes)


def run_with_selection(file_path: Path, selection: dict, language: str = 'san') -> OcrResponse:
    """Run Tesseract OCR on a specific selection of the image.

    :param file_path: path to the image we'll process with OCR.
    :param selection: dictionary with 'left', 'top', 'width', 'height' keys.
    :param language: language code for Tesseract (default: 'san' for Sanskrit).
    :return: an OCR response containing the image's text content and
        bounding boxes.
    """
    logging.debug(f"Starting Tesseract OCR on selection: {file_path} with language {language}")
    
    # Open the image
    image = Image.open(file_path)
    
    # Crop the image to the selection
    left, top, width, height = selection['left'], selection['top'], selection['width'], selection['height']
    selection_image = image.crop((left, top, left + width, top + height))
    
    # Get text content from the selection
    text_content = pytesseract.image_to_string(selection_image, lang=language)
    text_content = post_process(text_content)
    
    # For selections, we don't have detailed bounding boxes, so return empty list
    bounding_boxes = []
    
    return OcrResponse(text_content=text_content, bounding_boxes=bounding_boxes) 