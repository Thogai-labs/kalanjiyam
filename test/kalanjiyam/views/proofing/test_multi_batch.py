from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import pytest

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.models.batch import BatchJob, BatchItem
from kalanjiyam.tasks.s3_batch import run_multi_batch_operation


def test_multi_batch_page_access(rama_client):
    """Test that authenticated users can load the 2-window Multi Batch Processes page."""
    resp = rama_client.get("/proofing/multi-batch", follow_redirects=True)
    assert resp.status_code == 200
    assert "Multi Batch Processes" in resp.text
    assert "s3_batch Queue" in resp.text
    assert "Select Documents" in resp.text
    assert "Batch Functions" in resp.text
    assert "v2 contract only" in resp.text
    assert "OCR Engine:" in resp.text
    assert 'value="1"' in resp.text
    assert "Translation Engine:" in resp.text


def test_multi_batch_run_queue_routing(rama_client):
    """Test that starting a multi-batch process routes the Celery task to the s3_batch queue."""
    session = get_session()

    board = session.query(db.Board).first()
    if not board:
        board = db.Board(title="Test Board")
        session.add(board)
        session.flush()

    project = db.Project(
        slug="test-multi-batch-routing-proj",
        display_title="Test Multi Batch Routing Project",
        board_id=board.id,
    )
    session.add(project)
    session.commit()

    try:
        with patch("kalanjiyam.tasks.s3_batch.run_multi_batch_operation.apply_async") as mock_apply_async:
            mock_apply_async.return_value = MagicMock(id="mock-multi-batch-task-id")

            payload = {
                "project_ids": [project.id],
                "do_ocr": True,
                "ocr_engine": "gemma_ocr",
                "ocr_language": "auto",
                "do_translate": True,
                "trans_engine": "indictrans3",
                "source_lang": "sa",
                "target_lang": "en",
            }
            resp = rama_client.post(
                "/proofing/multi-batch/run",
                json=payload,
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["success"] is True
            assert data["task_id"] == "mock-multi-batch-task-id"

            mock_apply_async.assert_called_once()
            _, kwargs = mock_apply_async.call_args
            # Verify strictly placed on s3_batch queue as requested
            assert kwargs.get("queue") == "s3_batch"

    finally:
        session.query(BatchItem).filter_by(project_id=project.id).delete()
        session.delete(project)
        session.commit()


def test_multi_batch_skip_already_ocred_and_translated(flask_app):
    """Test that pages and books already OCRed / translated are skipped as requested."""
    session = get_session()

    board = session.query(db.Board).first()
    if not board:
        board = db.Board(title="Test Board")
        session.add(board)
        session.flush()

    status = session.query(db.PageStatus).first()
    user = session.query(db.User).first()

    # Create project with 2 pages:
    # Page 1: Already OCRed (has ocr_bounding_boxes) and already translated
    # Page 2: Not OCRed and not translated
    project = db.Project(
        slug="test-skip-multi-batch-project",
        display_title="Test Skip Multi Batch Project",
        board_id=board.id,
    )
    session.add(project)
    session.flush()

    page1 = db.Page(
        project_id=project.id,
        slug="1",
        order=1,
        status_id=status.id,
        ocr_bounding_boxes="0 0 100 100 Already OCRed Text",
    )
    page2 = db.Page(
        project_id=project.id,
        slug="2",
        order=2,
        status_id=status.id,
        ocr_bounding_boxes=None,
    )
    session.add_all([page1, page2])
    session.flush()

    rev1 = db.Revision(
        project_id=project.id,
        page_id=page1.id,
        content="Already OCRed Text",
        author_id=user.id if user else None,
        status_id=status.id,
    )
    session.add(rev1)
    session.flush()

    # Add translation on page 1
    trans1 = db.Translation(
        page_id=page1.id,
        revision_id=rev1.id,
        author_id=user.id if user else None,
        content="Already Translated Text",
        source_language="sa",
        target_language="en",
        translation_engine="indictrans3",
        status="completed",
    )
    session.add(trans1)

    batch_job = BatchJob(
        target_uri="ui://multi-batch",
        status="IN_PROGRESS",
        job_type="MULTI_BATCH",
    )
    session.add(batch_job)
    session.flush()

    batch_item = BatchItem(
        job_id=batch_job.id,
        file_path=project.slug,
        project_id=project.id,
        status="PENDING",
        total_pages=2,
    )
    session.add(batch_item)
    session.commit()

    try:
        with patch("kalanjiyam.tasks.ocr._run_ocr_for_page_inner") as mock_run_ocr, \
             patch("kalanjiyam.tasks.translation._run_translation_for_page_inner") as mock_run_trans, \
             patch("redis.Redis.from_url") as mock_redis:

            mock_redis.return_value.get.return_value = None
            mock_redis.return_value.setex.return_value = True

            # Run the task directly
            run_multi_batch_operation(
                batch_job_id=batch_job.id,
                project_ids=[project.id],
                do_ocr=True,
                ocr_engine="gemma_ocr",
                ocr_language="auto",
                do_translate=True,
                trans_engine="indictrans3",
                source_lang="sa",
                target_lang="en",
            )

            # Page 1 already had OCR, so mock_run_ocr should ONLY be called for page 2!
            assert mock_run_ocr.call_count == 1
            ocr_call_kwargs = mock_run_ocr.call_args[1]
            assert ocr_call_kwargs["page_slug"] == "2"

            # Page 1 already had Translation, so mock_run_trans should ONLY be called for page 2!
            assert mock_run_trans.call_count == 1
            trans_call_kwargs = mock_run_trans.call_args[1]
            assert trans_call_kwargs["page_slug"] == "2"

    finally:
        session.delete(trans1)
        session.delete(rev1)
        session.delete(page2)
        session.delete(page1)
        session.delete(batch_item)
        session.delete(batch_job)
        session.delete(project)
        session.commit()


def test_multi_batch_multiple_documents_and_error_metrics(rama_client):
    """Test that multi-batch processes multiple documents without DetachedInstanceError and logs error metrics."""
    session = get_session()

    board = session.query(db.Board).first()
    if not board:
        board = db.Board(title="Test Board")
        session.add(board)
        session.flush()

    status = session.query(db.PageStatus).first()

    # Create 2 test projects
    proj1 = db.Project(
        slug="test-multi-doc-1",
        display_title="Test Multi Doc 1",
        board_id=board.id,
    )
    proj2 = db.Project(
        slug="test-multi-doc-2",
        display_title="Test Multi Doc 2",
        board_id=board.id,
    )
    session.add_all([proj1, proj2])
    session.flush()

    pg1 = db.Page(project_id=proj1.id, slug="1", order=1, status_id=status.id)
    pg2 = db.Page(project_id=proj2.id, slug="1", order=1, status_id=status.id)
    session.add_all([pg1, pg2])
    session.flush()

    batch_job = BatchJob(
        target_uri="ui://multi-batch",
        status="IN_PROGRESS",
        job_type="MULTI_BATCH",
    )
    session.add(batch_job)
    session.flush()

    item1 = BatchItem(job_id=batch_job.id, file_path=proj1.slug, project_id=proj1.id, status="PENDING", total_pages=1)
    item2 = BatchItem(job_id=batch_job.id, file_path=proj2.slug, project_id=proj2.id, status="PENDING", total_pages=1)
    session.add_all([item1, item2])
    session.commit()

    try:
        with patch("kalanjiyam.tasks.ocr._run_ocr_for_page_inner") as mock_ocr, \
             patch("kalanjiyam.utils.metrics.record_metric") as mock_record_metric, \
             patch("redis.Redis.from_url") as mock_redis:

            mock_redis.return_value.get.return_value = None
            mock_redis.return_value.setex.return_value = True

            # First project OCR fails with an exception, second succeeds
            mock_ocr.side_effect = [RuntimeError("OCR Service Timeout"), 123]

            run_multi_batch_operation(
                batch_job_id=batch_job.id,
                project_ids=[proj1.id, proj2.id],
                do_ocr=True,
                ocr_engine="gemma_ocr",
                ocr_language="auto",
                do_translate=False,
            )

            # Both documents should have been processed (mock_ocr called twice)!
            assert mock_ocr.call_count == 2

            # The error on doc 1 must have been recorded in metrics
            error_metrics = [
                call for call in mock_record_metric.call_args_list
                if call[1].get("category") == "error"
            ]
            assert len(error_metrics) >= 1
            assert "OCR Service Timeout" in error_metrics[0][1]["error_message"]

    finally:
        session.delete(pg2)
        session.delete(pg1)
        session.delete(item2)
        session.delete(item1)
        session.delete(batch_job)
        session.delete(proj2)
        session.delete(proj1)
        session.commit()


class InMemoryRedis:
    def __init__(self):
        self.store = {}
        self.hashes = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, *args, **kwargs):
        self.store[key] = value
        return True

    def setex(self, key, time, value):
        self.store[key] = value
        return True

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return True

    def exists(self, key):
        return key in self.store

    def keys(self, pattern="*"):
        prefix = pattern.replace("*", "")
        return [k for k in self.store if k.startswith(prefix)]

    def hget(self, name, key):
        return self.hashes.get(name, {}).get(key)

    def hset(self, name, key=None, value=None, mapping=None):
        if name not in self.hashes:
            self.hashes[name] = {}
        if mapping:
            self.hashes[name].update(mapping)
        if key is not None:
            self.hashes[name][key] = value
        return True

    def hgetall(self, name):
        return self.hashes.get(name, {})

    def hdel(self, name, *keys):
        for k in keys:
            self.hashes.get(name, {}).pop(k, None)
        return True

    def expire(self, key, time):
        return True


