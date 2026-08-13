from unittest.mock import patch
import pytest

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.tasks.translation import run_translation_for_project

def test_run_translation_for_project_queue_routing(flask_app):
    session = get_session()
    
    # 1. Setup a dummy project and a page with revisions so we trigger the task group creation
    board = session.query(db.Board).first()
    if not board:
        board = db.Board(name="Test Board")
        session.add(board)
        session.flush()

    status = session.query(db.PageStatus).first()
    user = session.query(db.User).first()

    project = db.Project(
        slug="test-translate-queue-project",
        display_title="Test Translate Queue Project",
        board_id=board.id,
    )
    session.add(project)
    session.flush()
    
    page = db.Page(
        project_id=project.id,
        slug="1",
        order=1,
        status_id=status.id,
    )
    session.add(page)
    session.flush()
    
    revision = db.Revision(
        project_id=project.id,
        page_id=page.id,
        content="Test translation content",
        author_id=user.id if user else None,
        status_id=status.id,
    )
    session.add(revision)
    session.commit()

    try:
        # Patch Celery group's apply_async to intercept the queue argument
        with patch("celery.group.apply_async") as mock_apply_async:
            # Run without queue
            run_translation_for_project(
                app_env=flask_app.config["KALANJIYAM_ENVIRONMENT"],
                project=project,
                source_lang="sa",
                target_lang="en",
                engine="google",
            )
            mock_apply_async.assert_called_once()
            # It should not have been called with queue parameter (or queue=None)
            args, kwargs = mock_apply_async.call_args
            assert "queue" not in kwargs or kwargs["queue"] is None

            mock_apply_async.reset_mock()

            # Run with a specific queue
            run_translation_for_project(
                app_env=flask_app.config["KALANJIYAM_ENVIRONMENT"],
                project=project,
                source_lang="sa",
                target_lang="en",
                engine="google",
                queue="low_priority",
            )
            mock_apply_async.assert_called_once()
            args, kwargs = mock_apply_async.call_args
            assert kwargs.get("queue") == "low_priority"

    finally:
        # Cleanup database entries
        session.delete(revision)
        session.delete(page)
        session.delete(project)
        session.commit()

def test_batch_translate_view_queue_routing(rama_client, client):
    # This tests the view logic in project.py to ensure queue routing is correctly selected
    session = get_session()
    project = session.query(db.Project).filter_by(slug="test-project").first()

    with patch("kalanjiyam.tasks.translation.run_translation_for_project") as mock_run_translation, \
         patch("kalanjiyam.views.proofing.project.redis_client") as mock_redis:
        mock_run_translation.return_value = None  # Mock to avoid running actual task
        mock_redis.get.return_value = None
        
        # Test as authenticated user (rama_client)
        r = rama_client.post(
            "/proofing/test-project/batch-translate",
            data={
                "source_lang": "sa",
                "target_lang": "en",
                "engine": "indictrans2"
            }
        )
        # Check that it called run_translation_for_project with queue=None
        mock_run_translation.assert_called_once()
        args, kwargs = mock_run_translation.call_args
        assert kwargs.get("queue") is None

        mock_run_translation.reset_mock()

        try:
            # Update the project's fingerprint to allow unauthenticated access (guest)
            project.fingerprint_id = "test-fingerprint"
            session.add(project)
            session.commit()

            # Set cookies on unauthenticated client to match fingerprint
            client.set_cookie("device_fingerprint", "test-fingerprint")

            r_guest = client.post(
                "/proofing/test-project/batch-translate",
                data={
                    "source_lang": "sa",
                    "target_lang": "en",
                    "engine": "indictrans2"
                }
            )
            # Check that it called run_translation_for_project with queue='low_priority'
            mock_run_translation.assert_called_once()
            args, kwargs = mock_run_translation.call_args
            assert kwargs.get("queue") == "low_priority"
        finally:
            project.fingerprint_id = None
            session.add(project)
            session.commit()
