import json

from sqlalchemy import update

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
    author_id: int,
    *,
    document: dict | None = None,
    content_format: str = "plain",
) -> int:
    """Add a new revision for a page."""
    session = q.get_session()
    status_ids = {s.name: s.id for s in q.page_statuses()}
    new_version = version + 1
    resolved_content, resolved_document, resolved_format = _resolve_content_and_document(
        content, document, content_format
    )
    result = session.execute(
        update(db.Page)
        .where((db.Page.id == page.id) & (db.Page.version == version))
        .values(version=new_version, status_id=status_ids[status])
    )
    session.commit()

    num_rows_changed = result.rowcount
    if num_rows_changed == 0:
        raise EditError(f"Edit conflict {page.slug}, {version}")

    assert num_rows_changed == 1

    revision_ = db.Revision(
        project_id=page.project_id,
        page_id=page.id,
        summary=summary,
        content=resolved_content,
        author_id=author_id,
        status_id=status_ids[status],
        document=resolved_document,
        content_format=resolved_format,
    )
    session.add(revision_)
    session.commit()
    return new_version


def parse_document_field(raw: str | None) -> dict | None:
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None
