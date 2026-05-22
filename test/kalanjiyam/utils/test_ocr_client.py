"""Tests for OCR remote client and runner."""

from pathlib import Path
from unittest.mock import MagicMock, patch



def test_parse_bounding_boxes_tsv():
    from kalanjiyam.utils.ocr_client import _parse_bounding_boxes as parse_fn
    blob = "0\t0\t100\t20\tword\n120\t25\t300\t45\tanother"
    boxes = parse_fn(blob, "google")
    assert boxes == [(0, 0, 100, 20, "word"), (120, 25, 300, 45, "another")]


def test_run_ocr_remote(flask_app):
    with flask_app.app_context():
        flask_app.config.update(
            OCR_SERVICE_URL="http://ocr.test",
            OCR_SERVICE_API_KEY="secret",
            OCR_SERVICE_TIMEOUT=30,
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "namaste",
            "bounding_boxes": "0\t0\t10\t10\thi",
            "engine": "tesseract",
            "language": "san",
        }

        with patch("kalanjiyam.utils.ocr_client.httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value = mock_response

            from kalanjiyam.utils.ocr_client import run_ocr_remote

            result = run_ocr_remote(Path("/tmp/page.jpg"), "tesseract", "san")

        assert result.text_content == "namaste"
        assert result.bounding_boxes == [(0, 0, 10, 10, "hi")]


def test_ocr_runner_delegates_to_remote(flask_app):
    with flask_app.app_context():
        flask_app.config.update(OCR_SERVICE_URL="http://ocr.test", OCR_SERVICE_API_KEY="x")

        with patch("kalanjiyam.utils.ocr_client.run_ocr_remote") as remote:
            remote.return_value = OcrResponse(text_content="remote", bounding_boxes=[])
            from kalanjiyam.utils.ocr_runner import run_ocr

            result = run_ocr(Path("/tmp/x.jpg"), "2", "san")

        remote.assert_called_once()
        assert result.text_content == "remote"
