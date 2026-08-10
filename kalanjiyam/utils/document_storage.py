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


def _derive_bounding_boxes_from_document(doc_dict: dict | None) -> str | None:
    """Dynamically derive bounding box JSON payload from a PageDocument dict's blocks."""
    if not doc_dict or not isinstance(doc_dict, dict):
        return None
    blocks = doc_dict.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return None
    boxes: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        words = b.get("words")
        if isinstance(words, list) and words:
            for w in words:
                if isinstance(w, dict):
                    wbox = w.get("bbox")
                    wtext = w.get("text") if w.get("text") is not None else (w.get("content") or "")
                    if isinstance(wbox, (list, tuple)) and len(wbox) >= 4:
                        boxes.append({
                            "x1": float(wbox[0]),
                            "y1": float(wbox[1]),
                            "x2": float(wbox[2]),
                            "y2": float(wbox[3]),
                            "text": str(wtext),
                        })
        else:
            bbox = b.get("bbox")
            content = b.get("content") or ""
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
                if x2 > x1 or y2 > y1 or (x1 != 0 or y1 != 0 or x2 != 0 or y2 != 0):
                    boxes.append({
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "text": str(content),
                    })
    if not boxes:
        return None
    import json

    return json.dumps(boxes, ensure_ascii=False)


def save_page_ocr(page: Any, ocr_text: str) -> bool:
    """[DEPRECATED] Persist OCR bounding-box text to object storage.

    If object storage write fails (e.g., S3/VersityGW down), falls back
    to saving in DB column `ocr_bounding_boxes` so data is never lost.
    Returns True if successfully written to S3, False if fell back to DB.
    """
    from kalanjiyam.utils.storage import get_storage, page_ocr_key

    try:
        project_slug = page.project.slug
        key = page_ocr_key(project_slug, page.slug)
        get_storage().save_json_gz(key, ocr_text)
        return True
    except Exception as err:
        LOG.warning(
            "S3/VersityGW write failed for page %s OCR data. Falling back to DB column: %s",
            getattr(page, "slug", page),
            err,
        )
        page.ocr_bounding_boxes = ocr_text
        return False


def load_page_ocr(page: Any) -> str | None:
    """Load OCR bounding-box text for a page.

    Under Strategy B (Unified PageDocument Model), this first attempts to load
    the page's latest revision document (`load_revision_document(latest_rev)`)
    and dynamically derive bounding box JSON from `PageDocument.blocks`.

    If missing or no revision exists, it falls back to reading the legacy S3/VersityGW
    `/ocr/{page_slug}.json.gz` key or legacy PostgreSQL column.

    Returns ``None`` when no OCR data exists in any location.
    """
    revisions = getattr(page, "revisions", None)
    if revisions:
        try:
            latest_rev = revisions[-1] if isinstance(revisions, (list, tuple)) else list(revisions)[-1]
            doc_dict = load_revision_document(latest_rev)
            derived = _derive_bounding_boxes_from_document(doc_dict)
            if derived is not None:
                return derived
        except Exception as err:
            LOG.warning(
                "Failed to derive bounding boxes from page %s revision: %s",
                getattr(page, "slug", page),
                err,
            )

    # Legacy dual-read fallback: S3 / VersityGW payload
    project = getattr(page, "project", None)
    if project is not None:
        try:
            from kalanjiyam.utils.storage import get_storage, page_ocr_key

            key = page_ocr_key(project.slug, page.slug)
            data = get_storage().load_json_gz(key)
            if data is not None:
                return data
        except Exception as err:
            LOG.warning("Failed to fetch page %s OCR from S3: %s", getattr(page, "slug", page), err)

    # Fallback: legacy PostgreSQL column
    return getattr(page, "ocr_bounding_boxes", None)


# ---------------------------------------------------------------------------
# Revision document snapshots (Revision.document)
# ---------------------------------------------------------------------------


def get_page_revision_index(revision: Any) -> int:
    """Return 1-based page-local revision index (1, 2, 3...) for S3 key naming."""
    if revision is None:
        return 1

    page = getattr(revision, "page", None)
    if page and getattr(page, "revisions", None):
        for idx, rev in enumerate(page.revisions, start=1):
            if rev.id == getattr(revision, "id", None):
                return idx

    pv = getattr(revision, "page_version", None)
    if pv and getattr(pv, "version", None):
        return pv.version

    return 1


def derive_revision_tag(revision: Any) -> str:
    """Derive a human-readable semantic tag for S3 key naming (e.g. ocr, trans, user-john)."""
    if revision is None:
        return "rev"

    summary = (getattr(revision, "summary", "") or "").lower()

    # 1. OCR engine run
    if "ocr" in summary:
        return "ocr"

    # 2. Translation run
    if "translation" in summary or "translated" in summary:
        return "trans"

    # 3. Registered User Edit
    author = getattr(revision, "author", None)
    if author and getattr(author, "username", None):
        from slugify import slugify

        return f"user-{slugify(author.username)}"

    # 4. Guest / Anonymous edit
    return "user-guest"


