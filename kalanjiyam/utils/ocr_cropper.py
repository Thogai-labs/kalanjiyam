import os
import io
import logging
from typing import List, Dict, Any, Optional
from PIL import Image
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

DEFAULT_IMAGE_TYPES = {"figure", "image", "picture", "diagram"}

def save_crop_to_storage(
    cropped_img: Image.Image,
    block_id: Any,
    project_slug: Optional[str] = None,
    output_dir: Optional[str] = None,
    file_prefix: Optional[str] = None,
) -> str:
    """
    Save the cropped image either to the application's Storage backend (if project_slug is provided)
    or to a local directory. Returns the path or key where it was saved.
    """
    if file_prefix:
        clean_prefix = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(file_prefix))
        filename = f"extracted_{clean_prefix}_{block_id}.png"
    else:
        filename = f"extracted_{block_id}.png"

    if project_slug:
        try:
            from kalanjiyam.utils.storage import get_storage, editor_image_key
            
            # Save to in-memory byte array
            img_byte_arr = io.BytesIO()
            cropped_img.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)

            # Store in application storage
            storage = get_storage()
            key = editor_image_key(project_slug, filename)
            storage.save(key, img_byte_arr)
            
            # Update storage usage log
            try:
                from kalanjiyam.utils.storage import add_storage_usage_for_project
                add_storage_usage_for_project(project_slug)
            except Exception:
                pass

            logger.info(f"Saved cropped block {block_id} to storage key: {key}")
            return f"/static/uploads/{project_slug}/images/{filename}"
        except Exception as e:
            logger.exception(f"Failed to save crop {block_id} to Storage backend: {e}")

    # Fallback to local filesystem saving
    if not output_dir:
        output_dir = "extracted_elements"
    
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)
    cropped_img.save(output_path, "PNG")
    logger.info(f"Saved cropped block {block_id} locally to: {output_path}")
    return output_path

