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
        page = db.Page(project_id=project.id, slug="test-ver-page", order=99, status_id=1)
        session.add(page)
        session.commit()
        
        try:
            # 1. Add revision to 'role:p1' track
            v1 = add_revision(
                page,
                summary="P1 revision",
                content="P1 Content",
                status="reviewed-0",
                version=0,
                author_id=None,
                version_key="role:p1"
            )
            assert v1 == 1
            
            # Verify version record exists
            pv_p1 = session.query(db.PageVersion).filter_by(page_id=page.id, version_key="role:p1").first()
            assert pv_p1 is not None
            assert pv_p1.version == 1
            assert len(pv_p1.revisions) == 1
            assert pv_p1.revisions[0].content == "P1 Content"
            
            # 2. Add revision to 'role:p2' track
            v2 = add_revision(
                page,
                summary="P2 revision",
                content="P2 Content",
                status="reviewed-0",
                version=0,
                author_id=None,
                version_key="role:p2"
            )
            assert v2 == 1
            
            pv_p2 = session.query(db.PageVersion).filter_by(page_id=page.id, version_key="role:p2").first()
            assert pv_p2 is not None
            assert pv_p2.version == 1
            
            # 3. Add second revision to 'role:p1'
            v1_next = add_revision(
                page,
                summary="P1 revision 2",
                content="P1 Content 2",
                status="reviewed-0",
                version=1,
                author_id=None,
                version_key="role:p1"
            )
            assert v1_next == 2
            
            session.refresh(pv_p1)
            assert pv_p1.version == 2
            assert len(pv_p1.revisions) == 2
            assert pv_p1.revisions[1].content == "P1 Content 2"
            
        finally:
            # Clean up
            session.delete(page)
            session.commit()


def test_page_version_optimistic_locking(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-opt-lock", order=100, status_id=1)
        session.add(page)
        session.commit()
        
        try:
            # Add initial revision
            add_revision(
                page,
                summary="P1 init",
                content="Content",
                status="reviewed-0",
                version=0,
                author_id=None,
                version_key="role:p1"
            )
            
            # Edit conflict: trying to edit with old expected version (0 instead of 1)
            with pytest.raises(EditError):
                add_revision(
                    page,
                    summary="Conflict edit",
                    content="New content",
                    status="reviewed-0",
                    version=0,
                    author_id=None,
                    version_key="role:p1"
                )
        finally:
            session.delete(page)
            session.commit()


def test_role_based_fallback_resolution(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-fallbacks", order=101, status_id=1)
        session.add(page)
        session.commit()
        
        # Query users
        u_p1 = session.query(db.User).filter_by(username="u-basic").first() # Has P1 & P2
        u_mod = session.query(db.User).filter_by(username="u-moderator").first() # Has P1, P2, moderator
        
        try:
            # Case 1: No versions exist. Should fallback to target_key
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "role:p2" # basic user is P2
            assert active_key == "role:p2"
            
            # Case 2: Only 'ocr:chandra' exists
            pv_ocr = db.PageVersion(page_id=page.id, version_key="ocr:chandra", version=1)
            session.add(pv_ocr)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "role:p2"
            assert active_key == "ocr:chandra" # fallback to ocr:chandra since no role tracks exist
            
            # Case 3: 'role:p1' track exists
            pv_p1 = db.PageVersion(page_id=page.id, version_key="role:p1", version=1)
            session.add(pv_p1)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "role:p2"
            assert active_key == "role:p1" # fallback to p1 instead of ocr:chandra
            
            # Case 4: moderator user accesses
            target_key, active_key = resolve_version_keys(u_mod, page)
            assert target_key == "role:moderator"
            assert active_key == "role:p1" # still fallback to p1 (the highest available)
            
            # Case 5: 'role:p2' track is created
            pv_p2 = db.PageVersion(page_id=page.id, version_key="role:p2", version=1)
            session.add(pv_p2)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_mod, page)
            assert target_key == "role:moderator"
            assert active_key == "role:p2" # fallback to p2 now
            
        finally:
            session.delete(page)
            session.commit()


def test_get_version_display_name():
    assert "Consolidated P1 Version" in str(get_version_display_name("role:p1"))
    assert "Consolidated P2 Version" in str(get_version_display_name("role:p2"))
    assert "Consolidated Moderator Version" in str(get_version_display_name("role:moderator"))
    assert "Chandra OCR" in str(get_version_display_name("ocr:chandra"))
