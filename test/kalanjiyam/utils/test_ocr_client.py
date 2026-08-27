"""Tests for OCR remote client and runner."""

from pathlib import Path
from unittest.mock import MagicMock, patch



def test_parse_bounding_boxes_tsv():
    from kalanjiyam.utils.ocr_client import _parse_bounding_boxes as parse_fn
    blob = "0\t0\t100\t20\tword\n120\t25\t300\t45\tanother"
    boxes = parse_fn(blob, "google")
    assert boxes == [(0, 0, 100, 20, "word"), (120, 25, 300, 45, "another")]


def test_parse_bounding_boxes_surya_json():
    from kalanjiyam.utils.ocr_client import _parse_bounding_boxes as parse_fn
    blob = '[{"x1": 1.5, "y1": 2.0, "x2": 10.0, "y2": 20.0, "text": "hi"}]'
    assert parse_fn(blob, "surya") == [(1.5, 2.0, 10.0, 20.0, "hi")]


def test_parse_bounding_boxes_surya_bbox_array():
    from kalanjiyam.utils.ocr_client import _parse_bounding_boxes as parse_fn
    items = [{"bbox": [10, 20, 100, 40], "text": "line"}]
    assert parse_fn(items, "surya") == [(10.0, 20.0, 100.0, 40.0, "line")]


def test_parse_bounding_boxes_surya_list():
    from kalanjiyam.utils.ocr_client import _parse_bounding_boxes as parse_fn
    items = [{"x1": 0, "y1": 0, "x2": 5, "y2": 5, "text": "a"}]
    assert parse_fn(items, "surya") == [(0.0, 0.0, 5.0, 5.0, "a")]


