import json
import pytest
from unittest.mock import MagicMock, patch
from kalanjiyam.utils.user_tasks import get_user_identifier, add_user_task, get_user_tasks


def test_get_user_identifier_auth(flask_app):
    with flask_app.test_request_context():
        user = MagicMock()
        user.is_authenticated = True
        user.id = 42
        assert get_user_identifier(user) == "user:42"


def test_get_user_identifier_guest(flask_app):
    with flask_app.test_request_context(headers={"Cookie": "device_fingerprint=abc-123"}):
        user = MagicMock()
        user.is_authenticated = False
        from flask import request
        assert get_user_identifier(user, request) == "guest:abc-123"


def test_get_user_identifier_none(flask_app):
    with flask_app.test_request_context():
        user = MagicMock()
        user.is_authenticated = False
        from flask import request
        assert get_user_identifier(user, request) is None


@patch('kalanjiyam.utils.user_tasks.redis_client')
def test_add_user_task(mock_redis):
    mock_redis.hset.return_value = 1
    mock_redis.expire.return_value = True
    
    res = add_user_task(
        user_identifier="user:42",
        task_id="task-123",
        task_type="ocr",
        project_slug="my-book",
        project_title="My Book Title",
        extra_info={"engine": "google"}
    )
    
    assert res is True
    mock_redis.hset.assert_called_once()
    mock_redis.expire.assert_called_once()


@patch('kalanjiyam.utils.user_tasks.redis_client')
@patch('kalanjiyam.utils.user_tasks.GroupResult')
def test_get_user_tasks(mock_group_result, mock_redis):
    from datetime import datetime
    now_str = datetime.utcnow().isoformat()
    # Setup mock data for hgetall
    mock_redis.hgetall.return_value = {
        b"task-123": f'{{"task_id": "task-123", "type": "ocr", "project_slug": "my-book", "project_title": "My Book Title", "status": "running", "started_at": "{now_str}"}}'.encode('utf-8')
    }
    
    # Mock GroupResult
    mock_group = MagicMock()
    mock_group.results = [MagicMock(), MagicMock()]
    mock_group.completed_count.return_value = 1
    mock_group.ready.return_value = False
    mock_group_result.restore.return_value = mock_group
    
    tasks = get_user_tasks("user:42")
    
    assert len(tasks) == 1
    assert tasks[0]['task_id'] == "task-123"
    assert tasks[0]['status'] == "running"
    assert tasks[0]['progress'] == 0.5
    assert tasks[0]['completed_count'] == 1
    assert tasks[0]['total_count'] == 2


def test_get_tasks_api_unauth(client):
    resp = client.get("/proofing/api/tasks")
    assert resp.status_code == 200
    assert resp.json == {"tasks": []}


@patch('kalanjiyam.utils.user_tasks.get_user_tasks')
def test_get_tasks_api_auth(mock_get_user_tasks, rama_client):
    mock_get_user_tasks.return_value = [
        {"task_id": "task-123", "type": "ocr", "status": "completed"}
    ]
    resp = rama_client.get("/proofing/api/tasks")
    assert resp.status_code == 200
    assert resp.json == {"tasks": [{"task_id": "task-123", "type": "ocr", "status": "completed"}]}


@patch('kalanjiyam.utils.user_tasks.redis_client')
@patch('kalanjiyam.utils.user_tasks.GroupResult')
def test_get_user_tasks_decoded(mock_group_result, mock_redis):
    from datetime import datetime
    now_str = datetime.utcnow().isoformat()
    # Setup mock data for hgetall using strings instead of bytes
    mock_redis.hgetall.return_value = {
        "task-123": f'{{"task_id": "task-123", "type": "ocr", "project_slug": "my-book", "project_title": "My Book Title", "status": "running", "started_at": "{now_str}"}}'
    }
    
    mock_group = MagicMock()
    mock_group.results = [MagicMock(), MagicMock()]
    mock_group.completed_count.return_value = 1
    mock_group.ready.return_value = False
    mock_group_result.restore.return_value = mock_group
    
    tasks = get_user_tasks("user:42")
    
    assert len(tasks) == 1
    assert tasks[0]['task_id'] == "task-123"
    assert tasks[0]['status'] == "running"
    assert tasks[0]['progress'] == 0.5


@patch('kalanjiyam.utils.user_tasks.redis_client')
@patch('kalanjiyam.utils.user_tasks.GroupResult')
def test_get_user_tasks_sorting(mock_group_result, mock_redis):
    # Setup mock data with two tasks having different start times
    mock_redis.hgetall.return_value = {
        "task-1": '{"task_id": "task-1", "type": "ocr", "project_slug": "my-book", "project_title": "My Book Title", "status": "completed", "started_at": "2026-07-09T10:00:00"}',
        "task-2": '{"task_id": "task-2", "type": "ocr", "project_slug": "my-book", "project_title": "My Book Title", "status": "completed", "started_at": "2026-07-09T10:30:00"}'
    }
    
    mock_group_result.restore.return_value = None
    
    tasks = get_user_tasks("user:42")
    
    assert len(tasks) == 2
    # The newer task (started_at 10:30:00) should be first
    assert tasks[0]['task_id'] == "task-2"
    assert tasks[1]['task_id'] == "task-1"


@patch('kalanjiyam.utils.user_tasks.redis_client')
@patch('kalanjiyam.utils.user_tasks.GroupResult')
def test_get_user_tasks_stale_or_ready(mock_group_result, mock_redis):
    # Setup mock data for a stale task (older than 1 hour, status 'running')
    mock_redis.hgetall.return_value = {
        "task-stale": '{"task_id": "task-stale", "type": "ocr", "project_slug": "my-book", "project_title": "My Book Title", "status": "running", "started_at": "2020-01-01T10:00:00"}'
    }
    
    mock_group = MagicMock()
    mock_group.results = [MagicMock(), MagicMock()]
    mock_group.completed_count.return_value = 1
    mock_group.ready.return_value = False
    mock_group_result.restore.return_value = mock_group
    
    tasks = get_user_tasks("user:42")
    
    assert len(tasks) == 1
    # Since it is older than 1 hour (started in 2020), it should be marked as completed (since current > 0)
    assert tasks[0]['status'] == "completed"
    assert tasks[0]['progress'] == 1.0



