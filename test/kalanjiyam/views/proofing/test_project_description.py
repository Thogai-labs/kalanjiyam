"""Tests for the Description tab."""

from unittest.mock import patch

import pytest

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.utils import archival_taxonomy as at

URL = "/proofing/test-project/description"


@pytest.fixture(autouse=True)
def _single_tenant(flask_app):
    """Hold multi-tenancy off for these tests, then put it back.

    `flask_app` is session-scoped and several other test files switch
    `MULTI_TENANT_MODE` on without restoring it. Once it is on, the shared
    `test-project` is `is_publicly_viewable` with no organization, so
    `org_access.user_can_view_proofing_project` 403s every proofing route for
    everyone but a super admin -- which is why the whole of the sibling
    `test_project_metadata.py` fails in a full run and passes on its own.

    Scoped to this file rather than fixed in `conftest`: restoring the flag
    globally turns roughly sixty of those failures green but flips four tests in
    `test_public_books.py` and `test_main.py` red, because those pass only on the
    leaked state. Untangling that is its own job.
    """
    before = flask_app.config.get("MULTI_TENANT_MODE")
    flask_app.config["MULTI_TENANT_MODE"] = False
    yield
    flask_app.config["MULTI_TENANT_MODE"] = before


@pytest.fixture(autouse=True)
def _empty_description(flask_app):
    """`flask_app` is session-scoped, so curation leaks between tests."""
    with flask_app.app_context():
        session = get_session()
        session.query(db.MetadataEvidence).delete()
        session.query(db.MetadataField).delete()
        session.query(db.MetadataWindow).delete()
        session.query(db.MetadataExtractionRun).delete()
        session.commit()
    yield


# Access control
# --------------


def test_description__anonymous_is_redirected(client):
    assert client.get(URL).status_code == 302


def test_description__ordinary_proofreader_is_redirected(rama_client):
    assert rama_client.get(URL).status_code == 302


def test_description__moderator_can_view(moderator_client):
    assert moderator_client.get(URL).status_code == 200


def test_description__unknown_project_is_404(moderator_client):
    assert moderator_client.get("/proofing/nope/description").status_code == 404


def test_tab_is_listed_for_moderators(moderator_client):
    assert "/description" in moderator_client.get("/proofing/test-project/").text


def test_the_smoke_test_tab_is_gone(moderator_client):
    resp = moderator_client.get(
        "/proofing/test-project/metadata-test", follow_redirects=True
    )
    assert resp.status_code == 404


# Extraction
# ----------


def test_extract__enqueues_a_run(moderator_client):
    with patch(
        "kalanjiyam.tasks.archival_extract.extract_archival_metadata.delay"
    ) as delay:
        resp = moderator_client.post(f"{URL}/extract", follow_redirects=True)

    assert resp.status_code == 200
    assert delay.called
    assert delay.call_args[1]["force"] is False


def test_extract__passes_the_force_flag(moderator_client):
    with patch(
        "kalanjiyam.tasks.archival_extract.extract_archival_metadata.delay"
    ) as delay:
        moderator_client.post(
            f"{URL}/extract", data={"force": "1"}, follow_redirects=True
        )

    assert delay.call_args[1]["force"] is True


def test_extract__is_moderator_only(rama_client):
    with patch(
        "kalanjiyam.tasks.archival_extract.extract_archival_metadata.delay"
    ) as delay:
        resp = rama_client.post(f"{URL}/extract")
    assert resp.status_code == 302
    assert not delay.called


def test_status__reports_idle_when_nothing_is_running(moderator_client):
    with patch("kalanjiyam.tasks.archival_extract.get_progress", return_value=None):
        resp = moderator_client.get(f"{URL}/status")
    assert resp.json["status"] == "idle"


def test_status__reports_the_current_window(moderator_client):
    progress = {"status": "IN_PROGRESS", "stage": "reading window 3 of 24", "done": 3}
    with patch("kalanjiyam.tasks.archival_extract.get_progress", return_value=progress):
        resp = moderator_client.get(f"{URL}/status")
    assert resp.json["stage"] == "reading window 3 of 24"


# Curation
# --------


def _curated(flask_app, code):
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        return (
            session.query(db.MetadataField)
            .filter_by(project_id=project.id, tag_code=code, run_id=None)
            .first()
        )


def test_curate__saves_a_write_locked_tag(moderator_client, flask_app):
    resp = moderator_client.post(
        f"{URL}/curate",
        data={"tag_code": "CUSTODIAL HISTORY", "value": "Transferred in 1947."},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert _curated(flask_app, "CUSTODIAL HISTORY").curated_value == (
        "Transferred in 1947."
    )


def test_curate__an_empty_value_clears_it(moderator_client, flask_app):
    moderator_client.post(
        f"{URL}/curate",
        data={"tag_code": "ACCESS", "value": "Open."},
        follow_redirects=True,
    )
    moderator_client.post(
        f"{URL}/curate",
        data={"tag_code": "ACCESS", "value": ""},
        follow_redirects=True,
    )
    assert _curated(flask_app, "ACCESS") is None


def test_curate__rejects_an_entity_tag(moderator_client, flask_app):
    """A textarea cannot express a list of access points, so it must not try."""
    moderator_client.post(
        f"{URL}/curate",
        data={"tag_code": "PERSON NAME", "value": "Ahmad Yar Khan"},
        follow_redirects=True,
    )
    assert _curated(flask_app, "PERSON NAME") is None


def test_curate__rejects_an_unknown_tag(moderator_client, flask_app):
    resp = moderator_client.post(
        f"{URL}/curate",
        data={"tag_code": "NOT A TAG", "value": "x"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert _curated(flask_app, "NOT A TAG") is None


def test_curate__is_moderator_only(rama_client, flask_app):
    resp = rama_client.post(
        f"{URL}/curate", data={"tag_code": "ACCESS", "value": "Open."}
    )
    assert resp.status_code == 302
    assert _curated(flask_app, "ACCESS") is None


def test_the_locked_tags_are_offered_for_entry(moderator_client):
    """The three archivist-only tags must be reachable from the page itself."""
    body = moderator_client.get(URL).text
    for code in at.WRITE_LOCKED:
        assert code in body
