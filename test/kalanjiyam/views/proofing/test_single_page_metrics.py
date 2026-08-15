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


def test_single_page_ocr_route_records_all_metrics(flask_app):
    """Test calling the single-page OCR route and verifying metrics are saved in DB."""
    from kalanjiyam.utils.ocr_types import OcrResponse

    mock_ocr_resp = OcrResponse(
        text_content="Sample chapter content text.",
        bounding_boxes=[(10.0, 10.0, 500.0, 100.0, "Sample chapter content text.")],
        blocks=[
            {
                "id": "b1",
                "type": "paragraph",
                "bbox": [10.0, 10.0, 500.0, 100.0],
                "reading_order": 1,
                "content": "Sample chapter content text.",
                "confidence": 0.942,
                "words": [
                    {"text": "Sample", "bbox": [10.0, 10.0, 100.0, 50.0], "confidence": 0.95},
                    {"text": "chapter", "bbox": [110.0, 10.0, 200.0, 50.0], "confidence": 0.85},
                ]
            }
        ],
        content_format="blocks",
        page_width=1000,
        page_height=1500,
        page_confidence=0.942,
        contract_version="2.1",
        engine="dots_ocr",
        p05=0.85,
        blocks_count=1,
        chars_count=len("Sample chapter content text."),
        engine_latency_ms=342.5,
    )

    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first() or db.Board(name="Test Board 2")
        session.add(board)
        session.flush()

        status = session.query(db.PageStatus).first()
        project = db.Project(
            slug="test-single-ocr-route-book",
            display_title="Test Single OCR Route Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page)
        session.commit()

        # Login as test user (or mock guest/p2)
        with flask_app.test_client() as client:
            # Call the OCR API route
            with patch("kalanjiyam.views.proofing.page.get_page_image_filepath", return_value=None), \
                 patch("kalanjiyam.utils.ocr_runner.run_ocr", return_value=mock_ocr_resp), \
                 patch("kalanjiyam.utils.quotas.ensure_ocr_quota_for_project"), \
                 patch("kalanjiyam.utils.quotas.consume_ocr_credit_for_project"), \
                 patch("kalanjiyam.views.proofing.page.q.user_can_view_proofing_project", return_value=True), \
                 patch("kalanjiyam.views.proofing.decorators.current_user") as dec_user, \
                 patch("kalanjiyam.views.proofing.page.current_user") as mock_user:
                for u in (dec_user, mock_user):
                    u.is_authenticated = True
                    u.is_super_admin = False
                    u.is_org_admin = True
                    u.is_moderator = True
                    u.is_p2 = True
                    u.is_p1 = True
                    u.id = 1
                
                resp = client.get(f"/api/ocr/{project.slug}/{page.slug}/?engine=dots_ocr&language=sa")
                assert resp.status_code == 200

        # Query database to verify metrics were properly saved
        batch_job = session.query(BatchJob).filter_by(
            target_uri=f"single_page_proofing://ocr/{project.slug}"
        ).first()
        assert batch_job is not None
        assert batch_job.status == "COMPLETED"
        assert batch_job.job_type == "SINGLE_PAGE_PROOFING_OCR"

        batch_item = session.query(BatchItem).filter_by(
            job_id=batch_job.id, project_id=project.id
        ).first()
        assert batch_item is not None
        assert batch_item.status == "COMPLETED"
        assert batch_item.engine == "dots_ocr"
        assert batch_item.avg_confidence == 0.942
        assert batch_item.min_confidence == 0.942
        assert batch_item.avg_p05 == 0.85
        assert batch_item.total_blocks == 1
        assert batch_item.total_chars == len("Sample chapter content text.")
        assert batch_item.total_engine_latency_ms == 342.5

        ocr_page = session.query(BatchOcrPage).filter_by(
            batch_item_id=batch_item.id, page_number=1
        ).first()
        assert ocr_page is not None
        assert ocr_page.status == "COMPLETED"
        assert ocr_page.confidence == 0.942
        assert ocr_page.p05 == 0.85
        assert ocr_page.blocks == 1
        assert ocr_page.chars == len("Sample chapter content text.")
        assert ocr_page.engine_latency_ms == 342.5

