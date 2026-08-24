"""Comprehensive tests for Enhanced OCR pipeline.

Covers:
1. Normal OCR remains unchanged.
2. Enhanced OCR can run with Gemma.
3. Enhanced OCR can run with Dots.
4. Each supported preprocessing profile works (clahe_1, background_clahe, sharpen, normal).
5. Invalid engine is rejected.
6. Invalid enhancement profile is rejected.
7. Enhanced result is marked as enhanced (ocr_mode='enhanced', enhancement_version='1.0').
8. Engine and preprocessing metadata are stored.
9. Enhanced result does not overwrite normal OCR.
10. JSON is correctly gzip-compressed and can be read back.
11. Page dimensions / coordinate space remain correct.
12. Different engine + preprocessing combinations produce distinguishable versions/results.
"""

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from kalanjiyam.utils.document_storage import derive_revision_tag
from kalanjiyam.utils.image_preprocessing import (
    preprocess_image,
    preprocess_image_to_tempfile,
    validate_enhancement_profile,
)
from kalanjiyam.utils.ocr_persist import ocr_response_to_api_dict
from kalanjiyam.utils.ocr_runner import run_enhanced_ocr, run_ocr
from kalanjiyam.utils.ocr_types import OcrResponse
from kalanjiyam.utils.storage import MemoryStorage, page_enhanced_ocr_key, page_ocr_key


@pytest.fixture
def test_image(tmp_path) -> Path:
    """Create a temporary test image with realistic dimensions and color."""
    img_path = tmp_path / "test_page_19.jpg"
    im = Image.new("RGB", (400, 600), color=(245, 240, 230))
    im.save(img_path, format="JPEG")
    return img_path


@pytest.fixture
def mock_ocr_response():
    """Sample raw OCR response from backend engine."""
    return OcrResponse(
        text_content="Sample extracted text line 1\nSample extracted text line 2",
        bounding_boxes=[(10.0, 20.0, 390.0, 50.0, "Sample line")],
        blocks=[
            {
                "id": "block_1",
                "type": "paragraph",
                "bbox": [10, 20, 390, 50],
                "reading_order": 1,
                "content": "Sample extracted text line 1",
                "confidence": 0.95,
            }
        ],
        content_format="blocks",
        page_width=400,
        page_height=600,
        pipeline="standard",
        coordinate_space="pixel",
        contract_version="2.2",
        model={"name": "dots-ocr", "version": "1.0.0"},
        page_confidence=0.95,
        p05=0.95,
        blocks_count=1,
        chars_count=28,
        engine_latency_ms=150.0,
    )


# ---------------------------------------------------------------------------
# 1. Normal OCR remains unchanged
# ---------------------------------------------------------------------------
def test_normal_ocr_remains_unchanged(test_image, mock_ocr_response):
    with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response) as mock_remote:
        resp = run_ocr(test_image, engine_name="dots-ocr", language="sa")
        assert resp.ocr_mode == "standard"
        assert resp.enhancement_profile is None
        assert resp.enhancement_version is None
        # Verify normal API dict output does not inject enhanced fields
        api_dict = ocr_response_to_api_dict(resp, "dots_ocr", image_width=400, image_height=600)
        assert "ocr_mode" not in api_dict
        assert "preprocessing" not in api_dict
        assert api_dict["engine"] == "dots_ocr"
        assert api_dict["coordinate_space"] == "pixel"
        mock_remote.assert_called_once_with(test_image, "dots_ocr", "sa")


# ---------------------------------------------------------------------------
# 2. Enhanced OCR can run with Gemma
# ---------------------------------------------------------------------------
def test_enhanced_ocr_runs_with_gemma(test_image, mock_ocr_response):
    gemma_resp = OcrResponse(
        text_content="Gemma text",
        bounding_boxes=[(10.0, 20.0, 390.0, 50.0, "Gemma text")],
        blocks=[
            {
                "id": "g1",
                "type": "paragraph",
                "bbox": [10, 20, 390, 50],
                "reading_order": 1,
                "content": "Gemma text",
                "confidence": 0.92,
            }
        ],
        page_width=400,
        page_height=600,
        coordinate_space="pixel",
        contract_version="2.2",
        model={"name": "gemma-ocr", "version": "1.0.0"},
    )
    with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=gemma_resp) as mock_remote:
        resp = run_enhanced_ocr(
            test_image,
            engine_name="gemma-ocr",
            profile="background_clahe",
            language="sa",
        )
        assert resp.ocr_mode == "enhanced"
        assert resp.engine == "gemma_ocr"
        assert resp.enhancement_profile == "background_clahe"
        assert resp.enhancement_version == "1.0"
        assert resp.preprocessing_latency_ms is not None
        mock_remote.assert_called_once()
        # Verify the file sent to OCR was preprocessed, and engine normalized
        args, _ = mock_remote.call_args
        assert args[1] == "gemma_ocr"
        assert args[2] == "sa"


