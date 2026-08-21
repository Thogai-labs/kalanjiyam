import kalanjiyam.queries as q
from kalanjiyam.database import Project
from unittest.mock import MagicMock, patch



def test_summary(client):
    resp = client.get("/proofing/test-project/")
    assert resp.status_code == 200


def test_summary__bad_project(client):
    resp = client.get("/proofing/unknown/")
    assert resp.status_code == 404


def test_activity(client):
    resp = client.get("/proofing/test-project/activity")
    assert resp.status_code == 200


def test_activity__bad_project(client):
    resp = client.get("/proofing/unknown/activity")
    assert resp.status_code == 404


# For "Talk:" tests, see test_talk.py.


def test_edit__unauth(client):
    resp = client.get("/proofing/test-project/edit")
    assert resp.status_code == 302


def test_edit__auth(rama_client):
    resp = rama_client.get("/proofing/test-project/edit")
    assert "Edit:" in resp.text


def test_edit__auth__post_succeeds(rama_client):
    resp = rama_client.post(
        "/proofing/test-project/edit",
        data={
            "display_title": "Test Project",
            "description": "some description",
            "page_numbers": "",
            "author": "some author",
            "editor": "",
            "publisher": "some publisher",
            "publication_year": "",
        },
    )
    assert resp.status_code == 302


def test_edit__condition_tags(rama_client):
    import json
    tags = [
        {"name": "Shmushing", "pages": "1-2"},
        {"name": "Torn", "pages": "3"},
    ]
    resp = rama_client.post(
        "/proofing/test-project/edit",
        data={
            "display_title": "Test Project",
            "condition_tags": json.dumps(tags),
        },
    )
    assert resp.status_code == 302

    proj = q.project("test-project")
    assert len(proj.condition_tag_list) == 2
    assert proj.condition_tag_list[0]["name"] == "Shmushing"
    assert proj.condition_tag_list[0]["page_numbers"] == [1, 2]
    assert proj.condition_tag_list[1]["name"] == "Torn"
    assert proj.condition_tag_list[1]["page_numbers"] == [3]

    # Check summary page renders the tags and page issues
    summary_resp = rama_client.get("/proofing/test-project/")
    assert summary_resp.status_code == 200
    assert "Shmushing" in summary_resp.text
    assert "Torn" in summary_resp.text



def test_edit__auth__post_fails(rama_client):
    resp = rama_client.post(
        "/proofing/test-project/edit",
        data={
            # Bade page spec forces form to fail validation
            "page_numbers": "garbage in, garbage out",
        },
    )
    assert resp.status_code == 200.0
    assert "page number spec" in resp.text


def test_edit__auth__bad_project(rama_client):
    resp = rama_client.get("/proofing/unknown/edit")
    assert resp.status_code == 404


def test_download(client):
    resp = client.get("/proofing/test-project/download/")
    assert resp.status_code == 200


def test_download__bad_project(client):
    resp = client.get("/proofing/unknown/download/")
    assert resp.status_code == 404


def test_download_as_text(client):
    resp = client.get("/proofing/test-project/download/text")
    assert resp.status_code == 200


def test_download_as_text__bad_project(client):
    resp = client.get("/proofing/unknown/download/text")
    assert resp.status_code == 404


def test_download_as_xml(client):
    resp = client.get("/proofing/test-project/download/xml")
    assert resp.status_code == 200


def test_download_as_xml__bad_project(client):
    resp = client.get("/proofing/unknown/download/xml")
    assert resp.status_code == 404


def test_stats(moderator_client, rama_client):
    resp = moderator_client.get("/proofing/test-project/stats")
    assert resp.status_code == 200
    assert "Roman characters" in resp.text

    resp = rama_client.get("/proofing/test-project/stats")
    assert resp.status_code == 302


def test_search(rama_client):
    resp = rama_client.get("/proofing/test-project/search")
    assert "Search:" in resp.text


def test_search__bad_project(rama_client):
    resp = rama_client.get("/proofing/unknown/search")
    assert resp.status_code == 404


def test_replace(moderator_client):
    resp = moderator_client.get("/proofing/test-project/replace")
    assert "Replace:" in resp.text


def test_replace_post(moderator_client):
    resp = moderator_client.post(
        "/proofing/test-project/replace",
        data={
            "query": "the",
            "replace": "the",
        },
    )
    assert resp.status_code == 200


def test_replace__unauth(client):
    resp = client.get("/proofing/test-project/replace")
    assert resp.status_code == 302


def test_replace__bad_project(rama_client):
    resp = rama_client.get("/proofing/unknown/replace")
    assert resp.status_code == 404


def test_submit_changes(moderator_client):
    query = "test_query"
    replace = "test_replace"
    form_data = {"query": query, "replace": replace}
    resp = moderator_client.post(
        "/proofing/test-project/submit-changes", data=form_data
    )
    assert "Changes:" in resp.text


def test_submit_changes_post(moderator_client):
    resp = moderator_client.post(
        "/proofing/test-project/submit-changes",
        data={
            "query": "the",
            "replace": "the",
            "matches": [],
            "submit": True,
        },
    )

    assert resp.status_code == 200


def test_submit_unauth(client):
    resp = client.get("/proofing/test-project/submit-changes")
    assert resp.status_code == 302


def test_confirm_changes(moderator_client):
    resp = moderator_client.get("/proofing/test-project/confirm_changes")
    assert "replace" in resp.text


def test_confirm_unauth(client):
    resp = client.get("/proofing/test-project/confirm_changes")
    assert resp.status_code == 302