def save_revision_document(revision: Any, document: dict) -> bool:
    """Persist a revision's structured block document to object storage.

    If S3/VersityGW write fails, falls back to saving in DB column `document`.
    Returns True if written to S3, False if fell back to DB.
    """
    from kalanjiyam.utils.storage import get_storage, revision_document_key

    try:
        page = revision.page
        project = revision.project
        v_num = get_page_revision_index(revision)
        tag = derive_revision_tag(revision)
        key = revision_document_key(project.slug, page.slug, v_num, tag=tag)
        get_storage().save_json_gz(key, document)
        return True
    except Exception as err:
        LOG.warning(
            "S3/VersityGW write failed for revision %s document. Falling back to DB column: %s",
            getattr(revision, "id", revision),
            err,
        )
        revision.document = document
        return False


def load_revision_document(revision: Any) -> dict | None:
    """Load revision document, trying S3/VersityGW first, then DB.

    Returns ``None`` when no document exists in either location.
    """
    from kalanjiyam.utils.storage import get_storage, revision_document_key

    page = getattr(revision, "page", None)
    project = getattr(revision, "project", None)
    if page is not None and project is not None:
        storage = get_storage()
        v_num = get_page_revision_index(revision)
        tag = derive_revision_tag(revision)

        # 1. Try page-local tagged key first (e.g. user-john_v1.json.gz)
        try:
            key = revision_document_key(project.slug, page.slug, v_num, tag=tag)
            data = storage.load_json_gz(key)
            if data is not None:
                return data
        except Exception as err:
            LOG.warning("Failed to fetch revision %s document from S3: %s", revision.id, err)

        # 2. Try untagged version key & DB ID fallback keys
        fallback_keys = [
            revision_document_key(project.slug, page.slug, v_num, tag=""),
            f"projects/{project.slug}/revisions/{page.slug}/{tag}_rev{revision.id}.json.gz",
            f"projects/{project.slug}/revisions/{page.slug}/rev{revision.id}.json.gz",
            f"projects/{project.slug}/revisions/{page.slug}/{revision.id}.json.gz",
        ]
        for fkey in fallback_keys:
            try:
                data = storage.load_json_gz(fkey)
                if data is not None:
                    return data
            except Exception:
                pass

    # Fallback: legacy PostgreSQL column
    return getattr(revision, "document", None)


# ---------------------------------------------------------------------------
# Storage Health Check & Auto-Reconciliation
# ---------------------------------------------------------------------------


def is_storage_healthy() -> bool:
    """Check if S3 / VersityGW storage is online and reachable."""
    from kalanjiyam.utils.storage import get_storage

    try:
        storage = get_storage()
        # Test write & delete ping key or list check
        ping_key = "_health_ping.json.gz"
        storage.save_json_gz(ping_key, {"ping": True})
        storage.delete(ping_key)
        return True
    except Exception as err:
        LOG.debug("Storage health check failed: %s", err)
        return False


def reconcile_db_to_storage(session=None, limit: int = 100) -> dict[str, int]:
    """Scan DB for un-migrated OCR/document data and push to S3 if storage is online.

    When S3/VersityGW comes back online after downtime, this function moves
    any DB fallback data into S3/VersityGW and nullifies the DB columns.

    Returns dict with stats: {'reconciled_ocr': X, 'reconciled_revisions': Y}.
    """
    stats = {"reconciled_ocr": 0, "reconciled_revisions": 0}

    if not is_storage_healthy():
        LOG.warning("Storage health check failed; skipping S3 reconciliation.")
        return stats

    from kalanjiyam import database as db
    from kalanjiyam import queries as q

    if session is None:
        session = q.get_session()

    # 1. Reconcile Page OCR Bounding Boxes stored in DB
    unmigrated_pages = (
        session.query(db.Page)
        .filter(db.Page.ocr_bounding_boxes.isnot(None))
        .limit(limit)
        .all()
    )

    for page in unmigrated_pages:
        if page.project and page.ocr_bounding_boxes:
            if save_page_ocr(page, page.ocr_bounding_boxes):
                page.ocr_bounding_boxes = None
                stats["reconciled_ocr"] += 1

    # 2. Reconcile Revision Document JSON stored in DB
    unmigrated_revisions = (
        session.query(db.Revision)
        .filter(db.Revision.document.isnot(None))
        .limit(limit)
        .all()
    )

    for rev in unmigrated_revisions:
        if rev.page and rev.project and rev.document:
            if save_revision_document(rev, rev.document):
                rev.document = None
                stats["reconciled_revisions"] += 1

    if stats["reconciled_ocr"] > 0 or stats["reconciled_revisions"] > 0:
        session.commit()
        LOG.info(
            "Reconciled un-migrated DB data to S3: %d page OCR records, %d revision documents.",
            stats["reconciled_ocr"],
            stats["reconciled_revisions"],
        )

    return stats

