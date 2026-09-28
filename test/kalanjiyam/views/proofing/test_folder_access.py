import json
import uuid
import pytest

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils import org_access, project_utils


@pytest.fixture
def org_setup(flask_app):
    """Set up an organization, org admin, and two regular users with projects and folders."""
    with flask_app.app_context():
        session = q.get_session()
        uid = uuid.uuid4().hex[:6]

        # Create Org
        org = db.Group(name=f"Folder Access Test Org {uid}", slug=f"fa-org-{uid}")
        session.add(org)
        session.flush()

        # Roles
        org_admin_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        p1_role = session.query(db.Role).filter_by(name=SiteRole.P1.value).first()

        # Create Org Admin
        admin_user = db.User(
            username=f"fa-admin-{uid}",
            email=f"fa_admin_{uid}@example.com",
            organization_id=org.id,
        )
        admin_user.set_password("pass")
        if org_admin_role:
            admin_user.roles.append(org_admin_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org.id))
        org.admin_user_id = admin_user.id

        # Create User A (will be restricted to Folder-A only)
        user_a = db.User(
            username=f"fa-user-a-{uid}",
            email=f"fa_user_a_{uid}@example.com",
            organization_id=org.id,
        )
        user_a.set_password("pass")
        if p1_role:
            user_a.roles.append(p1_role)
        session.add(user_a)
        session.flush()
        session.add(db.UserGroups(user_id=user_a.id, group_id=org.id))

        # Create User B (will be default unrestricted mode)
        user_b = db.User(
            username=f"fa-user-b-{uid}",
            email=f"fa_user_b_{uid}@example.com",
            organization_id=org.id,
        )
        user_b.set_password("pass")
        if p1_role:
            user_b.roles.append(p1_role)
        session.add(user_b)
        session.flush()
        session.add(db.UserGroups(user_id=user_b.id, group_id=org.id))

        # Create Folders: Folder-A and Folder-B
        folder_a = project_utils.ensure_proof_folder(
            session, "Folder-A", creator_id=admin_user.id, organization_id=org.id
        )
        folder_b = project_utils.ensure_proof_folder(
            session, "Folder-B", creator_id=admin_user.id, organization_id=org.id
        )

        board = session.query(db.Board).first()

        # Create Projects: Root project, Folder-A project, Folder-B project
        proj_root = db.Project(
            slug=f"proj-root-{uid}",
            display_title="Project in Root Buffer Zone",
            folder=None,
            board_id=board.id if board else None,
            is_publicly_viewable=False,
            creator_id=admin_user.id,
        )
        proj_root.groups.append(org)
        session.add(proj_root)

        proj_a = db.Project(
            slug=f"proj-folder-a-{uid}",
            display_title="Project in Folder A",
            folder="Folder-A",
            board_id=board.id if board else None,
            is_publicly_viewable=False,
            creator_id=admin_user.id,
        )
        proj_a.groups.append(org)
        session.add(proj_a)

        proj_b = db.Project(
            slug=f"proj-folder-b-{uid}",
            display_title="Project in Folder B",
            folder="Folder-B",
            board_id=board.id if board else None,
            is_publicly_viewable=False,
            creator_id=admin_user.id,
        )
        proj_b.groups.append(org)
        session.add(proj_b)

        session.commit()

        return {
            "org_id": org.id,
            "admin_user_id": admin_user.id,
            "user_a_id": user_a.id,
            "user_b_id": user_b.id,
            "folder_a": folder_a.path,
            "folder_b": folder_b.path,
            "proj_root_slug": proj_root.slug,
            "proj_a_slug": proj_a.slug,
            "proj_b_slug": proj_b.slug,
        }


