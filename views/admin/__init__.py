"""Admin views for Kalanjiyam."""

from flask import Blueprint, render_template, redirect, url_for
from flask_login import current_user, login_required

import kalanjiyam.queries as q

bp = Blueprint("admin", __name__)


def admin_required(func):
    """Decorator to require admin access."""
    from functools import wraps
    
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if not current_user.is_admin:
            return redirect(url_for("proofing.index"))
        return func(*args, **kwargs)
    
    return decorated_view


@bp.route("/")
@login_required
@admin_required
def index():
    """Admin dashboard."""
    projects = q.projects()
    return render_template("admin/export_import.html", projects=projects)
