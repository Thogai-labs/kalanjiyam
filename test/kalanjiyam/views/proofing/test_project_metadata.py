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


def test_extract__enqueues_a_background_run(moderator_client):
    with patch(
        "kalanjiyam.tasks.metadata.extract_project_metadata.delay"
    ) as delay:
        resp = moderator_client.post(f"{URL}/extract", follow_redirects=True)

    assert resp.status_code == 200
    assert delay.called
    assert delay.call_args[1]["deep"] is False


def test_extract__passes_the_deep_flag(moderator_client):
    with patch(
        "kalanjiyam.tasks.metadata.extract_project_metadata.delay"
    ) as delay:
        moderator_client.post(f"{URL}/extract", data={"deep": "1"}, follow_redirects=True)

    assert delay.call_args[1]["deep"] is True


def test_extract__is_moderator_only(rama_client):
    with patch("kalanjiyam.tasks.metadata.extract_project_metadata.delay") as delay:
        resp = rama_client.post(f"{URL}/extract")
    assert resp.status_code == 302
    assert not delay.called


def test_status__reports_idle_when_nothing_is_running(moderator_client):
    with patch("kalanjiyam.tasks.metadata.get_progress", return_value=None):
        resp = moderator_client.get(f"{URL}/status")
    assert resp.status_code == 200
    assert resp.json["status"] == "idle"


def test_status__reports_a_running_stage(moderator_client):
    progress = {"status": "running", "stage": "profiling scripts", "done": 1}
    with patch("kalanjiyam.tasks.metadata.get_progress", return_value=progress):
        resp = moderator_client.get(f"{URL}/status")
    assert resp.json["stage"] == "profiling scripts"


def test_accept__without_a_staged_run_reports_nothing_to_load(moderator_client):
    with patch("kalanjiyam.tasks.metadata.accept_staged", return_value=False):
        resp = moderator_client.post(f"{URL}/accept", follow_redirects=True)
    assert resp.status_code == 200