def test_default_mode_user_sees_all_folders(flask_app, org_setup):
    """User with no access restrictions can view all folders (default mode)."""
    with flask_app.app_context():
        session = q.get_session()
        user_b = session.query(db.User).filter_by(id=org_setup["user_b_id"]).first()
        org_id = org_setup["org_id"]

        # User B has no restrictions
        assert not project_utils.is_user_folder_restricted(user_b, org_id, session=session)
        assert project_utils.get_user_accessible_folder_paths(user_b, org_id, session=session) is None

        # User B can access root, Folder-A, Folder-B
        assert project_utils.user_can_access_folder(user_b, "", org_id, session=session)
        assert project_utils.user_can_access_folder(user_b, "Folder-A", org_id, session=session)
        assert project_utils.user_can_access_folder(user_b, "Folder-B", org_id, session=session)

        # In available folders list, both Folder-A and Folder-B are present
        available = project_utils.get_all_available_folders(
            session, organization_id=org_id, user=user_b
        )
        assert "Folder-A" in available
        assert "Folder-B" in available

        # In workspace view, User B can access both folders
        client_b = flask_app.test_client(user=user_b)
        resp_a = client_b.get("/proofing/?folder=Folder-A")
        assert resp_a.status_code == 200
        assert "Project in Folder A" in resp_a.text

        resp_b = client_b.get("/proofing/?folder=Folder-B")
        assert resp_b.status_code == 200
        assert "Project in Folder B" in resp_b.text


