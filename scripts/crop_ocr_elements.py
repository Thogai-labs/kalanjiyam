#!/usr/bin/env python3
"""
crop_ocr_elements.py

A Python utility to parse GLM-OCR layout JSON files, scale and crop document visual elements
(figures, images, diagrams, pictures) using PyMuPDF and Pillow.
"""

import os
import json
import argparse
from typing import List

# Import cropping logic from app package
from kalanjiyam.utils.ocr_cropper import (
    crop_elements_from_image,
    crop_elements_from_pdf,
    DEFAULT_IMAGE_TYPES
)

def process_document(
    doc_path: str,
    json_path: str,
    output_dir: str,
    page_num: int = 0
) -> List[str]:
    """
    Helper function to load the JSON payload, check file type of doc_path, and route to proper cropper.
    """
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"Source document not found: {doc_path}")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON layout file not found: {json_path}")

    # Load layout JSON
    with open(json_path, encoding='utf-8') as f:
        layout_data = json.load(f)

    # Route based on extension/type
    ext = os.path.splitext(doc_path)[1].lower()
    
    if ext == ".pdf":
        return crop_elements_from_pdf(doc_path, layout_data, output_dir, page_num=page_num)
    else:
        # Attempt to open as image
        try:
            return crop_elements_from_image(doc_path, layout_data, output_dir)
        except Exception as e:
            raise ValueError(f"Unsupported document format or error loading file as image: {doc_path}. Error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crop visual elements from a document using GLM-OCR layout coordinates.")
    parser.add_argument("--doc", required=True, help="Path to the source document (PDF or Image)")
    parser.add_argument("--json-file", required=True, help="Path to the GLM-OCR layout JSON response")
    parser.add_argument("--output-dir", default="extracted_elements", help="Directory to save the cropped images")
    parser.add_argument("--page", type=int, default=0, help="PDF page index to crop from (0-indexed, default is 0)")

    args = parser.parse_args()

    try:
        results = process_document(args.doc, args.json_file, args.output_dir, page_num=args.page)
        print(f"\nSuccessfully cropped {len(results)} visual element(s).")
    except Exception as exc:
        print(f"\nError processing document: {exc}")
        exit(1)
