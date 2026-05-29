"""Platform super-admin user management helpers."""

from __future__ import annotations

import time

from flask_login import current_user

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam import consts
from kalanjiyam.enums import SiteRole


# Roles assignable via the web UI (never super_admin).
WEB_ASSIGNABLE_ROLES = (
    SiteRole.P1.value,
    SiteRole.P2.value,
    SiteRole.MODERATOR.value,
    SiteRole.ORG_ADMIN.value,
)


def count_super_admins(session) -> int:
    return (
        session.query(db.User)
        .join(db.UserRoles, db.UserRoles.user_id == db.User.id)
        .join(db.Role, db.Role.id == db.UserRoles.role_id)
        .filter(
            db.Role.name == SiteRole.SUPER_ADMIN.value,
            db.User.is_deleted.is_(False),
        )
        .count()
    )


def organization_choices() -> list[tuple[int, str]]:
    choices = [(0, "(none)")]
    for org in q.groups():
        choices.append((org.id, f"{org.name} ({org.slug})"))
    return choices


def assignable_role_choices(session) -> list[tuple[int, str]]:
    roles = (
        session.query(db.Role)
        .filter(db.Role.name.in_(WEB_ASSIGNABLE_ROLES))
        .order_by(db.Role.name)
        .all()
    )
    return [(r.id, r.name) for r in roles]


def validate_user_deletable(model: db.User) -> None:
    if model.username == consts.BOT_USERNAME:
        raise ValueError("The system bot account cannot be deleted.")
    actor_id = getattr(current_user, "id", None)
    if actor_id is not None and model.id == actor_id:
        raise ValueError("You cannot delete your own account.")
    if any(r.name == SiteRole.SUPER_ADMIN.value for r in model.roles):
        if count_super_admins(q.get_session()) <= 1:
            raise ValueError(
                "Cannot delete the only super admin. Use ./cli.py change-password instead."
            )


def soft_delete_user(model: db.User, session) -> None:
    """Mark user deleted and free username/email for reuse."""
    ts = int(time.time())
    session.query(db.Group).filter_by(admin_user_id=model.id).update(
        {db.Group.admin_user_id: None},
        synchronize_session=False,
    )
    model.username = f"{model.username[:48]}-del-{ts}"
    model.email = f"deleted-{model.id}-{ts}@deleted.invalid"
    model.set_is_deleted(True)
    model.organization_id = None
    session.query(db.UserGroups).filter_by(user_id=model.id).delete()
    for role in list(model.roles):
        if role.name != SiteRole.SUPER_ADMIN.value:
            model.roles.remove(role)
    session.add(model)


def sync_user_org_and_roles(form, model: db.User, session, *, is_created: bool) -> None:
    if is_created or (getattr(form, "password", None) and form.password.data):
        if is_created and not form.password.data:
            raise ValueError("Password is required when creating a user.")
        if form.password.data:
            model.set_password(form.password.data)

    assignable = {
        r.id: r
        for r in session.query(db.Role)
        .filter(db.Role.name.in_(WEB_ASSIGNABLE_ROLES))
        .all()
    }
    selected_ids = [rid for rid in (form.role_ids.data or []) if rid in assignable]
    new_roles = [assignable[rid] for rid in selected_ids]

    if not is_created:
        for role in model.roles:
            if role.name == SiteRole.SUPER_ADMIN.value:
                new_roles.append(role)

    model.roles = new_roles

    org_id = getattr(form, "organization_pick", None)
    org_id = org_id.data if org_id is not None else form.organization_id.data
    org_id = org_id or None
    if org_id == 0:
        org_id = None
    model.organization_id = org_id

    session.flush()
    session.query(db.UserGroups).filter_by(user_id=model.id).delete()
    if org_id:
        session.add(db.UserGroups(user_id=model.id, group_id=org_id))
        if any(r.name == SiteRole.ORG_ADMIN.value for r in model.roles):
            org = session.query(db.Group).filter_by(id=org_id).first()
            if org is not None:
                org.admin_user_id = model.id
                session.add(org)
