"""Utility module to handle saving original DOCX data to the database during direct translation when enabled by configuration."""

import logging
import uuid
from slugify import slugify
from kalanjiyam import database as db
from kalanjiyam import queries as q

LOG = logging.getLogger(__name__)


def save_original_docx_data_to_db(doc, docx_id: str, original_filename: str = None, creator_id: int = None):
    """Save the original DOCX document structure and text content to the database.

    Creates a Project, Pages, and initial Revision records for the DOCX document.
    """
    try:
        from kalanjiyam.utils.storage import get_storage
        from kalanjiyam.tasks.projects import _extract_docx_images, _segment_docx, _add_project_to_database

        session = q.get_session()
        storage = get_storage()

        title = original_filename or f"Direct DOCX {docx_id[:8]}"
        base_slug = slugify(title)
        slug = f"direct-tr-{docx_id[:8]}-{base_slug}" if base_slug else f"direct-tr-{docx_id[:8]}"

        existing = session.query(db.Project).filter_by(slug=slug).first()
        if existing:
            LOG.info(f"Project with slug '{slug}' already exists in DB.")
            return existing

        image_mapping = _extract_docx_images(doc, slug, storage)
        pages_list = _segment_docx(doc, slug, image_mapping)
        num_pages = len(pages_list)

        from flask import current_app
        require_org = True
        try:
            require_org = bool(current_app.config.get("DEFAULT_PROJECT_REQUIRES_ORG", True))
        except Exception:
            pass

        _add_project_to_database(
            display_title=title,
            slug=slug,
            num_pages=num_pages,
            creator_id=creator_id,
            require_org=require_org,
        )

        db_project = session.query(db.Project).filter_by(slug=slug).one()
        unreviewed = session.query(db.PageStatus).filter_by(name="reviewed-0").first()
        status_id = unreviewed.id if unreviewed else 1

        for idx, (page_text, page_html) in enumerate(pages_list):
            page_slug = str(idx + 1)
            db_page = session.query(db.Page).filter_by(project_id=db_project.id, slug=page_slug).first()
            if not db_page:
                db_page = db.Page(project_id=db_project.id, slug=page_slug)
                session.add(db_page)
                session.flush()

            # Original version track
            pv_orig = db.PageVersion(page_id=db_page.id, version_key="original")
            session.add(pv_orig)
            session.flush()

            doc_dict = {
                "content_format": "html",
                "blocks": [{
                    "id": f"b{uuid.uuid4().hex[:8]}",
                    "type": "paragraph",
                    "bbox": [0, 0, 0, 0],
                    "content": page_html,
                    "reading_order": 1
                }]
            }

            rev_orig = db.Revision(
                project_id=db_project.id,
                page_id=db_page.id,
                page_version_id=pv_orig.id,
                status_id=status_id,
                content=page_text,
                content_format="html",
                document=doc_dict
            )
            session.add(rev_orig)

        session.commit()
        LOG.info(f"Successfully saved original DOCX data for '{docx_id}' to DB under project slug '{slug}'.")
        return db_project
    except Exception as e:
        LOG.error(f"Failed to save original DOCX data to DB for '{docx_id}': {e}", exc_info=True)
        session.rollback()
        raise
