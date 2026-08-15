"""Tests for the Metadata tab.

Access is moderators and admins only; `is_moderator` already resolves to
MODERATOR | ADMIN | SUPER_ADMIN.
"""

from unittest.mock import patch

import kalanjiyam.database as db
from kalanjiyam.queries import get_session

URL = "/proofing/test-project/metadata"


# Access control
# --------------


def test_metadata__anonymous_is_redirected(client):
    resp = client.get(URL)
    assert resp.status_code == 302


def test_metadata__ordinary_proofreader_is_redirected(rama_client):
    resp = rama_client.get(URL)
    assert resp.status_code == 302


def test_metadata__moderator_can_view(moderator_client):
    resp = moderator_client.get(URL)
    assert resp.status_code == 200


def test_metadata__admin_can_view(admin_client):
    resp = admin_client.get(URL)
    assert resp.status_code == 200


def test_metadata__unknown_project_is_404(moderator_client):
    resp = moderator_client.get("/proofing/does-not-exist/metadata")
    assert resp.status_code == 404


def test_tab_is_hidden_from_non_moderators(rama_client, moderator_client):
    """The tab is not merely gated -- it is not shown at all."""
    assert "/metadata" not in rama_client.get("/proofing/test-project/").text
    assert "/metadata" in moderator_client.get("/proofing/test-project/").text


def _org_admin_client(flask_app):
    """An org admin: the ORG_ADMIN role *and* an organization are both needed."""
    session = get_session()
    user = session.query(db.User).filter_by(username="u-org-admin").first()
    if user is None:
        org = db.Group(slug="test-org", name="Test Org")
        session.add(org)
        session.flush()

        # `org_admin` is seeded from SiteRole by conftest's role seeding.
        role = session.query(db.Role).filter_by(name="org_admin").one()
        user = db.User(username="u-org-admin", email="u_org_admin@siddhasagaram.in")
        user.set_password("pass_org_admin")
        user.organization_id = org.id
        session.add(user)
        session.flush()
        user.roles = [role]
        session.commit()

    return flask_app.test_client(user=user)


def test_metadata__org_admin_can_view(flask_app):
    """`moderator_required` admits org admins, so the tab must show for them."""
    with flask_app.app_context():
        client = _org_admin_client(flask_app)
    resp = client.get(URL)
    assert resp.status_code == 200


def test_tab_is_visible_to_org_admins(flask_app):
    """Regression: gating the tab on `is_mod` alone hid it from users who
    could reach the route by URL."""
    with flask_app.app_context():
        client = _org_admin_client(flask_app)
    assert "/metadata" in client.get("/proofing/test-project/").text


# Saving
# ------


def test_metadata__save_round_trips(moderator_client, flask_app):
    resp = moderator_client.post(
        URL,
        data={
            "print_title": "A Printed Title",
            "author": "An Author",
            "place_of_publication": "Madras",
            "edition": "1st",
            "series": "Some Series",
            "subject": "Poetics",
            "languages": "sa, Deva, primary\nen, Latn, translation",
            "summary": "A short summary.",
            "keywords": "kavya, alankara",
            "toc": "Sarga 1 | 12\nSarga 2 | 40",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()

        assert project.print_title == "A Printed Title"
        assert project.author == "An Author"
        assert project.place_of_publication == "Madras"

        data = project.extracted_metadata
        assert data["languages"][0] == {
            "code": "sa",
            "script": "Deva",
            "role": "primary",
        }
        assert data["content"]["summary"] == "A short summary."
        assert data["content"]["keywords"] == ["kavya", "alankara"]
        assert data["content"]["toc"][0] == {"label": "Sarga 1", "page": "12"}


def test_metadata__save_reindexes_the_project(moderator_client):
    """Title and author are indexed on every page document."""
    with patch("kalanjiyam.tasks.search_index.enqueue_project") as enqueue:
        moderator_client.post(
            URL, data={"print_title": "Reindex Me"}, follow_redirects=True
        )
    assert enqueue.called


# Extraction
# ----------


def test_extract__the_sampling_extractor_is_gone(moderator_client):
    """Retired in favour of the full-text pass on the description tab.

    Asserted rather than merely deleted: leaving the route reachable would mean a
    six-page sample could still overwrite what a whole-document run established.
    """
    assert moderator_client.post(f"{URL}/extract").status_code == 404
    assert moderator_client.get(f"{URL}/status").status_code == 404


def test_accept__without_a_staged_run_reports_nothing_to_load(moderator_client):
    with patch("kalanjiyam.tasks.metadata.accept_staged", return_value=False):
        resp = moderator_client.post(f"{URL}/accept", follow_redirects=True)
    assert resp.status_code == 200
