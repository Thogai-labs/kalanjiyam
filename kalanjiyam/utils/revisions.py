import json
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.utils.page_document import PageDocument


class EditError(Exception):
    """Raised if a user's attempt to edit a page fails."""

    pass


def _resolve_content_and_document(
    content: str | None,
    document: dict | None,
    content_format: str,
) -> tuple[str, dict | None, str]:
    if document:
        doc = PageDocument.from_dict(document)
        plain = doc.to_plain_text()
        return plain or (content or ""), doc.to_dict(), doc.content_format
    if content:
        doc = PageDocument.from_legacy_content(content, content_format=content_format)
        return content, doc.to_dict() if doc.blocks else None, doc.content_format
    return "", None, content_format


def add_revision(
    page: db.Page,
    summary: str,
    content: str,
    status: str,
    version: int,
    author_id: int | None,
    *,
    document: dict | None = None,
    content_format: str = "plain",
    version_key: str = "role:p1",
) -> int:
    """Add a new revision for a page."""
    session = q.get_session()
    status_ids = {s.name: s.id for s in q.page_statuses()}
    new_version = version + 1
    resolved_content, resolved_document, resolved_format = _resolve_content_and_document(
        content, document, content_format
    )

    # 1. Fetch or create the PageVersion record for version_key
    page_version = session.query(db.PageVersion).filter_by(
        page_id=page.id,
        version_key=version_key
    ).first()

    if not page_version:
        if version != 0:
            raise EditError(
                f"Edit conflict: track {version_key} does not exist, but expected version {version}"
            )
        
        # Create a new version record
        page_version = db.PageVersion(
            page_id=page.id,
            version_key=version_key,
            version=new_version,
            updated_at=datetime.utcnow()
        )
        session.add(page_version)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise EditError(f"Edit conflict: track {version_key} concurrently created")
    else:
        # Perform optimistic locking on PageVersion.version
        result = session.execute(
            update(db.PageVersion)
            .where((db.PageVersion.id == page_version.id) & (db.PageVersion.version == version))
            .values(version=new_version, updated_at=datetime.utcnow())
        )
        if result.rowcount == 0:
            raise EditError(
                f"Edit conflict: track {version_key} version mismatch (expected {version})"
            )

    # 2. Update the page's overall status
    session.execute(
        update(db.Page)
        .where(db.Page.id == page.id)
        .values(status_id=status_ids[status])
    )
    session.commit()

    # 3. Create the Revision linked to the PageVersion
    revision_ = db.Revision(
        project_id=page.project_id,
        page_id=page.id,
        page_version_id=page_version.id,
        summary=summary,
        content=resolved_content,
        author_id=author_id,
        status_id=status_ids[status],
        document=resolved_document,
        content_format=resolved_format,
    )
    session.add(revision_)
    session.commit()

    # Persist document snapshot to S3/VersityGW (dual-write during migration).
    if resolved_document:
        from kalanjiyam.utils.document_storage import save_revision_document

        try:
            save_revision_document(revision_, resolved_document)
        except Exception:
            # S3/VersityGW write failure must not break the page-save flow;
            # the data is safely in the DB column as a fallback.
            import logging

            logging.getLogger(__name__).warning(
                "Failed to persist revision %s document to object storage",
                revision_.id,
                exc_info=True,
            )

    return new_version


def parse_document_field(raw: str | None) -> dict | None:
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None

