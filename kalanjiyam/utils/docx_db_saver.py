"""Utility module to handle saving original DOCX data as gzipped JSON (.json.gz) in storage during direct translation when enabled by configuration."""

import logging
import uuid
from datetime import datetime
from slugify import slugify

LOG = logging.getLogger(__name__)


def save_original_docx_data(doc, docx_id: str, original_filename: str = None, creator_id: int = None):
    """Save the original DOCX document structure and text content as gzipped JSON (.json.gz) in storage.

    Stores the output payload under key `docx/saved/{docx_id}.json.gz` (mapping to `uploads/docx/saved/{docx_id}.json.gz`).
    """
    try:
        from kalanjiyam.utils.storage import get_storage, docx_saved_data_key
        from kalanjiyam.tasks.projects import _extract_docx_images, _segment_docx

        storage = get_storage()

        title = original_filename or f"Direct DOCX {docx_id[:8]}"
        base_slug = slugify(title)
        slug = f"direct-tr-{docx_id[:8]}-{base_slug}" if base_slug else f"direct-tr-{docx_id[:8]}"

        image_mapping = _extract_docx_images(doc, slug, storage)
        pages_list = _segment_docx(doc, slug, image_mapping)

        pages_data = []
        for idx, (page_text, page_html) in enumerate(pages_list):
            page_slug = str(idx + 1)
            pages_data.append({
                "page_number": idx + 1,
                "slug": page_slug,
                "text": page_text,
                "html": page_html,
                "blocks": [{
                    "id": f"b{uuid.uuid4().hex[:8]}",
                    "type": "paragraph",
                    "bbox": [0, 0, 0, 0],
                    "content": page_html,
                    "reading_order": 1
                }]
            })

        payload = {
            "docx_id": docx_id,
            "title": title,
            "slug": slug,
            "original_filename": original_filename,
            "creator_id": creator_id,
            "created_at": datetime.utcnow().isoformat(),
            "num_pages": len(pages_list),
            "image_mapping": image_mapping,
            "pages": pages_data,
        }

        key = docx_saved_data_key(docx_id)
        storage.save_json_gz(key, payload)
        LOG.info(f"Successfully saved original DOCX data for '{docx_id}' to storage at '{key}'.")
        return payload
    except Exception as e:
        LOG.error(f"Failed to save original DOCX data to storage for '{docx_id}': {e}", exc_info=True)
        raise


def save_original_docx_data_to_db(doc, docx_id: str, original_filename: str = None, creator_id: int = None):
    """Alias for save_original_docx_data for backward compatibility."""
    return save_original_docx_data(doc, docx_id, original_filename=original_filename, creator_id=creator_id)
