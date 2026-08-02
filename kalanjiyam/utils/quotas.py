from flask import abort

from kalanjiyam import queries as q
from kalanjiyam import database as db
from kalanjiyam.utils.storage import get_storage, project_prefix


def _org_for_user(user):
    if getattr(user, "organization_id", None) is None:
        return None
    return q.group(user.organization_id)


def _org_for_project(project):
    if not project.groups:
        return None
    return project.groups[0]


def get_user_storage_used_bytes(user) -> int:
    session = q.get_session()
    projects = session.query(db.Project).filter_by(creator_id=user.id).all()
    storage = get_storage()
    used = 0
    for p in projects:
        used += storage.total_size(project_prefix(p.slug))
    return used


def ensure_storage_quota_for_user(user, incoming_bytes: int) -> None:
    if not user or not user.is_authenticated:
        return

    # Check if user is an org user or a registered user
    org = _org_for_user(user)

    # Determine per-user storage limit
    per_user_storage_limit = None
    if org is not None:
        per_user_storage_limit = org.default_user_storage_limit

    # Calculate user's current storage usage
    user_storage_used = get_user_storage_used_bytes(user)

    if per_user_storage_limit is not None:
        if user_storage_used + incoming_bytes > per_user_storage_limit:
            abort(402, description="Your personal storage quota has been exceeded")
    
    # Also enforce overall organization/tenant quota (or fallback)
    if org is not None:
        if org.storage_quota_bytes is not None:
            if org.storage_used_bytes + incoming_bytes > org.storage_quota_bytes:
                abort(402, description="Organization/Tenant storage quota has been exceeded")


def add_storage_usage_for_project(project_slug: str) -> None:
    project = q.project(project_slug)
    if project is None:
        return
    org = _org_for_project(project)
    if org is None:
        return
    storage = get_storage()
    used = 0
    for org_project in org.projects:
        used += storage.total_size(project_prefix(org_project.slug))
    org.storage_used_bytes = used
    session = q.get_session()
    session.add(org)
    session.commit()


def ensure_ocr_quota_for_project(project) -> None:
    # 1. Enforce per-user OCR credit limit if configured on the creator's organization
    session = q.get_session()
    creator = None
    if project.creator_id:
        creator = session.query(db.User).filter_by(id=project.creator_id).first()

    if creator:
        org = _org_for_user(creator)
        per_user_ocr_limit = None
        if org is not None:
            per_user_ocr_limit = org.default_user_ocr_limit

        if per_user_ocr_limit is not None:
            ocr_used = creator.ocr_credits_used or 0
            if ocr_used >= per_user_ocr_limit:
                abort(402, description="Your personal OCR credit limit has been exhausted")

    # 2. Enforce overall organization/tenant OCR credit limit (or fallback)
    org = _org_for_project(project)
    if org is not None and org.ocr_credit_limit is not None:
        if (org.ocr_credits_used or 0) >= org.ocr_credit_limit:
            abort(402, description="Organization/Tenant OCR credits exhausted")


def consume_ocr_credit_for_project(project) -> None:
    org = _org_for_project(project)
    if org is not None:
        org.ocr_credits_used = (org.ocr_credits_used or 0) + 1
    
    session = q.get_session()
    if project.creator_id:
        creator = session.query(db.User).filter_by(id=project.creator_id).first()
        if creator:
            creator.ocr_credits_used = (creator.ocr_credits_used or 0) + 1
            session.add(creator)

    if org is not None:
        session.add(org)
    session.commit()


from werkzeug.exceptions import HTTPException

class PaymentRequired(HTTPException):
    code = 402
    description = "Payment Required"


def ensure_translation_quota_for_project(project) -> None:
    # 1. Enforce per-user Translation credit limit if configured on the creator's organization
    session = q.get_session()
    creator = None
    if project.creator_id:
        creator = session.query(db.User).filter_by(id=project.creator_id).first()

    if creator:
        org = _org_for_user(creator)
        per_user_translation_limit = None
        if org is not None:
            per_user_translation_limit = org.default_user_translation_limit

        if per_user_translation_limit is not None:
            translation_used = creator.translation_credits_used or 0
            if translation_used >= per_user_translation_limit:
                raise PaymentRequired(description="Your personal Translation credit limit has been exhausted")

    # 2. Enforce overall organization/tenant Translation credit limit (or fallback)
    org = _org_for_project(project)
    if org is not None and org.translation_credit_limit is not None:
        if (org.translation_credits_used or 0) >= org.translation_credit_limit:
            raise PaymentRequired(description="Organization/Tenant Translation credits exhausted")