# ---------------------------------------------------------------------------
# 3. Enhanced OCR can run with Dots
# ---------------------------------------------------------------------------
def test_enhanced_ocr_runs_with_dots(test_image, mock_ocr_response):
    with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response) as mock_remote:
        resp = run_enhanced_ocr(
            test_image,
            engine_name="dots-ocr",
            profile="clahe_1",
            language="sa",
        )
        assert resp.ocr_mode == "enhanced"
        assert resp.engine == "dots_ocr"
        assert resp.enhancement_profile == "clahe_1"
        assert resp.enhancement_version == "1.0"
        mock_remote.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Each supported preprocessing profile works
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", ["clahe_1", "background_clahe", "sharpen", "normal"])
def test_each_preprocessing_profile_works(test_image, profile):
    with Image.open(test_image) as img:
        orig_size = img.size
        processed = preprocess_image(img, profile)
        assert processed is not None
        assert processed.size == orig_size
        assert isinstance(processed, Image.Image)

    with preprocess_image_to_tempfile(test_image, profile) as tmp_file:
        assert tmp_file.exists()
        with Image.open(tmp_file) as proc_img:
            assert proc_img.size == orig_size


# ---------------------------------------------------------------------------
# 5. Invalid engine is rejected
# ---------------------------------------------------------------------------
def test_invalid_engine_is_rejected(test_image):
    with pytest.raises(ValueError, match="Unsupported OCR engine"):
        run_enhanced_ocr(test_image, engine_name="unsupported_engine_xyz", profile="background_clahe")


# ---------------------------------------------------------------------------
# 6. Invalid enhancement profile is rejected
# ---------------------------------------------------------------------------
def test_invalid_enhancement_profile_is_rejected(test_image):
    with pytest.raises(ValueError, match="Unsupported enhancement profile"):
        run_enhanced_ocr(test_image, engine_name="dots-ocr", profile="invalid_magic_profile")

    with pytest.raises(ValueError, match="Unsupported enhancement profile"):
        validate_enhancement_profile("unknown_profile")


# ---------------------------------------------------------------------------
# 7. Enhanced result is marked as enhanced
# ---------------------------------------------------------------------------
def test_enhanced_result_marked_as_enhanced(test_image, mock_ocr_response):
    with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response):
        resp = run_enhanced_ocr(test_image, engine_name="dots-ocr", profile="background_clahe")
        api_dict = ocr_response_to_api_dict(resp, "dots_ocr", image_width=400, image_height=600)
        assert api_dict["ocr_mode"] == "enhanced"
        assert api_dict["enhancement_version"] == "1.0"
        assert api_dict["contract_version"] == "2.2"


# ---------------------------------------------------------------------------
# 8. Engine and preprocessing metadata are stored
# ---------------------------------------------------------------------------
def test_engine_and_preprocessing_metadata_stored(test_image, mock_ocr_response):
    with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response):
        resp = run_enhanced_ocr(test_image, engine_name="dots-ocr", profile="background_clahe")
        api_dict = ocr_response_to_api_dict(resp, "dots_ocr", image_width=400, image_height=600)
        assert api_dict["engine"] == "dots_ocr"
        assert api_dict["preprocessing"] == {"profile": "background_clahe"}
        assert api_dict["model"] == {"name": "dots-ocr", "version": "1.0.0"}
        assert "preprocessing_latency_ms" in api_dict


