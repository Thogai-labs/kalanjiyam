"""Accessors for OCR and revision document payloads.

This module provides the *single* read/write interface for heavy payload
data that is migrating from PostgreSQL columns to S3 / VersityGW object
storage.  Every consumer in the codebase should call these functions
rather than touching the DB columns directly.

Read strategy (dual-read fallback for zero-downtime migration):

    1. Try loading from S3 / VersityGW first.
    2. If the object is missing, fall back to the legacy PostgreSQL column.

Write strategy:

    - Always write to S3 / VersityGW.
    - Do **not** write to the DB column (new data goes to S3 only).
"""

from __future__ import annotations

import logging
from typing import Any

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OCR bounding-box payloads (Page.ocr_bounding_boxes)
# ---------------------------------------------------------------------------


def save_page_ocr(page: Any, ocr_text: str) -> None:
    """Persist OCR bounding-box text to object storage.

    *ocr_text* is the serialised ``x1 y1 x2 y2 text`` string produced by
    :func:`kalanjiyam.utils.ocr_types.serialize_bounding_boxes`.

    The data is stored gzip-compressed under the key returned by
    :func:`~kalanjiyam.utils.storage.page_ocr_key`.
    """
    from kalanjiyam.utils.storage import get_storage, page_ocr_key

    project_slug = page.project.slug
    key = page_ocr_key(project_slug, page.slug)
    get_storage().save_json_gz(key, ocr_text)


def load_page_ocr(page: Any) -> str | None:
    """Load OCR bounding-box text, trying S3/VersityGW first, then DB.

    Returns ``None`` when no OCR data exists in either location.
    """
    from kalanjiyam.utils.storage import get_storage, page_ocr_key

    project = getattr(page, "project", None)
    if project is not None:
        key = page_ocr_key(project.slug, page.slug)
        data = get_storage().load_json_gz(key)
        if data is not None:
            return data

    # Fallback: legacy PostgreSQL column
    return getattr(page, "ocr_bounding_boxes", None)


# ---------------------------------------------------------------------------
# Revision document snapshots (Revision.document)
# ---------------------------------------------------------------------------


def save_revision_document(revision: Any, document: dict) -> None:
    """Persist a revision's structured block document to object storage.

    *document* is the dict produced by ``PageDocument.to_dict()``.
    """
    from kalanjiyam.utils.storage import get_storage, revision_document_key

    page = revision.page
    project = revision.project
    key = revision_document_key(project.slug, page.slug, revision.id)
    get_storage().save_json_gz(key, document)


def load_revision_document(revision: Any) -> dict | None:
    """Load revision document, trying S3/VersityGW first, then DB.

    Returns ``None`` when no document exists in either location.
    """
    from kalanjiyam.utils.storage import get_storage, revision_document_key

    page = getattr(revision, "page", None)
    project = getattr(revision, "project", None)
    if page is not None and project is not None:
        key = revision_document_key(project.slug, page.slug, revision.id)
        data = get_storage().load_json_gz(key)
        if data is not None:
            return data

    # Fallback: legacy PostgreSQL column
    return getattr(revision, "document", None)
