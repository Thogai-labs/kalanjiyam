"""The admin search-index dashboards.

The important assertions here are the authorization ones: an org admin must
not be able to reach, read, or act on another organization's index, however
the request is shaped.
"""

import pytest

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.queries import get_session


@pytest.fixture()
def two_orgs(flask_app):
    """Two organizations, each with an org admin and a project."""
    with flask_app.app_context():
        session = get_session()
        made = {}
        for name in ("alpha", "beta"):
            org = session.query(db.Group).filter_by(slug=f"{name}-org").first()
            if org is None:
                org = db.Group(name=name.title(), slug=f"{name}-org")
                session.add(org)
                session.flush()

            admin = session.query(db.User).filter_by(username=f"{name}-admin").first()
            if admin is None:
                admin = db.User(username=f"{name}-admin", email=f"{name}@test.local")
                admin.set_password("pw")
                session.add(admin)
                session.flush()
                role = session.query(db.Role).filter_by(
                    name=SiteRole.ORG_ADMIN.value
                ).one()
                admin.roles.append(role)
            admin.organization_id = org.id

            project = session.query(db.Project).filter_by(slug=f"{name}-book").first()
            if project is None:
                board = db.Board(title=f"{name}-board")
                session.add(board)
                session.flush()
                project = db.Project(
                    slug=f"{name}-book",
                    display_title=f"{name.title()} Book",
                    board_id=board.id,
                )
                session.add(project)
                session.flush()
                session.add(
                    db.ProjectGroups(project_id=project.id, group_id=org.id)
                )
            session.commit()
            made[name] = {"org_id": org.id, "admin_id": admin.id, "project_id": project.id}
        return made


def _client_for(flask_app, user_id):
    session = get_session()
    return flask_app.test_client(user=session.query(db.User).get(user_id))


def test_platform_dashboard_requires_super_admin(client, rama_client):
    """Platform routes are hidden, not merely forbidden."""
    assert client.get("/admin/platform/search_index").status_code in (302, 404)
    assert rama_client.get("/admin/platform/search_index").status_code in (302, 404)


def test_super_admin_sees_the_dashboard(superadmin_client):
    resp = superadmin_client.get("/admin/platform/search_index")
    assert resp.status_code == 200
    assert b"Search index" in resp.data


def test_dashboard_renders_with_the_cluster_down(superadmin_client):
    """This is exactly when an admin needs to look at it."""
    resp = superadmin_client.get("/admin/platform/search_index")
    assert resp.status_code == 200
    assert b"Search disabled" in resp.data


def test_status_endpoint_returns_json(superadmin_client):
    resp = superadmin_client.get("/admin/platform/search_index/status")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "health" in payload
    assert "jobs" in payload


def test_org_admin_reaches_only_the_org_dashboard(flask_app, two_orgs):
    alpha = _client_for(flask_app, two_orgs["alpha"]["admin_id"])

    assert alpha.get("/admin/org/search_index").status_code == 200
    # Platform routes 404 for org-scoped admins, or redirect them home.
    assert alpha.get("/admin/platform/search_index").status_code in (302, 404)


def test_org_admin_status_is_scoped_to_their_org(flask_app, two_orgs):
    alpha = _client_for(flask_app, two_orgs["alpha"]["admin_id"])
    payload = alpha.get("/admin/org/search_index/status").get_json()

    org_ids = {org["id"] for org in payload["orgs"]}
    assert org_ids == {two_orgs["alpha"]["org_id"]}
    assert two_orgs["beta"]["org_id"] not in org_ids


def test_org_admin_cannot_rebuild_another_org(flask_app, two_orgs):
    """A forged org_id in the form must be ignored, not obeyed."""
    from kalanjiyam.models.search import SearchIndexJob

    alpha = _client_for(flask_app, two_orgs["alpha"]["admin_id"])
    beta_org_id = two_orgs["beta"]["org_id"]

    alpha.post(
        "/admin/org/search_index",
        data={"action": "rebuild", "org_id": beta_org_id},
        follow_redirects=True,
    )

    with flask_app.app_context():
        session = get_session()
        leaked = (
            session.query(SearchIndexJob)
            .filter(SearchIndexJob.scope_org_id == beta_org_id)
            .first()
        )
        assert leaked is None


def test_org_admin_cannot_reindex_another_orgs_book(flask_app, two_orgs):
    from kalanjiyam.models.search import SearchIndexJob

    alpha = _client_for(flask_app, two_orgs["alpha"]["admin_id"])
    beta_project_id = two_orgs["beta"]["project_id"]

    resp = alpha.post(
        "/admin/org/search_index",
        data={"action": "reindex_project", "project_id": beta_project_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"cannot reindex that book" in resp.data

    with flask_app.app_context():
        session = get_session()
        leaked = (
            session.query(SearchIndexJob)
            .filter(SearchIndexJob.scope_project_id == beta_project_id)
            .first()
        )
        assert leaked is None


def test_actions_are_refused_while_search_is_disabled(superadmin_client):
    """No job row should be created for a cluster that is switched off."""
    from kalanjiyam.models.search import SearchIndexJob

    session = q.get_session()
    before = session.query(SearchIndexJob).count()

    resp = superadmin_client.post(
        "/admin/platform/search_index",
        data={"action": "rebuild"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Search is disabled" in resp.data

    assert session.query(SearchIndexJob).count() == before


def test_drop_requires_the_slug_typed_exactly(flask_app, two_orgs, superadmin_client):
    from kalanjiyam.models.search import SearchIndexJob

    org_id = two_orgs["alpha"]["org_id"]
    resp = superadmin_client.post(
        "/admin/platform/search_index",
        data={"action": "drop", "org_id": org_id, "confirm": "wrong-slug"},
        follow_redirects=True,
    )
    assert b"Type the organization" in resp.data

    with flask_app.app_context():
        session = get_session()
        assert (
            session.query(SearchIndexJob)
            .filter(SearchIndexJob.job_type == "DROP")
            .count()
            == 0
        )


def test_unknown_action_is_rejected(superadmin_client):
    resp = superadmin_client.post(
        "/admin/platform/search_index",
        data={"action": "definitely-not-an-action"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Unknown action" in resp.data
