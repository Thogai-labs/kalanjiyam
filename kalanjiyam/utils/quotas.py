"""Organization quota helpers."""

from pathlib import Path

from flask import abort, current_app

from kalanjiyam import queries as q


def _org_for_user(user):
    if getattr(user, "organization_id", None) is None:
        return None
    return q.group(user.organization_id)


def _org_for_project(project):
    if not project.groups:
        return None
    return project.groups[0]


def ensure_storage_quota_for_user(user, incoming_bytes: int) -> None:
    org = _org_for_user(user)
    if org is None or org.storage_quota_bytes is None:
        return
    if org.storage_used_bytes + incoming_bytes > org.storage_quota_bytes:
        abort(402, description="Organization storage quota exceeded")


def add_storage_usage_for_project(project_slug: str) -> None:
    project = q.project(project_slug)
    if project is None:
        return
    org = _org_for_project(project)
    if org is None:
        return
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    used = 0
    for org_project in org.projects:
        project_dir = upload_folder / "projects" / org_project.slug
        if project_dir.exists():
            used += sum(p.stat().st_size for p in project_dir.rglob("*") if p.is_file())
    org.storage_used_bytes = used
    session = q.get_session()
    session.add(org)
    session.commit()


def ensure_ocr_quota_for_project(project) -> None:
    org = _org_for_project(project)
    if org is None or org.ocr_credit_limit is None:
        return
    if org.ocr_credits_used >= org.ocr_credit_limit:
        abort(402, description="Organization OCR credits exhausted")


def consume_ocr_credit_for_project(project) -> None:
    org = _org_for_project(project)
    if org is None:
        return
    org.ocr_credits_used = (org.ocr_credits_used or 0) + 1
    session = q.get_session()
    session.add(org)
    session.commit()
