"""Organization-aware access helpers."""

from flask import abort, current_app

import kalanjiyam.database as db


def is_multi_tenant_enabled() -> bool:
    return bool(current_app.config.get("MULTI_TENANT_MODE")) and bool(
        current_app.config.get("ENFORCE_ORG_ACCESS", True)
    )


def user_organization_id(user) -> int | None:
    return getattr(user, "organization_id", None)


def user_can_access_project(user, project: db.Project) -> bool:
    """True if user can access a project under current tenancy rules."""
    if getattr(user, "is_super_admin", False):
        return True
    if getattr(user, "is_admin", False) and not is_multi_tenant_enabled():
        return True

    if not is_multi_tenant_enabled():
        from kalanjiyam import queries as q

        return q.user_can_view_project_legacy(user, project)

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