def test_multi_batch_task_status_page_by_task_id(rama_client):
    """Test that visiting /proofing/multi-batch/<task_id> shows the dedicated status page."""
    import json
    session = get_session()

    board = session.query(db.Board).first()
    if not board:
        board = db.Board(title="Test Board")
        session.add(board)
        session.flush()

    project = db.Project(
        slug="test-task-status-proj",
        display_title="Test Task Status Project",
        board_id=board.id,
    )
    session.add(project)
    session.flush()

    batch_job = BatchJob(
        target_uri="ui://multi-batch",
        status="IN_PROGRESS",
        job_type="MULTI_BATCH",
    )
    session.add(batch_job)
    session.flush()

    batch_item = BatchItem(
        job_id=batch_job.id,
        file_path="Test Task Status Project (test-task-status-proj)",
        project_id=project.id,
        status="IN_PROGRESS",
        total_pages=5,
    )
    session.add(batch_item)
    session.commit()

    task_id = "test-celery-multi-task-uuid"
    fake_redis = InMemoryRedis()

    fake_redis.setex(f"multi_batch_task:{task_id}", 3600, str(batch_job.id))
    mock_state = {
        "job_id": batch_job.id,
        "task_id": task_id,
        "status": "IN_PROGRESS",
        "total_projects": 1,
        "completed_projects": 0,
        "total_pages": 5,
        "processed_pages": 2,
        "skipped_ocr_pages": 1,
        "skipped_trans_pages": 0,
        "skipped_ocr_books": 0,
        "skipped_trans_books": 0,
        "error_count": 0,
        "percent": 40,
        "started_at": "2026-10-19T22:00:00",
        "completed_at": None,
        "projects": [
            {
                "id": project.id,
                "slug": project.slug,
                "title": project.display_title,
                "total_pages": 5,
                "status": "IN_PROGRESS",
                "ocr_status": "IN_PROGRESS",
                "ocr_message": "",
                "trans_status": "PENDING",
                "trans_message": "",
                "message": "Processing page 3 of 5",
            }
        ],
        "logs": ["Job queued", "Processing started for test-task-status-proj"],
    }
    fake_redis.setex(f"multi_batch_job:{batch_job.id}", 3600, json.dumps(mock_state))

    try:
        with patch("kalanjiyam.views.proofing.multi_batch._get_redis_client", return_value=fake_redis):
            # Test GET /proofing/multi-batch/<task_id>
            resp = rama_client.get(f"/proofing/multi-batch/{task_id}", follow_redirects=True)
            assert resp.status_code == 200
            assert "Multi Batch Process Status" in resp.text
            assert task_id in resp.text
            assert f"#{batch_job.id}" in resp.text
            assert "Test Task Status Project" in resp.text
            assert "s3_batch Queue" in resp.text

            # Test GET /proofing/multi-batch/<job_id>
            resp_job = rama_client.get(f"/proofing/multi-batch/{batch_job.id}", follow_redirects=True)
            assert resp_job.status_code == 200
            assert "Multi Batch Process Status" in resp_job.text

            # Test GET /proofing/multi-batch/status/<task_id>
            api_resp = rama_client.get(f"/proofing/multi-batch/status/{task_id}")
            assert api_resp.status_code == 200
            data = api_resp.get_json()
            assert data["success"] is True
            assert data["data"]["job_id"] == batch_job.id
            assert data["data"]["percent"] == 40
            assert data["data"]["processed_pages"] == 2

    finally:
        session.delete(batch_item)
        session.delete(batch_job)
        session.delete(project)
        session.commit()


