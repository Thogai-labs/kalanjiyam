import pytest
import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.utils.revisions import add_revision, EditError
from kalanjiyam.views.proofing.page import resolve_version_keys, get_version_display_name

def test_page_version_creation_and_revisions(flask_app):
    with flask_app.app_context():
        session = get_session()
        
        # Setup temp page
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-ver-page-pytest", order=199, status_id=1)
        session.add(page)
        session.commit()
        
        try:
            # 1. Add revision to 'user:2' track (basic user)
            v1 = add_revision(
                page,
                summary="User 2 revision",
                content="User 2 Content",
                status="reviewed-0",
                version=0,
                author_id=None,
                version_key="user:2"
            )
            assert v1 == 1
            
            # Verify version record exists
            pv_u2 = session.query(db.PageVersion).filter_by(page_id=page.id, version_key="user:2").first()
            assert pv_u2 is not None
            assert pv_u2.version == 1
            assert len(pv_u2.revisions) == 1
            assert pv_u2.revisions[0].content == "User 2 Content"
            
            # 2. Add second revision to 'user:2'
            v1_next = add_revision(
                page,
                summary="User 2 revision 2",
                content="User 2 Content 2",
                status="reviewed-0",
                version=1,
                author_id=None,
                version_key="user:2"
            )
            assert v1_next == 2
            
            session.refresh(pv_u2)
            assert pv_u2.version == 2
            
        finally:
            # Clean up
            session.delete(page)
            session.commit()


def test_page_version_optimistic_locking(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-opt-lock-pytest", order=200, status_id=1)
        session.add(page)
        session.commit()
        
        try:
            add_revision(
                page,
                summary="User 2 init",
                content="Content",
                status="reviewed-0",
                version=0,
                author_id=None,
                version_key="user:2"
            )
            
            with pytest.raises(EditError):
                add_revision(
                    page,
                    summary="Conflict edit",
                    content="New content",
                    status="reviewed-0",
                    version=0,
                    author_id=None,
                    version_key="user:2"
                )
        finally:
            session.delete(page)
            session.commit()


def test_role_based_fallback_resolution(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-fallbacks-pytest", order=201, status_id=1)
        session.add(page)
        session.commit()
        
        u_p1 = session.query(db.User).filter_by(username="u-basic").first() # ID 2, P2/P1
        u_mod = session.query(db.User).filter_by(username="u-moderator").first() # ID 3, Moderator
        
        try:
            # Case 1: No versions exist.
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "user:2"
            assert active_key == "user:2"
            
            # Case 2: Only ocr:chandra exists
            pv_ocr = db.PageVersion(page_id=page.id, version_key="ocr:chandra", version=1)
            session.add(pv_ocr)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "user:2"
            assert active_key == "ocr:chandra"
            
            # Case 3: user:3 (Moderator user) track exists
            pv_u3 = db.PageVersion(page_id=page.id, version_key="user:3", version=1)
            session.add(pv_u3)
            session.commit()
            
            # P2 user (u_p1) should fall back to user:3 because Moderator is higher tier
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "user:2"
            assert active_key == "user:3"
        finally:
            session.delete(page)
            session.commit()


def test_get_version_display_name(flask_app):
    with flask_app.app_context():
        with flask_app.test_request_context():
            # ID 1 is seeded as admin in initialize_test_db()
            name_1 = get_version_display_name("user:1")
            assert "u-admin" in str(name_1)
            assert "Moderator" in str(name_1)

            # OCR engine formatting
            name_ocr = get_version_display_name("ocr:chandra")
            assert "OCR 6" in str(name_ocr)
            
            # Legacy display names
            name_legacy = get_version_display_name("role:p1")
            assert "Legacy Consolidated P1" in str(name_legacy)
