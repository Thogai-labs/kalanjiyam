import time
import os
from unittest.mock import patch
import pytest

from config import BaseConfig, _env
from kalanjiyam.utils.storage import MemoryStorage, cleanup_old_uploaded_files
from kalanjiyam.tasks.projects import cleanup_uploaded_files_task


def test_auto_cleanup_env_config_parsing():
    for val in ("true", "True", "TRUE", "1", "yes", "YES"):
        with patch.dict(os.environ, {"AUTO_UPLOADED_FILES_CLEANUP": val}):
            assert str(_env("AUTO_UPLOADED_FILES_CLEANUP", "false")).lower() in ("true", "1", "yes")

    for val in ("false", "False", "FALSE", "0", "no", "NO"):
        with patch.dict(os.environ, {"auto_uploaded_files_cleanup": val}, clear=True):
            assert not (str(_env("AUTO_UPLOADED_FILES_CLEANUP", "false")).lower() in ("true", "1", "yes"))


def test_cleanup_old_uploaded_files_deletes_only_old_pdf_doc_files():
    storage = MemoryStorage()
    now = time.time()
    eight_days_ago = now - (8 * 86400)
    six_days_ago = now - (6 * 86400)

    # Old files (>7 days)
    storage.save("projects/proj1/pdf/source.pdf", b"pdf data", mtime=eight_days_ago)
    storage.save("projects/proj2/docx/source.docx", b"docx data", mtime=eight_days_ago)
    storage.save("docx/uploads/old_doc.doc", b"doc data", mtime=eight_days_ago)

    # Recent files (<7 days)
    storage.save("projects/proj3/pdf/source.pdf", b"recent pdf", mtime=six_days_ago)
    storage.save("docx/uploads/recent_doc.docx", b"recent docx", mtime=six_days_ago)

    # Non-PDF/DOC files (>7 days old page image & editor image)
    storage.save("projects/proj1/pages/1.jpg", b"jpg data", mtime=eight_days_ago)
    storage.save("projects/proj1/images/fig.png", b"png data", mtime=eight_days_ago)

    deleted_count = cleanup_old_uploaded_files(storage, days=7)
    assert deleted_count == 3

    # Check old pdf/doc files were deleted
    assert not storage.exists("projects/proj1/pdf/source.pdf")
    assert not storage.exists("projects/proj2/docx/source.docx")
    assert not storage.exists("docx/uploads/old_doc.doc")

    # Check recent files and image assets were kept
    assert storage.exists("projects/proj3/pdf/source.pdf")
    assert storage.exists("docx/uploads/recent_doc.docx")
    assert storage.exists("projects/proj1/pages/1.jpg")
    assert storage.exists("projects/proj1/images/fig.png")


def test_cleanup_uploaded_files_task_respects_config(flask_app):
    storage = MemoryStorage()
    eight_days_ago = time.time() - (8 * 86400)
    storage.save("projects/old/pdf/source.pdf", b"old pdf", mtime=eight_days_ago)

    with patch("kalanjiyam.utils.storage.get_storage", return_value=storage):
        with flask_app.app_context():
            # Disabled config -> skips unless force is True
            flask_app.config["AUTO_UPLOADED_FILES_CLEANUP"] = False
            res = cleanup_uploaded_files_task(days=7, force=False)
            assert res == 0
            assert storage.exists("projects/old/pdf/source.pdf")

            # Enabled config -> runs cleanup
            flask_app.config["AUTO_UPLOADED_FILES_CLEANUP"] = True
            res = cleanup_uploaded_files_task(days=7, force=False)
            assert res == 1
            assert not storage.exists("projects/old/pdf/source.pdf")


def test_system_settings_auto_cleanup_days(flask_app):
    from kalanjiyam import queries as q
    with flask_app.app_context():
        settings = q.get_system_settings()
        assert hasattr(settings, "auto_cleanup_days")
        assert settings.auto_cleanup_days == 7
        settings.auto_cleanup_days = 14
        assert settings.auto_cleanup_days == 14