def test_admin(moderator_client):
    session = q.get_session()

    project = Project(slug="project-123", display_title="Dummy project", board_id=0)
    session.add(project)
    session.commit()

    resp = moderator_client.post(
        "/proofing/project-123/admin",
        data={
            "slug": "project-123",
        },
    )
    # Redirect (to project index page) indicates success.
    assert resp.status_code == 302


def test_admin__slug_mismatch(moderator_client):
    session = q.get_session()

    project = Project(slug="project-1234", display_title="Dummy project", board_id=0)
    session.add(project)
    session.commit()

    # Deletion fails due to a mismatched `slug` value.
    resp = moderator_client.post(
        "/proofing/project-1234/admin",
        data={
            "slug": "project-aoeu",
        },
    )
    assert resp.status_code == 200
    assert "Deletion failed" in resp.text


def test_admin__unauth(client):
    resp = client.get("/proofing/test-project/admin")
    assert resp.status_code == 302


def test_admin__no_admin(rama_client):
    resp = rama_client.get("/proofing/test-project/admin")
    assert resp.status_code == 302


def test_admin__has_moderator_role(moderator_client):
    resp = moderator_client.get("/proofing/test-project/admin")
    assert resp.status_code == 200
    assert "Admin:" in resp.text


def test_admin__has_admin_role(admin_client):
    resp = admin_client.get("/proofing/test-project/admin")
    assert resp.status_code == 200
    assert "Admin:" in resp.text


def test_admin__has_moderator_role__bad_project(admin_client):
    resp = admin_client.get("/proofing/unknown/admin")
    assert resp.status_code == 404


def test_batch_ocr(moderator_client):
    resp = moderator_client.get("/proofing/test-project/batch-ocr")
    assert resp.status_code == 200


def test_batch_ocr__unauth(client):
    resp = client.get("/proofing/test-project/batch-ocr")
    assert resp.status_code == 302


def test_batch_translate_view_engines_list(rama_client):
    from unittest.mock import patch
    with patch("kalanjiyam.views.proofing.project.get_available_translation_engines") as mock_engines:
        mock_engines.return_value = [
            {"value": "test-engine", "label": "Test Dynamic Engine"}
        ]
        resp = rama_client.get("/proofing/test-project/batch-translate")
        assert resp.status_code == 200
        assert "Test Dynamic Engine" in resp.text
        assert 'value="test-engine"' in resp.text


@patch("redis.Redis.from_url")
@patch("kalanjiyam.views.proofing.project.GroupResult")
def test_batch_ocr_status_cancelled(mock_group_result, mock_redis_from_url, rama_client):
    mock_redis = MagicMock()
    mock_redis.scan_iter.return_value = []
    mock_redis_from_url.return_value = mock_redis
    
    mock_group = MagicMock()
    mock_group.results = [MagicMock()]
    mock_group.results[0].state = 'REVOKED'
    mock_group.results[0].failed.return_value = False
    mock_group.completed_count.return_value = 0
    mock_group_result.restore.return_value = mock_group
    
    resp = rama_client.get("/proofing/batch-ocr-status/task-123")
    assert resp.status_code == 200
    assert "OCR Cancelled" in resp.text


@patch("redis.Redis.from_url")
@patch("kalanjiyam.views.proofing.project.GroupResult")
def test_batch_translate_status_cancelled(mock_group_result, mock_redis_from_url, rama_client):
    mock_redis = MagicMock()
    mock_redis.scan_iter.return_value = []
    mock_redis_from_url.return_value = mock_redis
    
    mock_group = MagicMock()
    mock_group.results = [MagicMock()]
    mock_group.results[0].state = 'REVOKED'
    mock_group.results[0].failed.return_value = False
    mock_group.completed_count.return_value = 0
    mock_group_result.restore.return_value = mock_group
    
    resp = rama_client.get("/proofing/batch-translate-status/task-123")
    assert resp.status_code == 200
    assert "Cancelled" in resp.text


@patch("redis.Redis.from_url")
@patch("kalanjiyam.views.proofing.project.GroupResult")
def test_batch_ocr_status_progress_math(mock_group_result, mock_redis_from_url, rama_client):
    mock_redis = MagicMock()
    mock_redis.scan_iter.return_value = []
    mock_redis_from_url.return_value = mock_redis
    
    # 5 out of 10 tasks completed -> 50% progress
    mock_results = [MagicMock() for _ in range(10)]
    for res in mock_results:
        res.state = 'STARTED'
        res.failed.return_value = False
        
    mock_group = MagicMock()
    mock_group.results = mock_results
    mock_group.completed_count.return_value = 5
    mock_group_result.restore.return_value = mock_group
    
    resp = rama_client.get("/proofing/batch-ocr-status/task-123")
    assert resp.status_code == 200
    assert "50%" in resp.text
    assert "width: 50.0%" in resp.text
    assert "AUTO" in resp.text


@patch("redis.Redis.from_url")
@patch("kalanjiyam.views.proofing.project.GroupResult")
def test_batch_ocr_status_default_language_auto(mock_group_result, mock_redis_from_url, rama_client):
    mock_redis = MagicMock()
    mock_redis.scan_iter.return_value = []
    mock_redis_from_url.return_value = mock_redis

    mock_group = MagicMock()
    mock_results = [MagicMock()]
    mock_results[0].state = "STARTED"
    mock_results[0].failed.return_value = False
    mock_group.results = mock_results
    mock_group.completed_count.return_value = 0
    mock_group_result.restore.return_value = mock_group

    resp = rama_client.get("/proofing/batch-ocr-status/task-123")
    assert resp.status_code == 200
    assert "AUTO" in resp.text



