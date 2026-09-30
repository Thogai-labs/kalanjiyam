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


def test_is_platform_super_admin_roles(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        super_user = _make_user(session, "super1", [SiteRole.SUPER_ADMIN.value])
        master_user = _make_user(session, "master1", [SiteRole.MASTER_USER.value])
        org_admin = _make_user(session, "org1", [SiteRole.ORG_ADMIN.value])
        session.commit()

        assert is_platform_super_admin(super_user) is True
        assert is_platform_super_admin(master_user) is False
        assert is_platform_super_admin(org_admin) is False


def test_org_admin_redirected_from_platform(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = db.Group(name="Acme", slug="acme-admin-test")
        session.add(org)
        session.flush()
        user = _make_user(session, "orgonly", [SiteRole.ORG_ADMIN.value])
        user.organization_id = org.id
        session.add(db.UserGroups(user_id=user.id, group_id=org.id))
        session.add(user)
        session.commit()

        org_client = flask_app.test_client(user=user)
        r = org_client.get("/admin/platform/")
        assert r.status_code == 302
        assert "/admin/org" in r.headers["Location"]


def test_meta_analyst_access_helpers(flask_app):
    with flask_app.app_context():
        from kalanjiyam.utils.admin_access import (
            can_access_meta_analytics,
            is_meta_analyst,
            is_platform_super_admin,
        )

        session = q.get_session()
        meta_user = _make_user(session, "meta1", [SiteRole.META_ANALYST.value])
        super_user = _make_user(session, "super_meta1", [SiteRole.SUPER_ADMIN.value])
        org_user = _make_user(session, "org_meta1", [SiteRole.ORG_ADMIN.value])
        session.commit()

        assert is_meta_analyst(meta_user) is True
        assert is_meta_analyst(super_user) is False
        assert is_meta_analyst(org_user) is False

        assert can_access_meta_analytics(meta_user) is True
        assert can_access_meta_analytics(super_user) is True
        assert can_access_meta_analytics(org_user) is False

        assert is_platform_super_admin(meta_user) is False


def test_meta_analyst_redirected_from_platform(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        user = _make_user(session, "metaonly", [SiteRole.META_ANALYST.value])
        session.commit()

        meta_client = flask_app.test_client(user=user)
        r = meta_client.get("/admin/platform/")
        assert r.status_code == 302
        assert "/admin/meta-analytics" in r.headers["Location"]


