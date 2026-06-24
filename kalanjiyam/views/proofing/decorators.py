from collections.abc import Callable
from functools import wraps

from flask import current_app, flash, redirect, url_for
from flask_login import current_user


def p2_required(func: Callable):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        from kalanjiyam.utils.org_access import user_organization_id
        from kalanjiyam import queries as q

        is_open_tenant = False
        if current_user.is_authenticated:
            try:
                open_tenant = q.get_or_create_open_tenant()
                is_open_tenant = (user_organization_id(current_user) == open_tenant.id)
            except Exception:
                pass

        is_p2_or_admin = (
            getattr(current_user, "is_p2", False)
            or getattr(current_user, "is_moderator", False)
            or getattr(current_user, "is_org_admin", False)
            or getattr(current_user, "is_super_admin", False)
        )

        allowed = (current_user.is_authenticated and is_open_tenant) or is_p2_or_admin

        if not allowed:
            flash("Sorry, you aren't authorized to use this feature.")
            return redirect(url_for("proofing.index"))
        return current_app.ensure_sync(func)(*args, **kwargs)

    return decorated_view


def moderator_required(func: Callable):
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if not (current_user.is_moderator or current_user.is_org_admin or current_user.is_super_admin):
            flash("Sorry, you aren't authorized to use this feature.")
            return redirect(url_for("proofing.index"))
        return current_app.ensure_sync(func)(*args, **kwargs)

    return decorated_view