def crop_elements_from_image(
    image_path: str,
    layout_data: Dict[str, Any],
    project_slug: Optional[str] = None,
    output_dir: Optional[str] = None,
    image_types: set = DEFAULT_IMAGE_TYPES,
    file_prefix: Optional[str] = None,
) -> List[str]:
    """
    Scale the source image to match layout coordinates and crop identified visual elements.
    Updates layout_data block content to contain the img tag referencing the cropped image.
    """
    page_width = layout_data.get("page_width")
    page_height = layout_data.get("page_height")

    if not page_width or not page_height:
        logger.warning("JSON layout data must contain 'page_width' and 'page_height'. Skipping crop.")
        return []

    if not os.path.exists(image_path):
        logger.warning(f"Source image not found: {image_path}. Skipping crop.")
        return []

    if not file_prefix and image_path:
        from pathlib import Path
        file_prefix = Path(image_path).stem

    try:
        with Image.open(image_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            
            scaled_img = img.resize((page_width, page_height), Image.Resampling.LANCZOS)

            saved_paths = []
            blocks = layout_data.get("blocks", []) or []
            for block in blocks:
                block_type = str(block.get("type", "")).lower()
                block_id = block.get("id")
                bbox = block.get("bbox")

                if block_type in image_types:
                    if not bbox or len(bbox) != 4:
                        continue
                    if block_id is None:
                        continue

                    xmin, ymin, xmax, ymax = map(float, bbox)
                    
                    xmin = max(0.0, min(xmin, float(page_width)))
                    ymin = max(0.0, min(ymin, float(page_height)))
                    xmax = max(0.0, min(xmax, float(page_width)))
                    ymax = max(0.0, min(ymax, float(page_height)))

                    if xmax <= xmin or ymax <= ymin:
                        continue

                    cropped = scaled_img.crop((int(xmin), int(ymin), int(xmax), int(ymax)))
                    saved_path = save_crop_to_storage(
                        cropped, block_id, project_slug=project_slug, output_dir=output_dir, file_prefix=file_prefix
                    )
                    saved_paths.append(saved_path)

                    # Update block content to display the image
                    if project_slug:
                        block["content"] = f'<img class="max-w-full max-h-full h-auto rounded-lg object-contain mx-auto" src="{saved_path}">'
                        block["type"] = "paragraph"
            return saved_paths
    except Exception as e:
        logger.exception(f"Failed to crop elements from image {image_path}: {e}")
        return []

def crop_elements_from_pdf(
    pdf_path: str,
    layout_data: Dict[str, Any],
    project_slug: Optional[str] = None,
    output_dir: Optional[str] = None,
    page_num: int = 0,
    image_types: set = DEFAULT_IMAGE_TYPES,
    file_prefix: Optional[str] = None,
) -> List[str]:
    """
    Render the PDF page directly at the layout coordinate resolution and crop identified visual elements.
    Updates layout_data block content to contain the img tag referencing the cropped image.
    """
    page_width = layout_data.get("page_width")
    page_height = layout_data.get("page_height")

    if not page_width or not page_height:
        logger.warning("JSON layout data must contain 'page_width' and 'page_height'. Skipping crop.")
        return []

    if not os.path.exists(pdf_path):
        logger.warning(f"Source PDF not found: {pdf_path}. Skipping crop.")
        return []

    if not file_prefix:
        from pathlib import Path
        file_prefix = f"{Path(pdf_path).stem}_p{page_num + 1}"

    try:
        doc = fitz.open(pdf_path)
        if page_num < 0 or page_num >= len(doc):
            logger.warning(f"Page index {page_num} is out of bounds for the PDF. Skipping crop.")
            doc.close()
            return []

        page = doc[page_num]
        orig_w = page.rect.width
        orig_h = page.rect.height

        zoom_x = page_width / orig_w
        zoom_y = page_height / orig_h
        matrix = fitz.Matrix(zoom_x, zoom_y)

        pix = page.get_pixmap(matrix=matrix)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        saved_paths = []
        blocks = layout_data.get("blocks", []) or []
        for block in blocks:
            block_type = str(block.get("type", "")).lower()
            block_id = block.get("id")
            bbox = block.get("bbox")

            if block_type in image_types:
                if not bbox or len(bbox) != 4:
                    continue
                if block_id is None:
                    continue

                xmin, ymin, xmax, ymax = map(float, bbox)

                xmin = max(0.0, min(xmin, float(page_width)))
                ymin = max(0.0, min(ymin, float(page_height)))
                xmax = max(0.0, min(xmax, float(page_width)))
                ymax = max(0.0, min(ymax, float(page_height)))

                if xmax <= xmin or ymax <= ymin:
                    continue

                cropped = img.crop((int(xmin), int(ymin), int(xmax), int(ymax)))
                saved_path = save_crop_to_storage(
                    cropped, block_id, project_slug=project_slug, output_dir=output_dir, file_prefix=file_prefix
                )
                saved_paths.append(saved_path)

                # Update block content to display the image
                if project_slug:
                    block["content"] = f'<img class="max-w-full h-auto rounded-lg" src="{saved_path}">'
                    block["type"] = "paragraph"

        doc.close()
        return saved_paths
    except Exception as e:
        logger.exception(f"Failed to crop elements from PDF {pdf_path}: {e}")
        return []

def crop_ocr_response_elements(
    doc_path: str,
    ocr_response: Any,
    project_slug: Optional[str] = None,
    output_dir: Optional[str] = None,
    page_num: int = 0
) -> List[str]:
    """
    Crop visual elements from OCR response object / dict.
    Updates ocr_response blocks in-place with the proper display img tag.
    """
    if not doc_path or not ocr_response:
        return []

    from pathlib import Path
    file_prefix = Path(doc_path).stem if doc_path else None

    # Get blocks list (it could be a list of dicts or custom objects)
    raw_blocks = getattr(ocr_response, "blocks", []) or []
    
    # We must support updating in-place. If they are custom objects (like Block class),
    # or dicts, we wrap them in a unified format for croppers.
    blocks_as_dicts = []
    for b in raw_blocks:
        if isinstance(b, dict):
            blocks_as_dicts.append(b)
        else:
            # Assume custom object (Block class)
            blocks_as_dicts.append({
                "id": getattr(b, "id", None),
                "type": getattr(b, "type", None),
                "bbox": getattr(b, "bbox", None),
                "content": getattr(b, "content", "")
            })

    layout_data = {
        "page_width": getattr(ocr_response, "page_width", None),
        "page_height": getattr(ocr_response, "page_height", None),
        "blocks": blocks_as_dicts
    }

    if not layout_data["page_width"] or not layout_data["page_height"]:
        return []

    ext = os.path.splitext(doc_path)[1].lower()
    if ext == ".pdf":
        saved_paths = crop_elements_from_pdf(
            doc_path, layout_data, project_slug=project_slug, output_dir=output_dir, page_num=page_num, file_prefix=file_prefix
        )
    else:
        saved_paths = crop_elements_from_image(
            doc_path, layout_data, project_slug=project_slug, output_dir=output_dir, file_prefix=file_prefix
        )

    # Sync content back to the original objects in ocr_response.blocks if they were updated
    for original, updated in zip(raw_blocks, blocks_as_dicts):
        if "content" in updated:
            if isinstance(original, dict):
                original["content"] = updated["content"]
                original["type"] = updated.get("type", original.get("type"))
            else:
                setattr(original, "content", updated["content"])
                setattr(original, "type", updated.get("type", getattr(original, "type", "paragraph")))

    return saved_paths
