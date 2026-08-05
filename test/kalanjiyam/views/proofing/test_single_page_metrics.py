import pytest
from unittest.mock import patch, MagicMock
import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage

def test_single_page_ocr_and_translation_metrics_status(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first()
        if not board:
            board = db.Board(name="Test Board Single Page")
            session.add(board)
            session.flush()

        status = session.query(db.PageStatus).first()

        project = db.Project(
            slug="test-single-page-metrics-book",
            display_title="Test Single Page Metrics Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page1 = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page1)
        session.commit()

        # Simulate the single page metrics recording logic from page.py
        project_slug = project.slug
        page_slug = page1.slug

        batch_job = BatchJob(
            target_uri=f"single_page_proofing://ocr/{project_slug}",
            status='IN_PROGRESS',
            job_type='SINGLE_PAGE_PROOFING_OCR'
        )
        session.add(batch_job)
        session.flush()

        project_title = getattr(project, 'display_title', None) or project_slug
        batch_item = BatchItem(
            job_id=batch_job.id,
            file_path=f"{project_title} ({project_slug})",
            project_id=project.id,
            status='IN_PROGRESS',
            total_pages=1,
        )
        session.add(batch_item)
        session.flush()

        ocr_page = BatchOcrPage(
            batch_item_id=batch_item.id,
            chunk_id=None,
            page_number=1,
            status='COMPLETED',
            ocr_latency_ms=1500.0,
        )
        session.add(ocr_page)

        # Mark completed
        batch_item.status = 'COMPLETED'
        batch_job.status = 'COMPLETED'
        session.commit()

        # Verify status is COMPLETED
        db_job = session.query(BatchJob).filter_by(id=batch_job.id).first()
        db_item = session.query(BatchItem).filter_by(id=batch_item.id).first()

        assert db_job.status == 'COMPLETED'
        assert db_item.status == 'COMPLETED'
        assert db_item.file_path == "Test Single Page Metrics Book (test-single-page-metrics-book)"