# ---------------------------------------------------------------------------
# 9. Enhanced result does not overwrite normal OCR
# ---------------------------------------------------------------------------
def test_enhanced_result_does_not_overwrite_normal_ocr(flask_app):
    with flask_app.app_context():
        mem_storage = MemoryStorage()
        with patch("kalanjiyam.utils.storage.get_storage", return_value=mem_storage):

            project_slug = "cool-book"
            page_slug = "19"

            # 1. Store normal OCR
            normal_key = page_ocr_key(project_slug, page_slug)
            normal_payload = {"text": "normal ocr text", "mode": "standard"}
            mem_storage.save_json_gz(normal_key, normal_payload)

            # 2. Store enhanced OCR
            enhanced_key = page_enhanced_ocr_key(project_slug, page_slug, "dots-ocr", "background_clahe")
            enhanced_payload = {
                "contract_version": "2.2",
                "ocr_mode": "enhanced",
                "enhancement_version": "1.0",
                "engine": "dots-ocr",
                "preprocessing": {"profile": "background_clahe"},
                "blocks": [],
            }
            mem_storage.save_json_gz(enhanced_key, enhanced_payload)

            # Assert keys are completely distinct
            assert normal_key != enhanced_key
            assert "enhanced" in enhanced_key
            assert "normal" not in enhanced_key

            # Assert loading normal OCR returns original untouched normal content
            loaded_normal = mem_storage.load_json_gz(normal_key)
            assert loaded_normal == normal_payload
            assert loaded_normal["mode"] == "standard"

            # Assert loading enhanced OCR returns enhanced content
            loaded_enhanced = mem_storage.load_json_gz(enhanced_key)
            assert loaded_enhanced["ocr_mode"] == "enhanced"
            assert loaded_enhanced["preprocessing"]["profile"] == "background_clahe"


# ---------------------------------------------------------------------------
# 10. JSON is correctly gzip-compressed and can be read back
# ---------------------------------------------------------------------------
def test_json_gzip_compression_and_decompression(flask_app):
    with flask_app.app_context():
        mem_storage = MemoryStorage()
        with patch("kalanjiyam.utils.storage.get_storage", return_value=mem_storage):

            key = page_enhanced_ocr_key("proj", "19", "dots-ocr", "clahe_1")
            data = {
                "ocr_mode": "enhanced",
                "engine": "dots-ocr",
                "enhancement_version": "1.0",
                "preprocessing": {"profile": "clahe_1"},
                "blocks": [{"id": "b1", "content": "compressed text"}],
            }
            mem_storage.save_json_gz(key, data)

            # Verify raw bytes in storage are valid gzip
            raw_bytes = mem_storage.read_bytes(key)
            decompressed_raw = gzip.decompress(raw_bytes).decode("utf-8")
            parsed = json.loads(decompressed_raw)
            assert parsed == data

            # Verify loading via load_json_gz helper
            loaded = mem_storage.load_json_gz(key)
            assert loaded == data


# ---------------------------------------------------------------------------
# 11. Page dimensions / coordinate space remain correct
# ---------------------------------------------------------------------------
def test_page_dimensions_and_coordinate_space(test_image, mock_ocr_response):
    with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response):
        resp = run_enhanced_ocr(test_image, engine_name="dots-ocr", profile="background_clahe")
        api_dict = ocr_response_to_api_dict(resp, "dots_ocr", image_width=400, image_height=600)
        assert api_dict["page_width"] == 400
        assert api_dict["page_height"] == 600
        assert api_dict["coordinate_space"] == "pixel"
        for block in api_dict["blocks"]:
            bbox = block["bbox"]
            assert len(bbox) == 4
            assert 0 <= bbox[0] <= 400
            assert 0 <= bbox[1] <= 600
            assert 0 <= bbox[2] <= 400
            assert 0 <= bbox[3] <= 600