def test_run_ocr_remote(flask_app, tmp_path):
    """New contract: blocks with confidence + language, no legacy text/bounding_boxes."""
    img = tmp_path / "page.jpg"
    img.write_bytes(b"fake")
    with flask_app.app_context():
        flask_app.config.update(
            OCR_SERVICE_URL="http://ocr.test",
            OCR_SERVICE_API_KEY="secret",
            OCR_SERVICE_TIMEOUT=30,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "source_type": "scan",
            "page_width": 1240,
            "page_height": 1754,
            "blocks": [
                {
                    "id": "a1b2c3d4",
                    "type": "paragraph",
                    "bbox": [120, 100, 980, 280],
                    "reading_order": 1,
                    "content": "namaste",
                    "confidence": 0.91,
                    "language": "sa",
                }
            ],
        }

        with patch("kalanjiyam.utils.ocr_client.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = mock_response

            from kalanjiyam.utils.ocr_client import run_ocr_remote

            result = run_ocr_remote(img, "tesseract", "sa")

        assert result.blocks is not None
        assert result.blocks[0]["confidence"] == 0.91
        assert result.blocks[0]["language"] == "sa"
        assert result.source_type == "scan"
        assert result.page_width == 1240


def test_run_ocr_remote_blocks_confidence_survives_pipeline(flask_app, tmp_path):
    """confidence and language must survive Block.from_dict → to_dict."""
    img = tmp_path / "page.jpg"
    img.write_bytes(b"fake")
    with flask_app.app_context():
        flask_app.config.update(OCR_SERVICE_URL="http://ocr.test", OCR_SERVICE_API_KEY="")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "source_type": "scan",
            "page_width": 100,
            "page_height": 200,
            "blocks": [
                {
                    "id": "b1b2c3d4",
                    "type": "heading",
                    "bbox": [0, 0, 100, 20],
                    "reading_order": 1,
                    "content": "Title",
                    "confidence": 0.95,
                    "language": "sa",
                },
                {
                    "id": "c1c2c3c4",
                    "type": "table",
                    "bbox": [0, 30, 100, 80],
                    "reading_order": 2,
                    "content": "<table><tr><td>a</td></tr></table>",
                    "confidence": 0.72,
                    "language": "sa",
                },
            ],
        }

        with patch("kalanjiyam.utils.ocr_client.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = mock_response

            from kalanjiyam.utils.ocr_client import run_ocr_remote
            from kalanjiyam.utils.page_document import Block

            result = run_ocr_remote(img, "surya", "sa")

        assert result.blocks is not None
        block = Block.from_dict(result.blocks[0])
        assert block.type == "heading"
        assert block.confidence == 0.95
        assert block.language == "sa"
        d = block.to_dict()
        assert d["confidence"] == 0.95
        assert d["language"] == "sa"


def test_ocr_runner_delegates_to_remote(flask_app, tmp_path):
    img = tmp_path / "x.jpg"
    img.write_bytes(b"fake")
    with flask_app.app_context():
        flask_app.config.update(OCR_SERVICE_URL="http://ocr.test", OCR_SERVICE_API_KEY="x")

        from kalanjiyam.utils.ocr_types import OcrResponse

        with patch("kalanjiyam.utils.ocr_runner.run_ocr_remote") as remote:
            remote.return_value = OcrResponse(text_content="remote", bounding_boxes=[])
            from kalanjiyam.utils.ocr_runner import run_ocr

            result = run_ocr(img, "2", "san")

        remote.assert_called_once()
        assert result.text_content == "remote"


def test_sanitize_block_clamps_confidence_and_filters_words():
    from kalanjiyam.utils.ocr_client import _sanitize_block

    block = _sanitize_block(
        {
            "id": "b1",
            "type": "paragraph",
            "content": "text here",
            "confidence": 1.7,
            "words": [
                {"text": "text", "bbox": [0, 0, 40, 10], "confidence": 0.42},
                # Contract: words without confidence are dropped.
                {"text": "here", "bbox": [50, 0, 90, 10]},
                # Words without text are dropped.
                {"bbox": [100, 0, 140, 10], "confidence": 0.9},
            ],
        }
    )
    assert block["confidence"] == 1.0
    assert block["words"] == [
        {"text": "text", "confidence": 0.42, "bbox": [0.0, 0.0, 40.0, 10.0]}
    ]


def test_engine_aliases_map_service_ids():
    from kalanjiyam.utils.ocr_types import (
        ENGINE_MAP,
        build_engine_choices,
        engine_for_service,
        normalize_engine,
        normalize_service_engine,
    )

    assert normalize_service_engine("surya-table") == "surya_table"
    assert normalize_service_engine("glm-ocr") == "glm_ocr"
    assert normalize_service_engine("gemma-ocr") == "gemma_ocr"
    assert normalize_service_engine("gemma-4") == "gemma_ocr"
    assert normalize_service_engine("gemma-4-31b") == "gemma_ocr"
    assert normalize_service_engine("llm-gemma") == "gemma_ocr"
    assert normalize_service_engine("llm_gemma") == "gemma_ocr"
    assert engine_for_service("surya_table") == "surya-table"
    assert engine_for_service("glm_ocr") == "glm-ocr"
    assert engine_for_service("gemma_ocr") == "gemma-ocr"
    assert normalize_engine("13") == "gemma_ocr"
    assert ENGINE_MAP["13"] == "gemma_ocr"

    choices = build_engine_choices(
        ["tesseract", "surya-table", "chandra", "glm-ocr", "gemma-ocr"],
        is_super_admin=True,
    )
    labels = {c["label"] for c in choices}
    assert "Surya Table" in labels
    assert "GLM OCR" in labels
    assert "Gemma OCR" in labels


def test_run_ocr_remote_parses_v2_contract_fields(flask_app, tmp_path):
    img = tmp_path / "page.jpg"
    img.write_bytes(b"fake")
    with flask_app.app_context():
        flask_app.config.update(OCR_SERVICE_URL="http://ocr.test", OCR_SERVICE_API_KEY="")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "contract_version": "2.0",
            "engine": "surya",
            "model": {"name": "surya-rec", "version": "0.6.1"},
            "source_type": "scan",
            "page_width": 100,
            "page_height": 200,
            "page_confidence": 0.91,
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "bbox": [0, 0, 100, 20],
                    "reading_order": 1,
                    "content": "hello world",
                    "confidence": 0.62,
                    "language": "sa",
                    "words": [
                        {"text": "hello", "bbox": [0, 0, 40, 20], "confidence": 0.98},
                        {"text": "world", "bbox": [50, 0, 100, 20], "confidence": 0.41},
                    ],
                }
            ],
        }

        with patch("kalanjiyam.utils.ocr_client.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = mock_response

            from kalanjiyam.utils.ocr_client import run_ocr_remote

            result = run_ocr_remote(img, "surya", "sa")

        assert result.model == {"name": "surya-rec", "version": "0.6.1"}
        assert result.page_confidence == 0.91
        assert result.blocks[0]["words"][1]["confidence"] == 0.41


def test_run_ocr_remote_fallback_to_secondary_url(flask_app, tmp_path):
    img = tmp_path / "page.jpg"
    img.write_bytes(b"fake")
    with flask_app.app_context():
        flask_app.config.update(
            OCR_SERVICE_URL="http://primary-ocr.test",
            OCR_SERVICE_URL_2="http://fallback-ocr.test",
            OCR_SERVICE_API_KEY="key1",
            OCR_SERVICE_API_KEY_2="key2",
            OCR_SERVICE_TIMEOUT=30,
        )

        mock_primary_response = MagicMock()
        mock_primary_response.status_code = 503
        mock_primary_response.text = "Primary service unavailable"

        mock_secondary_response = MagicMock()
        mock_secondary_response.status_code = 200
        mock_secondary_response.json.return_value = {
            "source_type": "scan",
            "page_width": 1000,
            "page_height": 1000,
            "blocks": [{"id": "b1", "type": "paragraph", "bbox": [0, 0, 100, 100], "reading_order": 1, "content": "fallback text", "confidence": 0.95}],
        }

        with patch("kalanjiyam.utils.ocr_client.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.side_effect = [mock_primary_response, mock_secondary_response]

            from kalanjiyam.utils.ocr_client import run_ocr_remote

            result = run_ocr_remote(img, "dots_ocr", "sa")

        assert result.blocks[0]["content"] == "fallback text"
        assert client.post.call_count == 2
        first_call = client.post.call_args_list[0]
        second_call = client.post.call_args_list[1]
        assert first_call[0][0] == "http://primary-ocr.test/v1/ocr"
        assert second_call[0][0] == "http://fallback-ocr.test/v1/ocr"