def test_multi_batch_task_status_page_not_found(rama_client):
    """Test that requesting an unknown task ID returns 404."""
    resp = rama_client.get("/proofing/multi-batch/nonexistent-task-id-999999")
    assert resp.status_code == 404


def test_multi_batch_user_tasks_progress_and_cancel(rama_client):
    """Test user_tasks integration: progress reading and task cancellation for multi_batch."""
    import json
    from kalanjiyam.utils.user_tasks import add_user_task, get_user_tasks, cancel_user_task

    session = get_session()
    user = session.query(db.User).first()
    assert user is not None

    batch_job = BatchJob(
        target_uri="ui://multi-batch",
        status="IN_PROGRESS",
        job_type="MULTI_BATCH",
    )
    session.add(batch_job)
    session.flush()

    task_id = "test-user-task-multi-batch-id"
    user_identifier = f"user:{user.id}"
    fake_redis = InMemoryRedis()

    mock_state = {
        "job_id": batch_job.id,
        "status": "IN_PROGRESS",
        "percent": 75,
        "completed_projects": 3,
        "total_projects": 4,
        "logs": [],
    }
    fake_redis.setex(f"multi_batch_job:{batch_job.id}", 3600, json.dumps(mock_state))

    try:
        with patch("kalanjiyam.utils.user_tasks.redis_client", fake_redis):
            # Add task
            add_user_task(
                user_identifier=user_identifier,
                task_id=task_id,
                task_type="multi_batch",
                project_slug="multi-batch",
                project_title="Multi Batch (2 documents)",
                extra_info={"job_id": batch_job.id},
            )

            # Query user tasks
            tasks = get_user_tasks(user_identifier)
            matching = [t for t in tasks if t["task_id"] == task_id]
            assert len(matching) == 1
            t_info = matching[0]
            assert t_info["status"] == "running"
            assert t_info["progress"] == 0.75
            assert t_info["completed_count"] == 3
            assert t_info["total_count"] == 4

            # Cancel the task
            with patch("celery.result.AsyncResult.revoke"):
                cancelled = cancel_user_task(user_identifier, task_id)
                assert cancelled is True

            # Check that Redis state and DB status updated to CANCELLED
            updated_job = session.query(BatchJob).get(batch_job.id)
            assert updated_job.status == "CANCELLED"

    finally:
        session.delete(batch_job)
        session.commit()


def test_header_renders_multi_batch_task_url(rama_client):
    """Test that the navbar / header renders task status URL template for multi-batch tasks."""
    resp = rama_client.get("/proofing/")
    assert resp.status_code == 200
    assert "/proofing/multi-batch/" in resp.text
    assert "task.type === 'multi_batch'" in resp.text


