"""Access control for platform vs organization admin views."""

from flask import abort, redirect, url_for
from flask_login import current_user

from kalanjiyam.enums import SiteRole


def is_platform_super_admin(user=None) -> bool:
    """True only for the platform ``super_admin`` role (not legacy ``admin``)."""
    user = user or current_user
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_role(SiteRole.SUPER_ADMIN)


def is_org_scoped_admin(user=None) -> bool:
    """Organization admin tied to a single org (not platform-wide)."""
    user = user or current_user
    if not getattr(user, "is_authenticated", False):
        return False
    return user.has_role(SiteRole.ORG_ADMIN) and bool(
        getattr(user, "organization_id", None)
    )


def platform_admin_inaccessible():
    """Redirect org admins to their dashboard; hide platform routes from everyone else."""
    if getattr(current_user, "is_authenticated", False) and is_org_scoped_admin():
        return redirect(url_for("org_admin_view.index"))
    abort(404)


def require_platform_super_admin() -> None:
    if not is_platform_super_admin():
        platform_admin_inaccessible()


def require_org_admin() -> int:
    """Return the caller's organization id or abort."""
    if not is_org_scoped_admin():
        abort(404)
    org_id = getattr(current_user, "organization_id", None)
    if org_id is None:
        abort(403)
    return org_id


def require_org_group_access(group_id: int) -> None:
    """Org admins may only access their own organization."""
    if is_platform_super_admin():
        return
    org_id = require_org_admin()
    if org_id != group_id:
        abort(403)
