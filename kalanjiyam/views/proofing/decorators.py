from collections.abc import Callable
from functools import wraps

from flask import current_app, flash, redirect, url_for
from flask_babel import lazy_gettext as _l
from flask_login import current_user


def p2_required(func: Callable):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        from kalanjiyam.utils.org_access import user_organization_id
        from kalanjiyam import queries as q
        from flask import request

        # 1. Allow P2 and admins
        is_p2_or_admin = (
            getattr(current_user, "is_p1", False)
            or getattr(current_user, "is_p2", False)
            or getattr(current_user, "is_moderator", False)
            or getattr(current_user, "is_org_admin", False)
            or getattr(current_user, "is_super_admin", False)
        )
        if is_p2_or_admin:
            return current_app.ensure_sync(func)(*args, **kwargs)

        # 2. Allow open-tenant registered users
        is_open_tenant = False
        if current_user.is_authenticated:
            try:
                open_tenant = q.get_or_create_open_tenant()
                is_open_tenant = (user_organization_id(current_user) == open_tenant.id)
            except Exception:
                pass
        if current_user.is_authenticated and is_open_tenant:
            return current_app.ensure_sync(func)(*args, **kwargs)

        # 3. Allow guest user if they own this project
        slug = request.view_args.get("slug") or request.view_args.get("project_slug") if request.view_args else None
        if slug:
            project = q.project(slug)
            if project and project.fingerprint_id:
                device_fp = request.cookies.get("device_fingerprint")
                if device_fp and project.fingerprint_id == device_fp:
                    return current_app.ensure_sync(func)(*args, **kwargs)

        flash(_l("Sorry, you aren't authorized to use this feature."), "error")
        return redirect(url_for("proofing.index"))

    return decorated_view


def moderator_required(func: Callable):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if not (current_user.is_moderator or current_user.is_org_admin or current_user.is_super_admin):
            flash(_l("Sorry, you aren't authorized to use this feature."), "error")
            return redirect(url_for("proofing.index"))
        return current_app.ensure_sync(func)(*args, **kwargs)

    return decorated_view
