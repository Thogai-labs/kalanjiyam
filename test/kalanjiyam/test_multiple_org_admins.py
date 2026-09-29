"""Tests verifying multiple organization admins per organization."""

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.admin_user import soft_delete_user, sync_user_org_and_roles
from kalanjiyam.enums import SiteRole


def _make_org(session, slug: str, name: str) -> db.Group:
    org = db.Group(name=name, slug=slug)
    session.add(org)
    session.flush()
    return org


def _make_user(
    session, username: str, org: db.Group | None, roles: list[str]
) -> db.User:
    user = db.User(username=username, email=f"{username}@test.local")
    user.set_password("test-password")
    if org is not None:
        user.organization_id = org.id
    session.add(user)
    session.flush()
    if org is not None:
        session.add(db.UserGroups(user_id=user.id, group_id=org.id))
    for role_name in roles:
        role = session.query(db.Role).filter_by(name=role_name).one()
        user.roles.append(role)
    session.add(user)
    session.flush()
    return user


def test_group_multiple_org_admins_model_and_query(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "multi-admin-org", "Multi Admin Org")

        admin1 = _make_user(session, "admin_one", org, [SiteRole.ORG_ADMIN.value])
        admin2 = _make_user(session, "admin_two", org, [SiteRole.ORG_ADMIN.value])
        regular = _make_user(session, "regular_member", org, [SiteRole.P1.value])
        org.admin_user_id = admin1.id
        session.commit()

        # Both users have is_org_admin == True
        assert admin1.is_org_admin is True
        assert admin2.is_org_admin is True
        assert regular.is_org_admin is False

        # Group model properties
        admin_ids = {u.id for u in org.admin_users}
        assert admin1.id in admin_ids
        assert admin2.id in admin_ids
        assert regular.id not in admin_ids
        assert len(admin_ids) == 2

        # org_admins alias
        assert {u.id for u in org.org_admins} == admin_ids

        # has_admin helper
        assert org.has_admin(admin1.id) is True
        assert org.has_admin(admin2.id) is True
        assert org.has_admin(regular.id) is False

        # queries helper
        query_admins = q.org_admins_for_group(org.id)
        assert {u.id for u in query_admins} == admin_ids


def test_multiple_org_admins_can_both_access_org_dashboard(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "two-admins-org", "Two Admins Org")
        admin1 = _make_user(session, "primary_adm", org, [SiteRole.ORG_ADMIN.value])
        admin2 = _make_user(session, "secondary_adm", org, [SiteRole.ORG_ADMIN.value])
        org.admin_user_id = admin1.id
        session.commit()

        # Admin 1 (Primary) accesses dashboard
        client1 = flask_app.test_client(user=admin1)
        resp1 = client1.get("/admin/org/")
        assert resp1.status_code == 200
        html1 = resp1.data.decode("utf-8")
        assert "Primary Admin" in html1
        assert "Org Admin" in html1
        assert "primary_adm" in html1
        assert "secondary_adm" in html1

        # Admin 2 (Secondary) accesses dashboard
        client2 = flask_app.test_client(user=admin2)
        resp2 = client2.get("/admin/org/")
        assert resp2.status_code == 200
        html2 = resp2.data.decode("utf-8")
        assert "Primary Admin" in html2
        assert "Org Admin" in html2

        # Both can access access_control
        ac_resp1 = client1.get("/admin/org/access_control")
        assert ac_resp1.status_code == 200
        ac_resp2 = client2.get("/admin/org/access_control")
        assert ac_resp2.status_code == 200


def test_org_admin_create_another_org_admin(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "create-admin-org", "Create Admin Org")
        admin1 = _make_user(session, "initial_admin", org, [SiteRole.ORG_ADMIN.value])
        org.admin_user_id = admin1.id
        session.commit()

        client = flask_app.test_client(user=admin1)

        # GET user create page has org_admin option
        get_resp = client.get("/admin/org/user/create")
        assert get_resp.status_code == 200
        assert b'value="org_admin"' in get_resp.data

        # POST creates new user with org_admin role
        post_resp = client.post(
            "/admin/org/user/create",
            data={
                "username": "brand_new_admin",
                "email": "brand_new_admin@test.local",
                "password": "pass_for_new_admin",
                "role_name": "org_admin",
            },
            follow_redirects=True,
        )
        assert post_resp.status_code == 200

        new_admin = session.query(db.User).filter_by(username="brand_new_admin").first()
        assert new_admin is not None
        assert new_admin.is_org_admin is True
        assert new_admin.organization_id == org.id
        # Primary admin remains admin1
        assert org.admin_user_id == admin1.id


