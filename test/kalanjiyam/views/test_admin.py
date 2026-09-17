import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole


def test_admin_index__unauth(client):
    resp = client.get("/admin/")
    assert resp.status_code == 404


def test_admin_index__auth_admin(admin_client):
    resp = admin_client.get("/admin/")
    assert resp.status_code == 302


def test_admin_index__auth_moderator(moderator_client):
    resp = moderator_client.get("/admin/")
    assert resp.status_code == 200


def test_admin_index__inactive(deleted_client, banned_client):
    assert deleted_client.get("/admin/").status_code == 404
    assert banned_client.get("/admin/").status_code == 404


def test_admin_text__unauth(client):
    resp = client.get("/admin/text/")
    assert resp.status_code == 404


def test_admin_text__admin_or_moderator(admin_client, moderator_client):
    resp = admin_client.get("/admin/projectsponsorship/")
    assert resp.status_code == 200

    resp = moderator_client.get("/admin/projectsponsorship/")
    assert resp.status_code == 200


def test_admin_text__admin_only(admin_client, moderator_client):
    resp = admin_client.get("/admin/text/")
    assert resp.status_code == 200

    resp = moderator_client.get("/admin/text/")
    assert resp.status_code == 404


def test_admin_text__inactive(deleted_client, banned_client):
    assert deleted_client.get("/admin/text/").status_code == 404
    assert banned_client.get("/admin/text/").status_code == 404


def test_org_admin_create_user_unauth(client):
    resp = client.get("/admin/org/user/create")
    assert resp.status_code == 404


def test_org_admin_create_user_flow(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = db.Group(name="Org Test Flow", slug="org-create-user-flow-test")
        session.add(org)
        session.flush()

        admin_user = db.User(username="test_org_admin_flow", email="orgadminflow@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org.id))
        session.commit()

        client = flask_app.test_client(user=admin_user)

        # GET separate UI page
        resp = client.get("/admin/org/user/create")
        assert resp.status_code == 200
        assert b"Create Organization User" in resp.data
        assert b"/admin/org/user/create" in resp.data

        # POST creates user
        post_resp = client.post(
            "/admin/org/user/create",
            data={
                "username": "new_proofer_user",
                "email": "new_proofer@test.local",
                "password": "secure_password_123",
                "role_name": "p2",
            },
            follow_redirects=True,
        )
        assert post_resp.status_code == 200

        # Check user in DB
        user = session.query(db.User).filter_by(username="new_proofer_user").first()
        assert user is not None
        assert user.email == "new_proofer@test.local"
        assert any(r.name == "p2" for r in user.roles)
        assert any(g.id == org.id for g in user.groups)

