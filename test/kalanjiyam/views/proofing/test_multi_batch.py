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