# ---------------------------------------------------------------------------
# 12. Different engine + preprocessing combinations produce distinguishable versions/results
# ---------------------------------------------------------------------------
def test_different_combinations_produce_distinguishable_results(flask_app):
    with flask_app.app_context():
        # Keys for 3 combinations on page 19:
        # page 19 + dots + background_clahe
        # page 19 + gemma + background_clahe
        # page 19 + dots + clahe_1
        key1 = page_enhanced_ocr_key("cool-book", "19", "dots-ocr", "background_clahe")
        key2 = page_enhanced_ocr_key("cool-book", "19", "gemma-ocr", "background_clahe")
        key3 = page_enhanced_ocr_key("cool-book", "19", "dots-ocr", "clahe_1")

        assert key1 != key2
        assert key1 != key3
        assert key2 != key3

        assert "dots-ocr/background_clahe/19.json.gz" in key1
        assert "gemma-ocr/background_clahe/19.json.gz" in key2
        assert "dots-ocr/clahe_1/19.json.gz" in key3

        # Revision tags for version tracks:
        class MockRevision:
            def __init__(self, key):
                self.page_version = MagicMock(version_key=key)
                self.summary = ""
                self.translations = []
                self.author = None

        rev1 = MockRevision("ocr:enhanced:dots_ocr:background_clahe")
        rev2 = MockRevision("ocr:enhanced:gemma_ocr:background_clahe")
        rev3 = MockRevision("ocr:enhanced:dots_ocr:clahe_1")
        rev_normal = MockRevision("ocr:dots_ocr")

        tag1 = derive_revision_tag(rev1)
        tag2 = derive_revision_tag(rev2)
        tag3 = derive_revision_tag(rev3)
        tag_normal = derive_revision_tag(rev_normal)

        assert tag1 == "ocr-enhanced-dots-ocr_background-clahe"
        assert tag2 == "ocr-enhanced-gemma-ocr_background-clahe"
        assert tag3 == "ocr-enhanced-dots-ocr_clahe-1"
        assert tag_normal == "ocr-dots-ocr"

        assert len({tag1, tag2, tag3, tag_normal}) == 4


# ---------------------------------------------------------------------------
# 13. API Route for Enhanced OCR
# ---------------------------------------------------------------------------
def test_enhanced_ocr_api_endpoint(flask_app, mock_ocr_response, tmp_path):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q

    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first() or db.Board(name="Test Board Enhanced")
        session.add(board)
        session.flush()

        status = session.query(db.PageStatus).first()
        project = db.Project(
            slug="test-enhanced-ocr-book",
            display_title="Test Enhanced OCR Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page)
        session.commit()

        dummy_img = tmp_path / "page_1.jpg"
        Image.new("RGB", (400, 600), color=(250, 250, 250)).save(dummy_img, format="JPEG")

        with flask_app.test_client() as client:
            with patch("kalanjiyam.views.proofing.page.get_page_image_filepath", return_value=dummy_img), \
                 patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response), \
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

                resp = client.get(
                    f"/api/enhanced-ocr/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=background_clahe&language=sa"
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["ocr_mode"] == "enhanced"
                assert data["enhancement_version"] == "1.0"
                assert data["preprocessing"]["profile"] == "background_clahe"
                assert data["engine"] == "dots_ocr"

                # Test alias route /api/ocr/enhanced/
                resp_alias = client.get(
                    f"/api/ocr/enhanced/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=background_clahe&language=sa"
                )
                assert resp_alias.status_code == 200

                # Test invalid enhancement profile via API returns 400
                bad_resp = client.get(
                    f"/api/enhanced-ocr/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=bad_profile"
                )
                assert bad_resp.status_code == 400


# ---------------------------------------------------------------------------
# 14. Background Task for Enhanced OCR
# ---------------------------------------------------------------------------
def test_enhanced_ocr_background_task(flask_app, mock_ocr_response, tmp_path):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q
    from kalanjiyam.tasks.ocr import _run_enhanced_ocr_for_page_inner

    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first() or db.Board(name="Test Board Task")
        session.add(board)
        session.flush()

        status = session.query(db.PageStatus).first()
        project = db.Project(
            slug="test-task-enhanced-book",
            display_title="Test Task Enhanced Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page)
        session.commit()

        dummy_img = tmp_path / "page_task.jpg"
        Image.new("RGB", (400, 600), color=(250, 250, 250)).save(dummy_img, format="JPEG")

        with patch("kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img), \
             patch("kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response), \
             patch("kalanjiyam.utils.quotas.ensure_ocr_quota_for_project"), \
             patch("kalanjiyam.utils.quotas.consume_ocr_credit_for_project"):

            result = _run_enhanced_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug=page.slug,
                engine="dots-ocr",
                profile="background_clahe",
                language="sa",
            )
            assert result is not None
            assert result["ocr_mode"] == "enhanced"
            assert result["preprocessing"]["profile"] == "background_clahe"
            assert result["engine"] == "dots_ocr"
