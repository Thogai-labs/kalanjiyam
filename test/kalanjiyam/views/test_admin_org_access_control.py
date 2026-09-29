import pytest
import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils import project_utils


def test_org_admin_access_control_unauth(client, flask_app):
    # Unauthenticated
    resp = client.get("/admin/org/access_control")
    assert resp.status_code == 404

    with flask_app.app_context():
        session = q.get_session()
        proofer_user = db.User(username="regular_proofer_ac", email="proofer_ac@test.local")
        proofer_user.set_password("pass123")
        session.add(proofer_user)
        session.commit()

        proofer_client = flask_app.test_client(user=proofer_user)
        resp = proofer_client.get("/admin/org/access_control")
        assert resp.status_code == 404


def test_org_admin_access_control_user_list(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = db.Group(name="Org Access Control Test", slug="org-ac-test")
        session.add(org)
        session.flush()

        admin_user = db.User(username="ac_org_admin", email="ac_admin@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org.id))

        # Member 1: Default mode (unrestricted)
        user_default = db.User(username="ac_user_default", email="default@test.local")
        user_default.set_password("pass123")
        user_default.organization_id = org.id
        session.add(user_default)
        session.flush()
        session.add(db.UserGroups(user_id=user_default.id, group_id=org.id))

        # Member 2: Restricted mode with 2 folders
        user_restricted = db.User(username="ac_user_restricted", email="restricted@test.local")
        user_restricted.set_password("pass123")
        user_restricted.organization_id = org.id
        session.add(user_restricted)
        session.flush()
        session.add(db.UserGroups(user_id=user_restricted.id, group_id=org.id))

        project_utils.set_user_folder_restriction(session, org.id, user_restricted.id, True)
        project_utils.grant_user_folder_access(session, org.id, user_restricted.id, "tamil_classics")
        project_utils.grant_user_folder_access(session, org.id, user_restricted.id, "archive/2026")
        session.commit()

        client = flask_app.test_client(user=admin_user)

        # GET access control user list view
        resp = client.get("/admin/org/access_control")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "Access Control" in html
        assert "ac_user_default" in html
        assert "ac_user_restricted" in html
        assert "All Folders" in html
        assert "tamil_classics" in html
        assert "archive/2026" in html
        assert "/admin/org/access_control/user/" in html


def test_org_admin_user_access_control_get_and_post_flows(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = db.Group(name="Org AC Flow", slug="org-ac-flow")
        session.add(org)
        session.flush()

        admin_user = db.User(username="ac_flow_admin", email="flow_admin@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org.id))

        target_user = db.User(username="target_member", email="target@test.local")
        target_user.set_password("pass123")
        target_user.organization_id = org.id
        session.add(target_user)
        session.flush()
        session.add(db.UserGroups(user_id=target_user.id, group_id=org.id))

        # Add folders in this org
        project_utils.ensure_proof_folder(
            session, "literature/poetry", creator_id=admin_user.id, organization_id=org.id
        )
        project_utils.ensure_proof_folder(
            session, "history/ancient", creator_id=admin_user.id, organization_id=org.id
        )

        board = session.query(db.Board).first() or db.Board()
        session.add(board)
        session.flush()

        p1 = db.Project(
            display_title="Book 1",
            slug="b1-slug-ac",
            folder="literature/poetry",
            board_id=board.id,
            creator_id=admin_user.id,
        )
        p1.groups.append(org)
        session.add(p1)
        session.commit()

        client = flask_app.test_client(user=admin_user)

        # 1. GET user access page
        url = f"/admin/org/access_control/user/{target_user.id}"
        resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert "target_member" in html
        assert "Folder Permissions" in html
        assert "Default Mode (All Folders)" in html
        assert "literature/poetry" in html
        assert "history/ancient" in html

        # 2. POST with folder checked -> dynamically activates Restricted Mode
        post_resp = client.post(
            url,
            data={
                "folders": ["literature/poetry"],
            },
            follow_redirects=True,
        )
        assert post_resp.status_code == 200

        # Verify in DB
        assert project_utils.is_user_folder_restricted(target_user, org.id, session=session) is True
        granted = project_utils.get_user_accessible_folder_paths(target_user, org.id, session=session)
        assert granted == {"literature/poetry"}

        # 3. POST with no folders checked -> dynamically reverts to Default Mode
        post_resp2 = client.post(
            url,
            data={},
            follow_redirects=True,
        )
        assert post_resp2.status_code == 200

        # Verify user is now unrestricted
        assert project_utils.is_user_folder_restricted(target_user, org.id, session=session) is False
        assert project_utils.get_user_accessible_folder_paths(target_user, org.id, session=session) is None


def test_org_admin_cannot_access_other_org_user(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org1 = db.Group(name="Org One AC", slug="org1-ac")
        org2 = db.Group(name="Org Two AC", slug="org2-ac")
        session.add_all([org1, org2])
        session.flush()

        admin_org1 = db.User(username="admin_org1", email="admin1@test.local")
        admin_org1.set_password("pass123")
        admin_org1.organization_id = org1.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_org1.roles.append(org_role)
        session.add(admin_org1)
        session.flush()
        session.add(db.UserGroups(user_id=admin_org1.id, group_id=org1.id))

        user_org2 = db.User(username="user_org2", email="user2@test.local")
        user_org2.set_password("pass123")
        user_org2.organization_id = org2.id
        session.add(user_org2)
        session.flush()
        session.add(db.UserGroups(user_id=user_org2.id, group_id=org2.id))
        session.commit()

        client = flask_app.test_client(user=admin_org1)
        resp = client.get(f"/admin/org/access_control/user/{user_org2.id}")
        assert resp.status_code == 404
