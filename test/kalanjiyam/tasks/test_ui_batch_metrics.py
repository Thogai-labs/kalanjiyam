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


def test_ocr_same_engine_skips_and_new_engine_runs(flask_app, tmp_path):
    from kalanjiyam import consts
    from kalanjiyam.tasks.ocr import _run_ocr_for_page_inner
    from kalanjiyam.utils.ocr_types import OcrResponse

    with flask_app.app_context():
        session = q.get_session()
        bot_user = q.user(consts.BOT_USERNAME)
        if not bot_user:
            bot_user = db.User(username=consts.BOT_USERNAME, email="bot@test.local")
            session.add(bot_user)
            session.flush()

        board = session.query(db.Board).first()
        if not board:
            board = db.Board(title="Test Board Skip")
            session.add(board)
            session.flush()

        status = session.query(db.PageStatus).first()

        project = db.Project(
            slug="test-ocr-skip-project",
            display_title="Test OCR Skip Project",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page1 = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page1)
        session.flush()

        # Create batch job and batch item
        job = BatchJob(
            target_uri=f"ui://project/{project.slug}",
            status="IN_PROGRESS",
            job_type="UI_BATCH_OCR",
        )
        session.add(job)
        session.flush()

        item = BatchItem(
            job_id=job.id,
            project_id=project.id,
            file_path="test.pdf",
            status="IN_PROGRESS",
            total_pages=1,
        )
        session.add(item)
        session.flush()

        ocr_p = BatchOcrPage(
            batch_item_id=item.id,
            page_number=1,
            status="PENDING",
        )
        session.add(ocr_p)
        session.commit()

        mock_resp_google = OcrResponse(
            text_content="Google OCR Result Page 1",
            bounding_boxes=[(0, 0, 100, 100, "Google OCR Result Page 1")],
            engine="google",
        )
        mock_resp_surya = OcrResponse(
            text_content="Surya OCR Result Page 1",
            bounding_boxes=[(0, 0, 100, 100, "Surya OCR Result Page 1")],
            engine="surya",
        )

        dummy_img = tmp_path / "1.png"
        dummy_img.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.tasks.ocr.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.tasks.ocr.consume_ocr_credit_for_project") as mock_consume_credit, \
             patch("kalanjiyam.utils.ocr_runner.run_ocr", return_value=mock_resp_google) as mock_run_ocr:

            # 1. First run with Google engine: runs OCR and consumes quota
            rev1_id = _run_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug="1",
                engine="google",
            )
            assert rev1_id is not None
            assert mock_run_ocr.call_count == 1
            assert mock_consume_credit.call_count == 1

            # Check PageVersion and Revision
            pv_google = session.query(db.PageVersion).filter_by(page_id=page1.id, version_key="ocr:google").first()
            assert pv_google is not None
            assert pv_google.version == 1

            rev1 = session.query(db.Revision).filter_by(page_id=page1.id, page_version_id=pv_google.id).first()
            assert rev1 is not None
            assert rev1.content == "Google OCR Result Page 1"
            assert rev1.author_id == bot_user.id

        # 2. Second run with SAME engine (Google): skips OCR API and quota
        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.tasks.ocr.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.tasks.ocr.consume_ocr_credit_for_project") as mock_consume_credit, \
             patch("kalanjiyam.utils.ocr_runner.run_ocr") as mock_run_ocr:

            rev2_id = _run_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug="1",
                engine="google",
            )
            # Returns existing revision version/ID without calling OCR API
            assert rev2_id is not None
            mock_run_ocr.assert_not_called()
            mock_consume_credit.assert_not_called()

        # 3. Third run with NEW engine (Surya): runs OCR API and creates new version track
        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.tasks.ocr.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.tasks.ocr.consume_ocr_credit_for_project") as mock_consume_credit, \
             patch("kalanjiyam.utils.ocr_runner.run_ocr", return_value=mock_resp_surya) as mock_run_ocr:

            rev3_id = _run_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug="1",
                engine="surya",
            )
            assert rev3_id is not None
            assert mock_run_ocr.call_count == 1
            assert mock_consume_credit.call_count == 1

            pv_surya = session.query(db.PageVersion).filter_by(page_id=page1.id, version_key="ocr:surya").first()
            assert pv_surya is not None
            assert pv_surya.version == 1

            rev3 = session.query(db.Revision).filter_by(page_id=page1.id, page_version_id=pv_surya.id).first()
            assert rev3 is not None
            assert rev3.content == "Surya OCR Result Page 1"
            assert rev3.author_id == bot_user.id

        # 4. Fourth run with force=True on Google: re-runs OCR API
        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.tasks.ocr.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.tasks.ocr.consume_ocr_credit_for_project") as mock_consume_credit, \
             patch("kalanjiyam.utils.ocr_runner.run_ocr", return_value=mock_resp_google) as mock_run_ocr:

            rev4_id = _run_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug="1",
                engine="google",
                force=True,
            )
            assert rev4_id is not None
            assert mock_run_ocr.call_count == 1
            assert mock_consume_credit.call_count == 1


def test_enhanced_ocr_same_engine_profile_skips(flask_app, tmp_path):
    from kalanjiyam import consts
    from kalanjiyam.tasks.ocr import _run_enhanced_ocr_for_page_inner
    from kalanjiyam.utils.ocr_types import OcrResponse

    with flask_app.app_context():
        session = q.get_session()
        bot_user = q.user(consts.BOT_USERNAME)
        if not bot_user:
            bot_user = db.User(username=consts.BOT_USERNAME, email="bot@test.local")
            session.add(bot_user)
            session.flush()

        board = session.query(db.Board).first()
        if not board:
            board = db.Board(title="Test Board Enhanced")
            session.add(board)
            session.flush()

        status = session.query(db.PageStatus).first()

        project = db.Project(
            slug="test-enhanced-ocr-skip-proj",
            display_title="Test Enhanced OCR Skip Proj",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page1 = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page1)
        session.commit()

        mock_resp_dots = OcrResponse(
            text_content="Dots OCR Enhanced Result Page 1",
            bounding_boxes=[(0, 0, 100, 100, "Dots OCR Enhanced Result Page 1")],
            engine="dots_ocr",
            ocr_mode="enhanced",
            enhancement_profile="document_cleanup",
        )

        dummy_img = tmp_path / "1.png"
        dummy_img.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.tasks.ocr.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.tasks.ocr.consume_ocr_credit_for_project") as mock_consume_credit, \
             patch("kalanjiyam.utils.enhanced_ocr.run_enhanced_ocr", return_value=mock_resp_dots) as mock_run_enh:

            # 1. First run with dots_ocr & document_cleanup
            res1 = _run_enhanced_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug="1",
                engine="dots_ocr",
                profile="document_cleanup",
            )
            assert res1 is not None
            assert mock_run_enh.call_count == 1
            assert mock_consume_credit.call_count == 1

            pv = session.query(db.PageVersion).filter_by(page_id=page1.id, version_key="ocr:enhanced:dots_ocr:document_cleanup").first()
            assert pv is not None

        # 2. Second run with same engine & profile: should skip run_enhanced_ocr
        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.tasks.ocr.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.tasks.ocr.consume_ocr_credit_for_project") as mock_consume_credit, \
             patch("kalanjiyam.utils.enhanced_ocr.run_enhanced_ocr") as mock_run_enh:

            res2 = _run_enhanced_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug="1",
                engine="dots_ocr",
                profile="document_cleanup",
            )
            assert res2 is not None
            mock_run_enh.assert_not_called()
            mock_consume_credit.assert_not_called()