def test_org_admin_promote_and_demote_member(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "promote-org", "Promote Org")
        admin1 = _make_user(session, "promo_admin", org, [SiteRole.ORG_ADMIN.value])
        member = _make_user(session, "promo_member", org, [SiteRole.P1.value])
        org.admin_user_id = admin1.id
        session.commit()

        client = flask_app.test_client(user=admin1)

        # Promote member to org_admin
        resp = client.post(
            "/admin/org/",
            data={
                "action": "change_role",
                "user_id": str(member.id),
                "role_name": "org_admin",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        session.refresh(member)
        assert member.is_org_admin is True

        # Demote newly promoted org_admin back to moderator
        resp_demote = client.post(
            "/admin/org/",
            data={
                "action": "change_role",
                "user_id": str(member.id),
                "role_name": "moderator",
            },
            follow_redirects=True,
        )
        assert resp_demote.status_code == 200
        session.refresh(member)
        assert member.is_org_admin is False
        assert member.is_moderator is True


def test_org_admin_designate_primary_admin(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "primary-swap-org", "Primary Swap Org")
        admin1 = _make_user(session, "swap_admin1", org, [SiteRole.ORG_ADMIN.value])
        admin2 = _make_user(session, "swap_admin2", org, [SiteRole.ORG_ADMIN.value])
        org.admin_user_id = admin1.id
        session.commit()

        client = flask_app.test_client(user=admin1)

        # Set admin2 as primary admin
        resp = client.post(
            "/admin/org/",
            data={
                "action": "set_primary_admin",
                "user_id": str(admin2.id),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"is now designated as primary admin" in resp.data

        session.refresh(org)
        assert org.admin_user_id == admin2.id


def test_org_admin_cannot_remove_primary_admin_or_self(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "remove-guard-org", "Remove Guard Org")
        admin1 = _make_user(session, "guard_primary", org, [SiteRole.ORG_ADMIN.value])
        admin2 = _make_user(session, "guard_secondary", org, [SiteRole.ORG_ADMIN.value])
        org.admin_user_id = admin1.id
        session.commit()
        org_id = org.id
        admin1_id = admin1.id
        admin2_id = admin2.id

    # Admin 2 tries to remove Admin 1 (primary) -> blocked
    client2 = flask_app.test_client(user=admin2)
    resp1 = client2.post(
        "/admin/org/",
        data={
            "action": "remove_user",
            "user_id": str(admin1_id),
        },
        follow_redirects=True,
    )
    assert b"Cannot remove the primary organization administrator" in resp1.data
    with flask_app.app_context():
        assert admin1_id in [u.id for u in q.users_in_group(org_id)]

    # Admin 2 tries to remove themselves -> blocked
    resp2 = client2.post(
        "/admin/org/",
        data={
            "action": "remove_user",
            "user_id": str(admin2_id),
        },
        follow_redirects=True,
    )
    assert b"You cannot remove yourself" in resp2.data
    with flask_app.app_context():
        assert admin2_id in [u.id for u in q.users_in_group(org_id)]

    # Primary admin removes secondary admin -> succeeds and removes org_admin role
    client1 = flask_app.test_client(user=admin1)
    resp3 = client1.post(
        "/admin/org/",
        data={
            "action": "remove_user",
            "user_id": str(admin2_id),
        },
        follow_redirects=True,
    )
    assert b"User removed from organization" in resp3.data
    with flask_app.app_context():
        assert admin2_id not in [u.id for u in q.users_in_group(org_id)]
        session = q.get_session()
        admin2_db = session.query(db.User).filter_by(id=admin2_id).first()
        assert admin2_db.is_org_admin is False


def test_super_admin_groups_view_multiple_admins(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        super_admin = _make_user(
            session, "super_groups_adm", None, [SiteRole.SUPER_ADMIN.value]
        )
        user_a = _make_user(session, "group_adm_a", None, [SiteRole.P1.value])
        user_b = _make_user(session, "group_adm_b", None, [SiteRole.P1.value])
        session.commit()

        client = flask_app.test_client(user=super_admin)

        # Create organization with two org admins
        resp = client.post(
            "/admin/groups/create",
            data={
                "name": "Super Multi Org",
                "slug": "super-multi-org",
                "admin_user_ids": [str(user_a.id), str(user_b.id)],
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

        created_org = session.query(db.Group).filter_by(slug="super-multi-org").first()
        assert created_org is not None
        assert created_org.admin_user_id == user_a.id

        session.refresh(user_a)
        session.refresh(user_b)
        assert user_a.is_org_admin is True
        assert user_b.is_org_admin is True

        # Edit organization to keep only user_b as admin
        edit_resp = client.post(
            f"/admin/groups/edit/{created_org.id}",
            data={
                "name": "Super Multi Org Updated",
                "slug": "super-multi-org",
                "admin_user_ids": [str(user_b.id)],
            },
            follow_redirects=True,
        )
        assert edit_resp.status_code == 200

        session.refresh(created_org)
        session.refresh(user_a)
        session.refresh(user_b)
        assert created_org.admin_user_id == user_b.id
        assert user_b.is_org_admin is True
        assert user_a.is_org_admin is False


def test_cli_create_multiple_org_admins(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "cli-multi-org", "CLI Multi Org")
        session.commit()

        # First org admin
        org_admin_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        u1 = db.User(username="cli_admin_1", email="cli_admin1@test.local")
        u1.set_password("pass1")
        session.add(u1)
        session.flush()
        u1.roles.append(org_admin_role)
        u1.organization_id = org.id
        if not org.admin_user_id:
            org.admin_user_id = u1.id
        session.add(db.UserGroups(user_id=u1.id, group_id=org.id))
        session.add_all([u1, org])
        session.commit()

        # Second org admin added to same org
        u2 = db.User(username="cli_admin_2", email="cli_admin2@test.local")
        u2.set_password("pass2")
        session.add(u2)
        session.flush()
        u2.roles.append(org_admin_role)
        u2.organization_id = org.id
        if not org.admin_user_id:
            org.admin_user_id = u2.id
        session.add(db.UserGroups(user_id=u2.id, group_id=org.id))
        session.add_all([u2, org])
        session.commit()

        session.refresh(org)
        session.refresh(u1)
        session.refresh(u2)

        assert u1.is_org_admin is True
        assert u2.is_org_admin is True
        assert u1.organization_id == org.id
        assert u2.organization_id == org.id
        # Primary admin remains u1
        assert org.admin_user_id == u1.id


def test_admin_user_sync_and_soft_delete_with_multiple_admins(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "sync-softdel-org", "Sync Softdel Org")
        adm1 = _make_user(session, "sync_adm1", org, [SiteRole.ORG_ADMIN.value])
        org.admin_user_id = adm1.id
        session.commit()

        # Add second org admin via sync_user_org_and_roles
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        user2 = db.User(username="sync_adm2", email="sync_adm2@test.local")
        user2.set_password("pass")
        session.add(user2)
        session.flush()

        class FakeForm:
            password = type("Field", (), {"data": ""})()
            role_ids = type("Field", (), {"data": [org_role.id]})()
            organization_pick = type("Field", (), {"data": org.id})()
            organization_id = None
            organization_ids = None

        sync_user_org_and_roles(FakeForm(), user2, session, is_created=False)
        session.commit()

        session.refresh(org)
        session.refresh(user2)
        assert user2.is_org_admin is True
        # adm1 is still primary admin
        assert org.admin_user_id == adm1.id

        # Soft delete the primary admin (adm1)
        soft_delete_user(adm1, session)
        session.commit()

        session.refresh(org)
        # Primary admin should be reassigned to user2
        assert org.admin_user_id == user2.id
