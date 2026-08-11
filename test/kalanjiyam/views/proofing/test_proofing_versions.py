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
            assert target_key == "main"
            assert active_key == "main"
            
            # Case 2: Only ocr:chandra exists
            pv_ocr = db.PageVersion(page_id=page.id, version_key="ocr:chandra", version=1)
            session.add(pv_ocr)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "main"
            assert active_key == "ocr:chandra"
            
            # Case 3: user:3 (Moderator user) track exists
            pv_u3 = db.PageVersion(page_id=page.id, version_key="user:3", version=1)
            session.add(pv_u3)
            session.commit()
            
            # P2 user (u_p1) should fall back to user:3 because Moderator is higher tier
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert target_key == "main"
            assert active_key == "user:3"
        finally:
            session.delete(page)
            session.commit()


def test_ocr_and_translation_fallback_resolution(flask_app):
    """Verify that when a page has both OCR and Translation tracks, Translation is preferred over OCR."""
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-ocr-tr-pytest", order=202, status_id=1)
        session.add(page)
        session.commit()
        
        u_p1 = session.query(db.User).filter_by(username="u-basic").first()
        
        try:
            # 1. OCR track exists
            pv_ocr = db.PageVersion(page_id=page.id, version_key="ocr:1", version=1)
            session.add(pv_ocr)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert active_key == "ocr:1"
            
            # 2. Batch Translation track added (translation: prefix)
            pv_tr = db.PageVersion(page_id=page.id, version_key="translation:google:sa->en", version=1)
            session.add(pv_tr)
            session.commit()
            
            # Translation track should take precedence over OCR track
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert active_key == "translation:google:sa->en"

            # 3. Legacy TR: translation track added
            session.delete(pv_tr)
            pv_tr_legacy = db.PageVersion(page_id=page.id, version_key="TR:google:sa->en", version=1)
            session.add(pv_tr_legacy)
            session.commit()
            
            target_key, active_key = resolve_version_keys(u_p1, page)
            assert active_key == "TR:google:sa->en"
        finally:
            session.delete(page)
            session.commit()


def test_timestamp_based_version_resolution(flask_app):
    """Verify that version fallback selects the most recently updated track unless user has own track."""
    import datetime
    with flask_app.app_context():
        session = get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        page = db.Page(project_id=project.id, slug="test-timestamp-res-pytest", order=203, status_id=1)
        session.add(page)
        session.commit()

        u_basic = session.query(db.User).filter_by(username="u-basic").first() # ID 2
        u_mod = session.query(db.User).filter_by(username="u-moderator").first() # ID 3
        u_other = session.query(db.User).filter_by(username="u-admin").first() # ID 4

        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            t1 = now - datetime.timedelta(hours=3)
            t2 = now - datetime.timedelta(hours=2)
            t3 = now - datetime.timedelta(hours=1)

            # 1. Moderator edit at t1
            pv_mod = db.PageVersion(page_id=page.id, version_key="user:3", version=1, updated_at=t1)
            session.add(pv_mod)
            session.commit()

            # 2. Translation run at t2 (newer than moderator edit)
            pv_tr = db.PageVersion(page_id=page.id, version_key="translation:google:sa->en", version=1, updated_at=t2)
            session.add(pv_tr)
            session.commit()

            # User4 (who hasn't edited yet) should see translation track because it's newer than moderator edit (t2 > t1)
            target_key, active_key = resolve_version_keys(u_other, page)
            assert active_key == "translation:google:sa->en"

            # 3. Another user edit at t3 (newer than translation)
            pv_u2 = db.PageVersion(page_id=page.id, version_key="user:2", version=1, updated_at=t3)
            session.add(pv_u2)
            session.commit()

            # User4 should now see user:2's edit because it's the newest track (t3 > t2)
            target_key, active_key = resolve_version_keys(u_other, page)
            assert active_key == "user:2"

            # User2 should see their own user:2 track
            target_key, active_key = resolve_version_keys(u_basic, page)
            assert active_key == "user:2"
            assert target_key == "main"
        finally:
            session.delete(page)
            session.commit()


def test_get_version_display_name(flask_app):
    with flask_app.app_context():
        with flask_app.test_request_context():
            # ID 4 is seeded as admin in initialize_test_db()
            name_4 = get_version_display_name("user:4")
            assert "u-admin" in str(name_4)
            assert "Moderator" in str(name_4)

            # OCR engine formatting
            name_ocr = get_version_display_name("ocr:chandra")
            assert "OCR 6" in str(name_ocr)
            
            # Legacy display names
            name_legacy = get_version_display_name("role:p1")
            assert "Legacy Consolidated P1" in str(name_legacy)

            # Translation display names
            name_tr = get_version_display_name("translation:google:sa->en")
            assert "Google" in str(name_tr)
            assert "SA → EN" in str(name_tr)

            name_tr_legacy = get_version_display_name("TR:google:sa->en")
            assert "Google" in str(name_tr_legacy)
            assert "SA → EN" in str(name_tr_legacy)