def test_restricted_user_a_only_sees_folder_a(flask_app, org_setup):
    """If user A has access to folder A only, he can view folder A, but not folder B."""
    with flask_app.app_context():
        session = q.get_session()
        user_a = session.query(db.User).filter_by(id=org_setup["user_a_id"]).first()
        org_id = org_setup["org_id"]

        # Restrict User A and grant access to Folder-A only
        project_utils.set_user_folder_restriction(session, org_id, user_a.id, True)
        project_utils.grant_user_folder_access(session, org_id, user_a.id, "Folder-A")
        session.commit()

        # Verify restriction status
        assert project_utils.is_user_folder_restricted(user_a, org_id, session=session)
        allowed = project_utils.get_user_accessible_folder_paths(user_a, org_id, session=session)
        assert allowed == {"Folder-A"}

        # Root buffer zone is accessible
        assert project_utils.user_can_access_folder(user_a, "", org_id, session=session)
        # Folder A is accessible
        assert project_utils.user_can_access_folder(user_a, "Folder-A", org_id, session=session)
        # Folder B is NOT accessible
        assert not project_utils.user_can_access_folder(user_a, "Folder-B", org_id, session=session)

        # Available folders for user A only contains Folder-A
        available = project_utils.get_all_available_folders(
            session, organization_id=org_id, user=user_a
        )
        assert "Folder-A" in available
        assert "Folder-B" not in available

        # Check project permission checks
        proj_root = session.query(db.Project).filter_by(slug=org_setup["proj_root_slug"]).first()
        proj_a = session.query(db.Project).filter_by(slug=org_setup["proj_a_slug"]).first()
        proj_b = session.query(db.Project).filter_by(slug=org_setup["proj_b_slug"]).first()

        assert org_access.user_can_view_proofing_project(user_a, proj_root) is True
        assert org_access.user_can_view_proofing_project(user_a, proj_a) is True
        assert org_access.user_can_view_proofing_project(user_a, proj_b) is False

        # In workspace view, User A can access Folder-A
        client_a = flask_app.test_client(user=user_a)
        resp_a = client_a.get("/proofing/?folder=Folder-A")
        assert resp_a.status_code == 200
        assert "Project in Folder A" in resp_a.text

        # User A attempting to access Folder-B is redirected with access denied
        resp_b = client_a.get("/proofing/?folder=Folder-B")
        assert resp_b.status_code == 302
        assert resp_b.headers["Location"].endswith("/proofing/")

        # AJAX request gets 403
        resp_b_ajax = client_a.get("/proofing/?folder=Folder-B", headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp_b_ajax.status_code == 403


def test_root_buffer_zone_cannot_be_restricted(flask_app, org_setup):
    """Root folder cannot be configured in the access control system."""
    with flask_app.app_context():
        session = q.get_session()
        admin_user = session.query(db.User).filter_by(id=org_setup["admin_user_id"]).first()
        admin_client = flask_app.test_client(user=admin_user)

        # Root folder via GET /proofing/folders/access?folder=
        resp_get = admin_client.get("/proofing/folders/access?folder=")
        assert resp_get.status_code == 400
        data_get = resp_get.get_json()
        assert data_get["success"] is False
        assert "buffer zone" in data_get["error"]

        # Root folder via POST /proofing/folders/access
        resp_post = admin_client.post(
            "/proofing/folders/access",
            json={"folder": "", "users": []},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp_post.status_code == 400
        data_post = resp_post.get_json()
        assert data_post["success"] is False
        assert "buffer zone" in data_post["error"]


def test_header_access_control_shown_for_org_admin(flask_app, org_setup):
    """When folder is accessed by org_admin, show Access Control in place of Guidelines & Preparation."""
    with flask_app.app_context():
        session = q.get_session()
        admin_user = session.query(db.User).filter_by(id=org_setup["admin_user_id"]).first()
        user_a = session.query(db.User).filter_by(id=org_setup["user_a_id"]).first()

    admin_client = flask_app.test_client(user=admin_user)
    user_client = flask_app.test_client(user=user_a)

    # Org admin accessing root folder -> Guidelines & Preparation shown (Access Control button has x-show="folder")
    resp_admin_root = admin_client.get("/proofing/")
    assert resp_admin_root.status_code == 200
    assert "Guidelines &amp; Preparation" in resp_admin_root.text or "Guidelines & Preparation" in resp_admin_root.text
    assert "Access Control" in resp_admin_root.text  # The button exists with x-show="folder"

    # Org admin accessing a folder -> Access Control button is present
    resp_admin_folder = admin_client.get("/proofing/?folder=Folder-A")
    assert resp_admin_folder.status_code == 200
    assert "Access Control" in resp_admin_folder.text
    assert "openFolderAccessModal(folder)" in resp_admin_folder.text

    # Regular user accessing Folder-A -> Access Control button is NOT present
    resp_user_folder = user_client.get("/proofing/?folder=Folder-A")
    assert resp_user_folder.status_code == 200
    assert "Guidelines &amp; Preparation" in resp_user_folder.text or "Guidelines & Preparation" in resp_user_folder.text
    assert "openFolderAccessModal(folder)" not in resp_user_folder.text


def test_folder_access_api_get_and_post(flask_app, org_setup):
    """Org admin can retrieve and update folder access via API endpoints."""
    with flask_app.app_context():
        session = q.get_session()
        admin_user = session.query(db.User).filter_by(id=org_setup["admin_user_id"]).first()
        user_a = session.query(db.User).filter_by(id=org_setup["user_a_id"]).first()

    admin_client = flask_app.test_client(user=admin_user)
    user_client = flask_app.test_client(user=user_a)

    # Non-admin forbidden
    resp_forbidden = user_client.get("/proofing/folders/access?folder=Folder-A")
    assert resp_forbidden.status_code == 403

    # Org admin GET
    resp = admin_client.get("/proofing/folders/access?folder=Folder-A")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["folder"] == "Folder-A"
    assert len(data["users"]) >= 3  # Admin, User A, User B

    # Org admin POST: restrict user_a, grant access to Folder-A; keep user_b unrestricted
    post_data = {
        "folder": "Folder-A",
        "users": [
            {
                "user_id": org_setup["user_a_id"],
                "is_restricted": True,
                "has_access": True,
            },
            {
                "user_id": org_setup["user_b_id"],
                "is_restricted": False,
                "has_access": False,
            },
        ],
    }
    resp_post = admin_client.post(
        "/proofing/folders/access",
        json=post_data,
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_post.status_code == 200
    assert resp_post.get_json()["success"] is True

    # Verify through GET
    resp_verify = admin_client.get("/proofing/folders/access?folder=Folder-A")
    data_verify = resp_verify.get_json()
    users_map = {u["id"]: u for u in data_verify["users"]}
    assert users_map[org_setup["user_a_id"]]["is_restricted"] is True
    assert users_map[org_setup["user_a_id"]]["has_folder_access"] is True
    assert users_map[org_setup["user_b_id"]]["is_restricted"] is False
