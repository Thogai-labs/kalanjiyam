import math
from datetime import datetime
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import lazy_gettext as _l
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from sqlalchemy import orm
from wtforms import BooleanField, StringField
from wtforms.widgets import TextArea

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils import heatmap
from kalanjiyam.views.proofing.decorators import moderator_required

bp = Blueprint("user", __name__)


class RolesForm(FlaskForm):
    pass


class EditProfileForm(FlaskForm):
    description = StringField(_l("Profile description"), widget=TextArea())


@bp.route("/<username>/")
def summary(username):
    user_ = q.user(username)
    if not user_:
        abort(404)

    return render_template(
        "proofing/user/summary.html",
        user=user_,
    )


@bp.route("/<username>/activity")
def activity(username):
    """Summarize the user's public activity on Kalanjiyam with pagination and date filter."""
    user_ = q.user(username)
    if not user_:
        abort(404)

    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = request.args.get("per_page", 20, type=int)
    if per_page not in [10, 20, 50]:
        per_page = 20

    date_str = request.args.get("date", "").strip()
    filter_date = None
    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            filter_date = None
            date_str = ""

    session = q.get_session()
    recent_revisions = (
        session.query(db.Revision)
        .options(
            orm.defer(db.Revision.content),
            orm.joinedload(db.Revision.page).load_only(db.Page.id, db.Page.slug),
        )
        .filter_by(author_id=user_.id)
        .order_by(db.Revision.created.desc())
        .all()
    )
    recent_projects = (
        session.query(db.Project)
        .filter_by(creator_id=user_.id)
        .order_by(db.Project.created_at.desc())
        .all()
    )

    all_activity = [("revision", r.created, r) for r in recent_revisions]
    all_activity += [("project", p.created_at, p) for p in recent_projects]
    all_activity.sort(key=lambda x: x[1], reverse=True)

    hm = heatmap.create(x[1].date() for x in all_activity)

    if filter_date:
        filtered_activity = [x for x in all_activity if x[1].date() == filter_date]
    else:
        filtered_activity = all_activity

    total_items = len(filtered_activity)
    total_pages = max(1, math.ceil(total_items / per_page)) if total_items > 0 else 1

    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_activity = filtered_activity[start_idx:end_idx]

    return render_template(
        "proofing/user/activity.html",
        user=user_,
        recent_activity=page_activity,
        heatmap=hm,
        page=page,
        per_page=per_page,
        total_items=total_items,
        total_pages=total_pages,
        selected_date=date_str,
    )


@bp.route("/<username>/edit", methods=["GET", "POST"])
@login_required
def edit(username):
    """Allow a user to edit their own information."""
    user_ = q.user(username)
    if not user_:
        abort(404)

    # Only this user can edit their bio.
    if username != current_user.username:
        abort(403)

    form = EditProfileForm(obj=user_)
    if form.validate_on_submit():
        session = q.get_session()
        form.populate_obj(user_)
        session.commit()
        flash(_l("Saved changes."), "success")
        return redirect(url_for("proofing.user.summary", username=username))

    return render_template("proofing/user/edit.html", user=user_, form=form)


def _make_role_form(roles, user_):
    descriptions = {
        SiteRole.P1: _l("Proofreading 1 (can make pages yellow)"),
        SiteRole.P2: _l("Proofreading 2 (can make pages green)"),
        SiteRole.MODERATOR: _l("Moderator"),
        SiteRole.MASTER_USER: _l("Master User (multi-organization access)"),
    }
    # We're mutating a global object, but this is safe because we're doing so
    # in an idempotent way.
    for r in roles:
        attr_name = f"id_{r.id}"
        user_has_role = r in user_.roles
        setattr(
            RolesForm,
            attr_name,
            BooleanField(descriptions.get(r.name, r.name), default=user_has_role),
        )
    return RolesForm()


@bp.route("/<username>/admin", methods=["GET", "POST"])
@moderator_required
def admin(username):
    """Adjust a user's roles."""
    user_ = q.user(username)
    if not user_:
        abort(404)

    session = q.get_session()
    # Exclude admin.
    # (Admin roles should be added manually by the server administrator.)
    all_roles = [
        r
        for r in session.query(db.Role).all()
        if r.name not in {"admin", "super_admin"}
    ]
    all_roles = sorted(all_roles, key=lambda x: x.name)

    form = _make_role_form(all_roles, user_)

    if form.validate_on_submit():
        id_to_role = {r.id: r for r in all_roles}
        user_role_ids = {r.id for r in user_.roles}
        for key, should_have_role in form.data.items():
            if not key.startswith("id_"):
                continue

            _, _, id = key.partition("_")
            id = int(id)
            role_ = id_to_role[id]
            has_role = role_.id in user_role_ids
            if has_role and not should_have_role:
                user_.roles.remove(role_)
            if not has_role and should_have_role:
                user_.roles.append(role_)

        session.add(user_)
        session.commit()

        flash(_l("Saved changes."), "success")

    return render_template(
        "proofing/user/admin.html",
        user=user_,
        form=form,
    )