def consume_translation_credit_for_project(project) -> None:
    org = _org_for_project(project)
    if org is not None:
        org.translation_credits_used = (org.translation_credits_used or 0) + 1
    
    session = q.get_session()
    if project.creator_id:
        creator = session.query(db.User).filter_by(id=project.creator_id).first()
        if creator:
            creator.translation_credits_used = (creator.translation_credits_used or 0) + 1
            session.add(creator)

    if org is not None:
        session.add(org)
    session.commit()


def estimate_docx_pages(doc) -> int:
    """Estimate the page count of a python-docx Document object using the page segmentation heuristic."""
    from docx.text.paragraph import Paragraph
    from docx.table import Table
    from kalanjiyam.tasks.projects import _get_paragraph_plain_text
    
    pages = 0
    current_page_text = []
    
    def flush_page():
        nonlocal pages
        pages += 1
        current_page_text.clear()
        
    for child in doc.element.body.iterchildren():
        if child.tag.endswith('sectPr'):
            continue
        if child.tag.endswith('p'):
            p = Paragraph(child, doc)
            p_text = _get_paragraph_plain_text(p)
            current_page_text.append(p_text)
            
            p_xml = child.xml
            is_break = ('w:br' in p_xml and 'w:type="page"' in p_xml) or ('w:lastRenderedPageBreak' in p_xml)
            if is_break or len("".join(current_page_text)) > 1500:
                flush_page()
        elif child.tag.endswith('tbl'):
            table = Table(child, doc)
            table_text = " ".join(
                _get_paragraph_plain_text(pt) 
                for row in table.rows 
                for cell in row.cells 
                for pt in cell.paragraphs
            )
            current_page_text.append(table_text)
            if len("".join(current_page_text)) > 1500:
                flush_page()
                
    if current_page_text or pages == 0:
        flush_page()
        
    return pages


def ensure_translation_quota_for_user(user, required_credits: int = 1) -> None:
    """Enforce translation quota limits for a specific user before performing translation."""
    if not user or not user.is_authenticated:
        return

    session = q.get_session()
    # Reload user inside session to avoid stale state
    user = session.query(db.User).filter_by(id=user.id).first()
    if not user:
        return

    org = _org_for_user(user)

    # 1. Enforce per-user Translation credit limit if configured on the creator's organization
    per_user_translation_limit = None
    if org is not None:
        per_user_translation_limit = org.default_user_translation_limit

    if per_user_translation_limit is not None:
        translation_used = user.translation_credits_used or 0
        if translation_used + required_credits > per_user_translation_limit:
            raise PaymentRequired(
                description=f"Your personal Translation credit limit has been exhausted (requires {required_credits} credits, but you have {per_user_translation_limit - translation_used} left)"
            )

    # 2. Enforce overall organization/tenant Translation credit limit
    if org is not None and org.translation_credit_limit is not None:
        org_used = org.translation_credits_used or 0
        if org_used + required_credits > org.translation_credit_limit:
            raise PaymentRequired(
                description=f"Organization/Tenant Translation credits exhausted (requires {required_credits} credits, but organization has {org.translation_credit_limit - org_used} left)"
            )


def consume_translation_credits_for_user(user, credits: int) -> None:
    """Consume the specified number of translation credits for a user and their organization."""
    if not user or not user.is_authenticated:
        return

    session = q.get_session()
    user = session.query(db.User).filter_by(id=user.id).first()
    if not user:
        return

    org = _org_for_user(user)
    if org is not None:
        org.translation_credits_used = (org.translation_credits_used or 0) + credits
        session.add(org)

    user.translation_credits_used = (user.translation_credits_used or 0) + credits
    session.add(user)
    session.commit()


