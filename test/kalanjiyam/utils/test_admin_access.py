"""Tests for platform vs org admin access."""

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils.admin_access import is_platform_super_admin


def _make_user(session, username: str, roles: list[str]) -> db.User:
    user = db.User(username=username, email=f"{username}@test.local")
    user.set_password("test-password")
    session.add(user)
    session.flush()
    for role_name in roles:
        role = session.query(db.Role).filter_by(name=role_name).one()
        user.roles.append(role)
    session.add(user)
    session.flush()
    return user


def test_is_platform_super_admin_roles(app):
    with app.app_context():
        session = q.get_session()
        super_user = _make_user(session, "super1", [SiteRole.SUPER_ADMIN.value])
        legacy_admin = _make_user(session, "legacy1", [SiteRole.ADMIN.value])
        org_admin = _make_user(session, "org1", [SiteRole.ORG_ADMIN.value])
        session.commit()

        assert is_platform_super_admin(super_user) is True
        assert is_platform_super_admin(legacy_admin) is False
        assert is_platform_super_admin(org_admin) is False


def test_org_admin_redirected_from_platform(app, client):
    with app.app_context():
        session = q.get_session()
        org = db.Group(name="Acme", slug="acme-admin-test")
        session.add(org)
        session.flush()
        user = _make_user(session, "orgonly", [SiteRole.ORG_ADMIN.value])
        user.organization_id = org.id
        session.add(db.UserGroups(user_id=user.id, group_id=org.id))
        session.add(user)
        session.commit()

    client.post(
        "/sign-in",
        data={"username": "orgonly", "password": "test-password"},
        follow_redirects=True,
    )
    r = client.get("/admin/platform/")
    assert r.status_code == 302
    assert "/admin/org" in r.headers["Location"]
