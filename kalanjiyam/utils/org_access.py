"""Organization-aware access helpers."""

from flask import abort, current_app, request

import kalanjiyam.database as db


def is_multi_tenant_enabled() -> bool:
    return bool(current_app.config.get("MULTI_TENANT_MODE")) and bool(
        current_app.config.get("ENFORCE_ORG_ACCESS", True)
    )


def user_organization_id(user) -> int | None:
    org_id = getattr(user, "organization_id", None)
    if org_id is None and getattr(user, "is_authenticated", False):
        from kalanjiyam import queries as q
        try:
            open_tenant = q.get_or_create_open_tenant()
            org_id = open_tenant.id
        except Exception:
            pass
    return org_id


def is_project_publicly_viewable(project: db.Project) -> bool:
    return bool(getattr(project, "is_publicly_viewable", False))


def user_can_access_project(user, project: db.Project) -> bool:
    """True if user can access a project under current tenancy rules."""
    if getattr(user, "is_super_admin", False):
        return True
    if getattr(user, "is_admin", False) and not is_multi_tenant_enabled():
        return True

    # 1. Device fingerprint check for guest-owned projects
    device_fp = request.cookies.get("device_fingerprint") if request else None
    if getattr(project, "fingerprint_id", None) and device_fp:
        if project.fingerprint_id == device_fp:
            return True

    if not is_multi_tenant_enabled():
        from kalanjiyam import queries as q
        return q.user_can_view_project_legacy(user, project)

    # 2. Check open-tenant isolation
    is_open_tenant = any(g.slug == "open-tenant" for g in project.groups)
    if is_open_tenant:
        # If it is a guest-created project (has fingerprint_id), we ONLY allow if fingerprint matches.
        if getattr(project, "fingerprint_id", None):
            return False
            
        # Otherwise, it's a registered user project. Guests are not allowed.
        if not getattr(user, "is_authenticated", False):
            return False
        return project.creator_id == getattr(user, "id", None)

    # 3. Multi-tenant mode: org membership is always required for enterprise projects.
    if not getattr(user, "is_authenticated", False):
        return False

    org_id = user_organization_id(user)
    if org_id is None:
        return False

    return any(g.id == org_id for g in project.groups)


def user_can_access_text(user, text: db.Text) -> bool:
    """True if user can access a text under current tenancy rules."""
    if getattr(user, "is_super_admin", False):
        return True
    if getattr(user, "is_admin", False) and not is_multi_tenant_enabled():
        return True

    if not is_multi_tenant_enabled():
        from kalanjiyam import queries as q

        return q.user_can_view_text_legacy(user, text)

    if not getattr(user, "is_authenticated", False):
        return False

    org_id = user_organization_id(user)
    if org_id is None:
        return False
    return any(g.id == org_id for g in text.groups)


def require_project_access(user, project: db.Project) -> None:
    if not user_can_access_project(user, project):
        abort(403)


def require_text_access(user, text: db.Text) -> None:
    if not user_can_access_text(user, text):
        abort(403)


def filter_projects_for_user(user, projects: list[db.Project]) -> list[db.Project]:
    return [p for p in projects if user_can_access_project(user, p)]


def filter_texts_for_user(user, texts: list[db.Text]) -> list[db.Text]:
    return [t for t in texts if user_can_access_text(user, t)]
