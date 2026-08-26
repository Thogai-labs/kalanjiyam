"""Comprehensive tests for Enhanced OCR pipeline.

Covers:
1. Normal OCR remains unchanged.
2. Enhanced OCR can run with Gemma.
3. Enhanced OCR can run with Dots.
4. Each supported preprocessing profile works (document_cleanup, clahe, sharpen, text_enhancement).
5. Output dimensions are preserved and source image is not modified in-place.
6. Each profile produces a distinct preprocessed output.
7. Preprocessing parameters and custom PreprocessingConfig work.
8. Invalid engine is rejected.
9. Invalid enhancement profile (including "normal") is rejected cleanly.
10. Alias resolution works (background_clahe -> document_cleanup, clahe_1 -> clahe).
11. Enhanced result is marked as enhanced with both 'enhancement' and 'preprocessing' metadata.
12. Enhanced result does not overwrite normal OCR.
13. JSON is correctly gzip-compressed and can be read back.
14. Page dimensions / coordinate space remain correct.
15. Different engine + preprocessing combinations produce distinguishable versions/results.
16. API Route for Enhanced OCR.
17. Background Task for Enhanced OCR.
"""

import gzip
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from kalanjiyam.utils.document_storage import derive_revision_tag
from kalanjiyam.utils.image_preprocessing import (
    DEFAULT_PREPROCESSING_CONFIG,
    SUPPORTED_ENHANCEMENT_PROFILES,
    PreprocessingConfig,
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
    """Create a temporary test image with realistic scanned page content (text, background, stain)."""
    img_path = tmp_path / "test_page_19.jpg"
    im = Image.new("RGB", (400, 600), color=(235, 225, 205))  # Aged paper color
    draw = ImageDraw.Draw(im)
    # Add simulated lines of text / strokes
    for y in range(50, 550, 30):
        draw.line([(30, y), (370, y)], fill=(40, 35, 30), width=3)
    # Add uneven illumination gradient / stain
    for i in range(100):
        draw.rectangle(
            [i, i, 400 - i, 600 - i], outline=(220 - i // 2, 210 - i // 2, 190 - i // 2)
        )
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
    with patch(
        "kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response
    ) as mock_remote:
        resp = run_ocr(test_image, engine_name="dots-ocr", language="sa")
        assert resp.ocr_mode == "standard"
        assert resp.enhancement_profile is None
        assert resp.enhancement_version is None
        # Verify normal API dict output does not inject enhanced fields
        api_dict = ocr_response_to_api_dict(
            resp, "dots_ocr", image_width=400, image_height=600
        )
        assert "ocr_mode" not in api_dict
        assert "enhancement" not in api_dict
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
    with patch(
        "kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=gemma_resp
    ) as mock_remote:
        resp = run_enhanced_ocr(
            test_image,
            engine_name="gemma-ocr",
            profile="document_cleanup",
            language="sa",
        )
        assert resp.ocr_mode == "enhanced"
        assert resp.engine == "gemma_ocr"
        assert resp.enhancement_profile == "document_cleanup"
        assert resp.enhancement_version == "1.0"
        assert resp.preprocessing_latency_ms is not None
        mock_remote.assert_called_once()
        args, _ = mock_remote.call_args
        assert args[1] == "gemma_ocr"
        assert args[2] == "sa"


# ---------------------------------------------------------------------------
# 3. Enhanced OCR can run with Dots
# ---------------------------------------------------------------------------
def test_enhanced_ocr_runs_with_dots(test_image, mock_ocr_response):
    with patch(
        "kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response
    ) as mock_remote:
        resp = run_enhanced_ocr(
            test_image,
            engine_name="dots-ocr",
            profile="bg_clahe",
            language="sa",
        )
        assert resp.ocr_mode == "enhanced"
        assert resp.engine == "dots_ocr"
        assert resp.enhancement_profile == "bg_clahe"
        assert resp.enhancement_version == "1.0"
        mock_remote.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Each supported preprocessing profile works
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("profile", SUPPORTED_ENHANCEMENT_PROFILES)
def test_each_preprocessing_profile_works(test_image, profile):
    with Image.open(test_image) as img:
        orig_size = img.size
        orig_pixels = list(img.getdata())
        processed = preprocess_image(img, profile)
        assert processed is not None
        assert processed.size == orig_size
        assert isinstance(processed, Image.Image)
        # Verify source image was not mutated in-place
        assert list(img.getdata()) == orig_pixels

    with preprocess_image_to_tempfile(test_image, profile) as tmp_file:
        assert tmp_file.exists()
        with Image.open(tmp_file) as proc_img:
            assert proc_img.size == orig_size


# ---------------------------------------------------------------------------
# 5. Output dimensions are preserved and source image is not modified
# ---------------------------------------------------------------------------
def test_dimensions_and_source_immutability(test_image):
    with Image.open(test_image) as original:
        orig_data = list(original.getdata())

        for profile in SUPPORTED_ENHANCEMENT_PROFILES:
            result = preprocess_image(original, profile)
            assert result.size == (400, 600)
            # Original remains identical
            assert list(original.getdata()) == orig_data


# ---------------------------------------------------------------------------
# 6. Each profile produces a distinct preprocessed output
# ---------------------------------------------------------------------------
def test_distinct_preprocessing_outputs(test_image):
    with Image.open(test_image) as img:
        outputs = {}
        for profile in SUPPORTED_ENHANCEMENT_PROFILES:
            proc = preprocess_image(img, profile)
            # Store pixel sample hash
            outputs[profile] = list(proc.convert("L").getdata())

        # Verify all profiles produce mutually distinct pixel arrays
        profiles = list(SUPPORTED_ENHANCEMENT_PROFILES)
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                p1, p2 = profiles[i], profiles[j]
                assert outputs[p1] != outputs[p2], (
                    f"Outputs of {p1} and {p2} should be distinct"
                )


# ---------------------------------------------------------------------------
# 7. Preprocessing parameters and custom PreprocessingConfig work
# ---------------------------------------------------------------------------
def test_custom_preprocessing_config(test_image):
    with Image.open(test_image) as img:
        cfg_default = DEFAULT_PREPROCESSING_CONFIG
        cfg_custom = PreprocessingConfig(
            clahe_clip_limit=5.0,
            sharpen_percent=250,
            text_gamma=0.40,
        )

        res_bg_clahe_def = preprocess_image(img, "bg_clahe", config=cfg_default)
        res_bg_clahe_custom = preprocess_image(img, "bg_clahe", config=cfg_custom)
        assert list(res_bg_clahe_def.getdata()) != list(res_bg_clahe_custom.getdata())

        res_sharp_def = preprocess_image(img, "sharpen", config=cfg_default)
        res_sharp_custom = preprocess_image(img, "sharpen", config=cfg_custom)
        assert list(res_sharp_def.getdata()) != list(res_sharp_custom.getdata())


# ---------------------------------------------------------------------------
# 8. Invalid engine is rejected
# ---------------------------------------------------------------------------
def test_invalid_engine_is_rejected(test_image):
    with pytest.raises(ValueError, match="Unsupported OCR engine"):
        run_enhanced_ocr(
            test_image, engine_name="unsupported_engine_xyz", profile="document_cleanup"
        )


# ---------------------------------------------------------------------------
# 9. Invalid enhancement profile (including 'normal') is rejected
# ---------------------------------------------------------------------------
def test_invalid_enhancement_profile_is_rejected(test_image):
    with pytest.raises(ValueError, match="Unsupported enhancement profile"):
        run_enhanced_ocr(
            test_image, engine_name="dots-ocr", profile="invalid_magic_profile"
        )

    # "normal" profile is intentionally removed and must be rejected
    with pytest.raises(ValueError, match="Unsupported enhancement profile"):
        validate_enhancement_profile("normal")

    with pytest.raises(ValueError, match="Unsupported enhancement profile"):
        validate_enhancement_profile("unknown_profile")


# ---------------------------------------------------------------------------
# 10. Alias resolution works
# ---------------------------------------------------------------------------
def test_profile_alias_resolution():
    assert validate_enhancement_profile("background_clahe") == "bg_clahe"
    assert validate_enhancement_profile("bg_clahe") == "bg_clahe"
    assert validate_enhancement_profile("bg+clahe") == "bg_clahe"
    assert validate_enhancement_profile("clahe") == "bg_clahe"
    assert validate_enhancement_profile("clahe_1") == "bg_clahe"
    assert validate_enhancement_profile("DOCUMENT_CLEANUP") == "document_cleanup"
    assert validate_enhancement_profile("SHARPEN") == "sharpen"
    assert validate_enhancement_profile("text_enhancement") == "text_enhancement"
    assert validate_enhancement_profile("hybrid_binarization") == "hybrid_binarization"
    assert validate_enhancement_profile("HYBRID") == "hybrid_binarization"
    assert validate_enhancement_profile("historical_hybrid") == "hybrid_binarization"
    assert validate_enhancement_profile("binarize") == "hybrid_binarization"


# ---------------------------------------------------------------------------
# 11. Enhanced result is marked as enhanced with enhancement & preprocessing metadata
# ---------------------------------------------------------------------------
def test_enhanced_result_metadata(test_image, mock_ocr_response):
    with patch(
        "kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response
    ):
        resp = run_enhanced_ocr(
            test_image, engine_name="dots-ocr", profile="document_cleanup"
        )
        api_dict = ocr_response_to_api_dict(
            resp, "dots_ocr", image_width=400, image_height=600
        )
        assert api_dict["ocr_mode"] == "enhanced"
        assert api_dict["enhancement_version"] == "1.0"
        assert api_dict["contract_version"] == "2.2"
        assert api_dict["engine"] == "dots_ocr"
        assert api_dict["enhancement"] == {
            "profile": "document_cleanup",
            "version": "1.0",
        }
        assert api_dict["preprocessing"] == {
            "profile": "document_cleanup",
            "version": "1.0",
        }
        assert api_dict["model"] == {"name": "dots-ocr", "version": "1.0.0"}
        assert "preprocessing_latency_ms" in api_dict


# ---------------------------------------------------------------------------
# 12. Enhanced result does not overwrite normal OCR
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
            enhanced_key = page_enhanced_ocr_key(
                project_slug, page_slug, "dots-ocr", "document_cleanup"
            )
            enhanced_payload = {
                "contract_version": "2.2",
                "ocr_mode": "enhanced",
                "enhancement_version": "1.0",
                "engine": "dots-ocr",
                "enhancement": {"profile": "document_cleanup", "version": "1.0"},
                "preprocessing": {"profile": "document_cleanup", "version": "1.0"},
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
            assert loaded_enhanced["enhancement"]["profile"] == "document_cleanup"


# ---------------------------------------------------------------------------
# 13. JSON is correctly gzip-compressed and can be read back
# ---------------------------------------------------------------------------
def test_json_gzip_compression_and_decompression(flask_app):
    with flask_app.app_context():
        mem_storage = MemoryStorage()
        with patch("kalanjiyam.utils.storage.get_storage", return_value=mem_storage):
            key = page_enhanced_ocr_key("proj", "19", "dots-ocr", "bg_clahe")
            data = {
                "ocr_mode": "enhanced",
                "engine": "dots-ocr",
                "enhancement_version": "1.0",
                "enhancement": {"profile": "bg_clahe", "version": "1.0"},
                "preprocessing": {"profile": "bg_clahe", "version": "1.0"},
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
# 14. Page dimensions / coordinate space remain correct
# ---------------------------------------------------------------------------
def test_page_dimensions_and_coordinate_space(test_image, mock_ocr_response):
    with patch(
        "kalanjiyam.utils.ocr_runner.run_ocr_remote", return_value=mock_ocr_response
    ):
        resp = run_enhanced_ocr(
            test_image, engine_name="dots-ocr", profile="text_enhancement"
        )
        api_dict = ocr_response_to_api_dict(
            resp, "dots_ocr", image_width=400, image_height=600
        )
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
# 15. Different engine + preprocessing combinations produce distinguishable versions/results
# ---------------------------------------------------------------------------
def test_different_combinations_produce_distinguishable_results(flask_app):
    with flask_app.app_context():
        # Keys for combinations on page 19:
        key1 = page_enhanced_ocr_key("cool-book", "19", "dots-ocr", "document_cleanup")
        key2 = page_enhanced_ocr_key("cool-book", "19", "gemma-ocr", "document_cleanup")
        key3 = page_enhanced_ocr_key("cool-book", "19", "dots-ocr", "bg_clahe")
        key4 = page_enhanced_ocr_key("cool-book", "19", "dots-ocr", "text_enhancement")

        assert len({key1, key2, key3, key4}) == 4

        assert "dots-ocr/document_cleanup/19.json.gz" in key1
        assert "gemma-ocr/document_cleanup/19.json.gz" in key2
        assert "dots-ocr/bg_clahe/19.json.gz" in key3
        assert "dots-ocr/text_enhancement/19.json.gz" in key4

        # Revision tags for version tracks:
        class MockRevision:
            def __init__(self, key):
                self.page_version = MagicMock(version_key=key)
                self.summary = ""
                self.translations = []
                self.author = None

        rev1 = MockRevision("ocr:enhanced:dots_ocr:document_cleanup")
        rev2 = MockRevision("ocr:enhanced:gemma_ocr:document_cleanup")
        rev3 = MockRevision("ocr:enhanced:dots_ocr:bg_clahe")
        rev4 = MockRevision("ocr:enhanced:dots_ocr:text_enhancement")
        rev_normal = MockRevision("ocr:dots_ocr")

        tag1 = derive_revision_tag(rev1)
        tag2 = derive_revision_tag(rev2)
        tag3 = derive_revision_tag(rev3)
        tag4 = derive_revision_tag(rev4)
        tag_normal = derive_revision_tag(rev_normal)

        assert tag1 == "ocr-enhanced-dots-ocr_document-cleanup"
        assert tag2 == "ocr-enhanced-gemma-ocr_document-cleanup"
        assert tag3 == "ocr-enhanced-dots-ocr_bg-clahe"
        assert tag4 == "ocr-enhanced-dots-ocr_text-enhancement"
        assert tag_normal == "ocr-dots-ocr"

        assert len({tag1, tag2, tag3, tag4, tag_normal}) == 5


# ---------------------------------------------------------------------------
# 16. API Route for Enhanced OCR
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
        Image.new("RGB", (400, 600), color=(250, 250, 250)).save(
            dummy_img, format="JPEG"
        )

        with flask_app.test_client() as client:
            with (
                patch(
                    "kalanjiyam.views.proofing.page.get_page_image_filepath",
                    return_value=dummy_img,
                ),
                patch(
                    "kalanjiyam.utils.ocr_runner.run_ocr_remote",
                    return_value=mock_ocr_response,
                ),
                patch("kalanjiyam.utils.quotas.ensure_ocr_quota_for_project"),
                patch("kalanjiyam.utils.quotas.consume_ocr_credit_for_project"),
                patch(
                    "kalanjiyam.views.proofing.page.q.user_can_view_proofing_project",
                    return_value=True,
                ),
                patch("kalanjiyam.views.proofing.decorators.current_user") as dec_user,
                patch("kalanjiyam.views.proofing.page.current_user") as mock_user,
            ):
                for u in (dec_user, mock_user):
                    u.is_authenticated = True
                    u.is_super_admin = False
                    u.is_org_admin = True
                    u.is_moderator = True
                    u.is_p2 = True
                    u.is_p1 = True
                    u.id = 1

                # Test document_cleanup
                resp = client.get(
                    f"/api/enhanced-ocr/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=document_cleanup&language=sa"
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["ocr_mode"] == "enhanced"
                assert data["enhancement_version"] == "1.0"
                assert data["enhancement"]["profile"] == "document_cleanup"
                assert data["preprocessing"]["profile"] == "document_cleanup"
                assert data["engine"] == "dots_ocr"

                # Test text_enhancement
                resp_text_enh = client.get(
                    f"/api/enhanced-ocr/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=text_enhancement&language=sa"
                )
                assert resp_text_enh.status_code == 200
                data_text_enh = resp_text_enh.get_json()
                assert data_text_enh["enhancement"]["profile"] == "text_enhancement"

                # Test alias route /api/ocr/enhanced/ with alias background_clahe -> bg_clahe
                resp_alias = client.get(
                    f"/api/ocr/enhanced/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=background_clahe&language=sa"
                )
                assert resp_alias.status_code == 200
                assert resp_alias.get_json()["enhancement"]["profile"] == "bg_clahe"

                # Test invalid enhancement profile via API returns 400
                bad_resp = client.get(
                    f"/api/enhanced-ocr/{project.slug}/{page.slug}/?engine=dots_ocr&enhancement=bad_profile"
                )
                assert bad_resp.status_code == 400


# ---------------------------------------------------------------------------
# 17. Background Task for Enhanced OCR
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
        Image.new("RGB", (400, 600), color=(250, 250, 250)).save(
            dummy_img, format="JPEG"
        )

        with (
            patch(
                "kalanjiyam.tasks.ocr.get_page_image_filepath", return_value=dummy_img
            ),
            patch(
                "kalanjiyam.utils.ocr_runner.run_ocr_remote",
                return_value=mock_ocr_response,
            ),
            patch("kalanjiyam.utils.quotas.ensure_ocr_quota_for_project"),
            patch("kalanjiyam.utils.quotas.consume_ocr_credit_for_project"),
        ):
            result = _run_enhanced_ocr_for_page_inner(
                app_env="testing",
                project_slug=project.slug,
                page_slug=page.slug,
                engine="dots-ocr",
                profile="document_cleanup",
                language="sa",
            )
            assert result is not None
            assert result["ocr_mode"] == "enhanced"
            assert result["enhancement"]["profile"] == "document_cleanup"
            assert result["preprocessing"]["profile"] == "document_cleanup"
            assert result["engine"] == "dots_ocr"


# ---------------------------------------------------------------------------
# 18. Preview Enhancement API Endpoint
# ---------------------------------------------------------------------------
def test_preview_enhancement_endpoint(flask_app, tmp_path):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q

    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first() or db.Board(name="Test Board Preview")
        session.add(board)
        session.flush()

        status = session.query(db.PageStatus).first()
        project = db.Project(
            slug="test-preview-book",
            display_title="Test Preview Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page)
        session.commit()

        dummy_img = tmp_path / "preview_page.jpg"
        Image.new("RGB", (200, 300), color=(220, 220, 220)).save(
            dummy_img, format="JPEG"
        )

        with flask_app.test_client() as client:
            with (
                patch(
                    "kalanjiyam.views.proofing.page.get_page_image_filepath",
                    return_value=dummy_img,
                ),
                patch(
                    "kalanjiyam.views.proofing.page.q.user_can_view_proofing_project",
                    return_value=True,
                ),
                patch("kalanjiyam.views.proofing.decorators.current_user") as dec_user,
                patch("kalanjiyam.views.proofing.page.current_user") as mock_user,
            ):
                for u in (dec_user, mock_user):
                    u.is_authenticated = True
                    u.is_super_admin = True
                    u.id = 1

                # 1. Preview hybrid_binarization
                resp = client.get(
                    f"/api/preview-enhancement/{project.slug}/{page.slug}/?profile=hybrid_binarization"
                )
                assert resp.status_code == 200
                assert resp.content_type == "image/jpeg"
                assert len(resp.data) > 0

                # 2. Preview document_cleanup
                resp_doc = client.get(
                    f"/api/preview-enhancement/{project.slug}/{page.slug}/?profile=document_cleanup"
                )
                assert resp_doc.status_code == 200
                assert resp_doc.content_type == "image/jpeg"

                # 3. Invalid profile returns 400
                resp_bad = client.get(
                    f"/api/preview-enhancement/{project.slug}/{page.slug}/?profile=bad_profile_xyz"
                )
                assert resp_bad.status_code == 400


# ---------------------------------------------------------------------------
# 19. Replace and Revert Page Image API Endpoint
# ---------------------------------------------------------------------------
def test_replace_and_revert_page_image_endpoint(flask_app, tmp_path):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q

    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first() or db.Board(name="Test Board Replace")
        session.add(board)
        session.flush()

        status = session.query(db.PageStatus).first()
        project = db.Project(
            slug="test-replace-book",
            display_title="Test Replace Book",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        session.add(page)
        session.commit()

        dummy_img = tmp_path / "replace_page.jpg"
        Image.new("RGB", (200, 300), color=(200, 200, 200)).save(
            dummy_img, format="JPEG"
        )

        from kalanjiyam.utils.storage import (
            get_project_org_slug,
            get_storage,
            page_master_image_key,
        )

        storage = get_storage()
        org_slug = get_project_org_slug(project)
        m_key = page_master_image_key(project.slug, page.slug, org_slug=org_slug)
        if storage.exists(m_key):
            storage.delete(m_key)

        with flask_app.test_client() as client:
            with (
                patch(
                    "kalanjiyam.views.proofing.page.get_page_image_filepath",
                    return_value=dummy_img,
                ),
                patch(
                    "kalanjiyam.views.proofing.page.q.user_can_view_proofing_project",
                    return_value=True,
                ),
                patch("kalanjiyam.views.proofing.decorators.current_user") as dec_user,
                patch("kalanjiyam.views.proofing.page.current_user") as mock_user,
            ):
                for u in (dec_user, mock_user):
                    u.is_authenticated = True
                    u.is_super_admin = True
                    u.id = 1

                # Check initial status
                status_resp = client.get(
                    f"/api/replace-page-image/{project.slug}/{page.slug}/?action=status"
                )
                assert status_resp.status_code == 200
                assert status_resp.get_json()["has_master_backup"] is False

                # Replace with hybrid_binarization
                replace_resp = client.post(
                    f"/api/replace-page-image/{project.slug}/{page.slug}/",
                    json={"action": "replace", "profile": "hybrid_binarization"},
                )
                assert replace_resp.status_code == 200
                assert replace_resp.get_json()["status"] == "ok"
                assert replace_resp.get_json()["is_preprocessed"] is True

                # Status should now indicate master backup exists
                status_after = client.get(
                    f"/api/replace-page-image/{project.slug}/{page.slug}/?action=status"
                )
                assert status_after.get_json()["has_master_backup"] is True

                # Revert back to original
                revert_resp = client.post(
                    f"/api/replace-page-image/{project.slug}/{page.slug}/",
                    json={"action": "revert"},
                )
                assert revert_resp.status_code == 200
                assert revert_resp.get_json()["status"] == "ok"
                assert revert_resp.get_json()["is_preprocessed"] is False


# ---------------------------------------------------------------------------
# 20. Batch Enhanced OCR Endpoints and Task Dispatch
# ---------------------------------------------------------------------------
def test_batch_enhanced_ocr_get_and_post(flask_app, tmp_path):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q

    with flask_app.app_context():
        session = q.get_session()
        board = session.query(db.Board).first() or db.Board(
            name="Test Board Batch Enhanced"
        )
        session.add(board)
        session.flush()

        project = db.Project(
            slug=f"test-batch-enh-{uuid.uuid4().hex[:6]}",
            board_id=board.id,
            display_title="Test Batch Enhanced OCR Project",
        )
        session.add(project)
        session.flush()

        status = session.query(db.PageStatus).first() or db.PageStatus(
            name="Status Batch"
        )
        session.add(status)
        session.flush()

        page1 = db.Page(project_id=project.id, order=1, slug="1", status_id=status.id)
        page2 = db.Page(project_id=project.id, order=2, slug="2", status_id=status.id)
        session.add_all([page1, page2])
        session.commit()

        with flask_app.test_client() as client:
            with (
                patch(
                    "kalanjiyam.views.proofing.project.q.user_can_view_proofing_project",
                    return_value=True,
                ),
                patch("kalanjiyam.views.proofing.decorators.current_user") as dec_user,
                patch("kalanjiyam.views.proofing.project.current_user") as mock_user,
                patch("kalanjiyam.views.proofing.project.redis_client") as mock_redis,
                patch(
                    "kalanjiyam.views.proofing.project.ocr_tasks.run_enhanced_ocr_for_project"
                ) as mock_run_proj,
            ):
                mock_redis.get.return_value = None
                mock_redis.scan_iter.return_value = []
                for u in (dec_user, mock_user):
                    u.is_authenticated = True
                    u.is_super_admin = True
                    u.is_moderator = True
                    u.id = 1

                # Mock task return
                mock_task = MagicMock()
                mock_task.id = "test-enhanced-task-id-123"
                mock_run_proj.return_value = mock_task

                # 1. GET Batch Enhanced OCR config form
                get_resp = client.get(f"/proofing/{project.slug}/batch-enhanced-ocr")
                assert get_resp.status_code == 200
                assert b"Enhanced Batch OCR Pipeline" in get_resp.data
                assert b"Hybrid Binarization" in get_resp.data

                # 2. POST to trigger Enhanced Batch OCR with save_enhanced_images=1
                post_resp = client.post(
                    f"/proofing/{project.slug}/batch-enhanced-ocr",
                    data={
                        "engine": "12",
                        "profile": "hybrid_binarization",
                        "language": "sa",
                        "save_enhanced_images": "1",
                    },
                )
                assert post_resp.status_code == 200
                mock_run_proj.assert_called_once()
                call_kwargs = mock_run_proj.call_args.kwargs
                assert call_kwargs["engine"] == "dots_ocr"
                assert call_kwargs["profile"] == "hybrid_binarization"
                assert call_kwargs["save_enhanced_images"] is True

                # 3. Check status endpoint
                with patch(
                    "kalanjiyam.views.proofing.project.GroupResult.restore"
                ) as mock_restore:
                    mock_group_res = MagicMock()
                    mock_group_res.results = [
                        MagicMock(state="SUCCESS", failed=lambda: False)
                    ]
                    mock_group_res.completed_count.return_value = 1
                    mock_restore.return_value = mock_group_res

                    status_resp = client.get(
                        f"/proofing/batch-enhanced-ocr-status/{mock_task.id}"
                    )
                    assert status_resp.status_code == 200
