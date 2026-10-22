"""Tests for the project-wide structure extraction Celery task."""

from unittest.mock import MagicMock, patch

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.tasks import structure as structure_tasks


def test_doc_to_plain():
    doc = {
        "blocks": [
            {"text": "Line 1"},
            {"content": "Line 2"},
            {"other": "skip"},
        ]
    }
    assert "Line 1" in structure_tasks._doc_to_plain(doc)
    assert "Line 2" in structure_tasks._doc_to_plain(doc)


def test_extract_project_structure__not_found(flask_app):
    with patch.object(structure_tasks.extract_project_structure, "update_state"):
        res = structure_tasks.extract_project_structure.run(
            app_env=flask_app.config["KALANJIYAM_ENVIRONMENT"],
            project_slug="non-existent-project",
            user_id=1,
        )
    assert res["status"] == "error"
    assert "not found" in res["error"]


def test_extract_project_structure__success(flask_app):
    with flask_app.app_context():
        session = get_session()
        board = db.Board(title="board-structure-test")
        session.add(board)
        session.flush()

        project = db.Project(
            slug="test-proj-structure",
            display_title="Test Structure",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        status = session.query(db.PageStatus).filter_by(name="reviewed-0").first()
        page = db.Page(
            slug="page-1",
            project_id=project.id,
            order=1,
            status=status,
        )
        session.add(page)
        session.flush()

        # Add a default revision with OCR text
        pv = db.PageVersion(page_id=page.id, version_key="", version=1)
        session.add(pv)
        session.flush()

        rev = db.Revision(
            project_id=project.id,
            page_id=page.id,
            page_version_id=pv.id,
            content="Chapter 1: The Beginning page 1",
            content_format="plain",
            author_id=1,
            status_id=status.id,
        )
        session.add(rev)
        session.commit()

        mock_entries = [
            {
                "entry_id": "e1",
                "parent_entry_id": None,
                "level": 1,
                "entry_type": "chapter",
                "heading": "Chapter 1: The Beginning",
                "page_label_raw": "1",
                "needs_review": False,
            }
        ]

        with patch.object(structure_tasks.extract_project_structure, "update_state"), \
             patch("kalanjiyam.utils.structure_extract.extract", return_value=mock_entries):
            res = structure_tasks.extract_project_structure.run(
                app_env=flask_app.config["KALANJIYAM_ENVIRONMENT"],
                project_slug="test-proj-structure",
                user_id=1,
            )

        assert res["status"] == "done"
        assert res["pages_processed"] == 1
        assert res["entries_total"] == 1
        assert res["errors"] == []

        # Verify revision track created
        from kalanjiyam.utils.document_storage import load_revision_document

        saved_pv = (
            session.query(db.PageVersion)
            .filter_by(page_id=page.id, version_key="chat-structure:user:1")
            .first()
        )
        assert saved_pv is not None
        assert len(saved_pv.revisions) == 1
        rev = saved_pv.revisions[0]
        doc = load_revision_document(rev) or rev.document
        assert doc is not None
        assert doc["entries"] == mock_entries
