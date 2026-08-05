import pytest
from unittest.mock import patch, MagicMock
import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
from kalanjiyam.tasks.ocr import run_ocr_for_project
from kalanjiyam.tasks.translation import run_translation_for_project

def test_run_ocr_for_project_creates_batch_job_and_items(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first()
        if not board:
            board = db.Board(name="Test Board OCR")
            session.add(board)
            session.flush()

        status = session.query(db.PageStatus).first()

        project = db.Project(
            slug="test-ocr-metrics-book",
            display_title="Test OCR Metrics Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page1 = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        page2 = db.Page(project_id=project.id, order=2, slug="2", status_id=status.id)
        session.add_all([page1, page2])
        session.commit()

        with patch("kalanjiyam.tasks.ocr.group") as mock_group:
            mock_group_result = MagicMock()
            mock_group.return_value = mock_group_result
            mock_group_result.apply_async.return_value = mock_group_result

            res = run_ocr_for_project("testing", project)
            assert res is not None

        # Verify BatchJob created
        job = session.query(BatchJob).filter_by(target_uri=f"ui://project/{project.slug}").first()
        assert job is not None
        assert job.job_type == "UI_BATCH_OCR"
        assert job.status == "IN_PROGRESS"

        # Verify BatchItem created
        item = session.query(BatchItem).filter_by(job_id=job.id).first()
        assert item is not None
        assert item.project_id == project.id
        assert item.total_pages == 2

        # Verify BatchOcrPage created
        pages = session.query(BatchOcrPage).filter_by(batch_item_id=item.id).all()
        assert len(pages) == 2


def test_run_translation_for_project_creates_batch_job_and_items(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first()
        if not board:
            board = db.Board(name="Test Board Trans")
            session.add(board)
            session.flush()

        status = session.query(db.PageStatus).first()
        user = session.query(db.User).first()

        project = db.Project(
            slug="test-trans-metrics-book",
            display_title="Test Trans Metrics Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page1 = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page1)
        session.flush()

        rev1 = db.Revision(
            page_id=page1.id,
            project_id=project.id,
            status_id=status.id,
            content="Some text to translate",
            summary="Initial text",
            author_id=user.id if user else None,
        )
        session.add(rev1)
        session.commit()

        with patch("kalanjiyam.tasks.translation.group") as mock_group:
            mock_group_result = MagicMock()
            mock_group.return_value = mock_group_result
            mock_group_result.apply_async.return_value = mock_group_result

            res = run_translation_for_project(
                app_env="testing",
                project=project,
                source_lang="sa",
                target_lang="en",
            )
            assert res is not None

        # Verify BatchJob created
        job = session.query(BatchJob).filter_by(target_uri=f"ui://translation/{project.slug}").first()
        assert job is not None
        assert job.job_type == "UI_BATCH_TRANSLATION"
        assert job.status == "IN_PROGRESS"

        # Verify BatchItem created
        item = session.query(BatchItem).filter_by(job_id=job.id).first()
        assert item is not None
        assert item.project_id == project.id
        assert item.total_pages == 1
        assert item.source_lang == "sa"
        assert item.target_lang == "en"

        # Verify BatchOcrPage created
        pages = session.query(BatchOcrPage).filter_by(batch_item_id=item.id).all()
        assert len(pages) == 1
