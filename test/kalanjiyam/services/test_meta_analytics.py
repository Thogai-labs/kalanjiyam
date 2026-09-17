import uuid
from datetime import datetime, timedelta
import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SitePageStatus, SiteRole
from kalanjiyam.services.meta_analytics import MetaAnalyticsService


def _setup_multi_tenant_data(session):
    """Seed multi-tenant organizations, projects, pages, revisions, and metrics."""
    uid = uuid.uuid4().hex[:8]
    # 1. Create two test organizations
    org1 = db.Group(
        name=f"Thogai Heritage {uid}",
        slug=f"thogai-heritage-{uid}",
        is_active=True,
        storage_used_bytes=150 * 1024 * 1024,
        storage_quota_bytes=200 * 1024 * 1024,  # 75% utilized -> Warning
        ocr_credits_used=45,
        ocr_credit_limit=100,
        translation_credits_used=1200,
        translation_credit_limit=5000,
    )
    org2 = db.Group(
        name=f"Ancient Tamil Sangam {uid}",
        slug=f"tamil-sangam-{uid}",
        is_active=True,
        storage_used_bytes=20 * 1024 * 1024,
        storage_quota_bytes=500 * 1024 * 1024,
        ocr_credits_used=10,
        ocr_credit_limit=500,
        translation_credits_used=0,
        translation_credit_limit=1000,
    )
    session.add_all([org1, org2])
    session.flush()

    # 2. Add users to org1
    user1 = db.User(username=f"proofer_{uid}", email=f"p1_{uid}@org1.local")
    user1.set_password("pass")
    session.add(user1)
    session.flush()
    session.add(db.UserGroups(user_id=user1.id, group_id=org1.id))

    # 3. Create Project in org1 with sensitive content
    board = db.Board(title=f"manuscript board {uid}")
    session.add(board)
    session.flush()

    proj1 = db.Project(
        slug=f"secret-manuscript-{uid}",
        display_title=f"Confidential Palm Leaf #{uid}",
        board_id=board.id,
        is_publicly_viewable=False,
    )
    session.add(proj1)
    session.flush()
    session.add(db.ProjectGroups(group_id=org1.id, project_id=proj1.id))

    # Pages
    r0 = session.query(db.PageStatus).filter_by(name=SitePageStatus.R0.value).one()
    r1 = session.query(db.PageStatus).filter_by(name=SitePageStatus.R1.value).one()
    r2 = session.query(db.PageStatus).filter_by(name=SitePageStatus.R2.value).one()

    secret_ocr = "CLASSIFIED_PALM_LEAF_OCR_CONTENT_XY123"
    secret_text = "HIGHLY_SENSITIVE_TEXT_PAYLOAD_ABC789"

    page1 = db.Page(
        project_id=proj1.id,
        slug="p1",
        order=1,
        status_id=r2.id,
        ocr_bounding_boxes=f"10 10 20 20 {secret_ocr}",
    )
    page2 = db.Page(
        project_id=proj1.id,
        slug="p2",
        order=2,
        status_id=r1.id,
    )
    page3 = db.Page(
        project_id=proj1.id,
        slug="p3",
        order=3,
        status_id=r0.id,
    )
    session.add_all([page1, page2, page3])
    session.flush()

    rev1 = db.Revision(
        project_id=proj1.id,
        page_id=page1.id,
        author_id=user1.id,
        status_id=r2.id,
        content=secret_text,
    )
    session.add(rev1)

    # 4. System metric logs
    err = db.SystemMetricLog(
        category="error",
        name="TaskFailureException",
        group_id=org1.id,
        status="FAILED",
        error_level="ERROR",
        error_message="Worker timeout during execution.",
    )
    session.add(err)

    # 5. Batch Job
    bj = db.BatchJob(
        target_uri="s3://bucket/test.pdf",
        status="COMPLETED",
        job_type="BATCH_OCR",
    )
    session.add(bj)
    session.flush()

    b_item = db.BatchItem(
        job_id=bj.id,
        file_path="/files/test.pdf",
        project_id=proj1.id,
        total_pages=15,
        status="COMPLETED",
    )
    session.add(b_item)
    session.commit()

    return {
        "org1": org1,
        "org2": org2,
        "proj1": proj1,
        "user1": user1,
        "secret_ocr": secret_ocr,
        "secret_text": secret_text,
    }


def test_meta_analytics_cluster_summary(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        data = _setup_multi_tenant_data(session)

        summary = MetaAnalyticsService.get_cluster_summary()
        assert summary["total_orgs"] >= 2
        assert summary["active_orgs"] >= 2
        assert summary["total_projects"] >= 1
        assert summary["total_pages"] >= 3
        assert summary["status_counts"]["r2"] >= 1
        assert summary["status_counts"]["r1"] >= 1
        assert summary["status_counts"]["r0"] >= 1
        assert summary["storage_used_bytes"] >= 170 * 1024 * 1024
        assert summary["ocr_credits_used"] >= 55


def test_meta_analytics_activity_matrix(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        data = _setup_multi_tenant_data(session)

        matrix = MetaAnalyticsService.get_org_activity_matrix(time_window_days=30)
        assert len(matrix) >= 2

        org1_row = next((r for r in matrix if r["org_id"] == data["org1"].id), None)
        assert org1_row is not None
        assert org1_row["name"] == data["org1"].name
        assert org1_row["members_count"] == 1
        assert org1_row["projects_count"] == 1
        assert org1_row["total_pages"] == 3
        assert org1_row["r2_count"] == 1
        assert org1_row["r1_count"] == 1
        assert org1_row["r0_count"] == 1
        assert org1_row["revisions_in_window"] >= 1
        assert org1_row["active_contributors"] == 1
        assert org1_row["storage_pct"] == 75.0
        assert org1_row["health"] in ("WARNING", "HEALTHY", "CRITICAL")


def test_meta_analytics_velocity_trends(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        _setup_multi_tenant_data(session)

        trends = MetaAnalyticsService.get_workflow_velocity_trends(days=7)
        assert len(trends["dates"]) == 7
        assert len(trends["revisions"]) == 7
        assert len(trends["errors"]) == 7
        assert trends["total_revisions_in_period"] >= 1


def test_meta_analytics_events_stream(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        _setup_multi_tenant_data(session)

        events = MetaAnalyticsService.get_meta_events_stream(limit=20)
        assert len(events) >= 1

        batch_events = MetaAnalyticsService.get_meta_events_stream(limit=20, event_type="BATCH")
        assert all(e["event_type"] == "BATCH" for e in batch_events)

        error_events = MetaAnalyticsService.get_meta_events_stream(limit=20, event_type="ERROR")
        assert all(e["event_type"] == "ERROR" for e in error_events)


def test_meta_analytics_org_profile(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        data = _setup_multi_tenant_data(session)

        profile = MetaAnalyticsService.get_org_meta_profile(data["org1"].id)
        assert profile is not None
        assert profile["org"]["name"] == data["org1"].name
        assert profile["summary"]["projects_count"] == 1
        assert profile["summary"]["total_pages"] == 3
        assert len(profile["projects"]) == 1
        assert profile["projects"][0]["display_title"] == data["proj1"].display_title
        assert len(profile["members"]) == 1
        assert profile["members"][0]["username"] == data["user1"].username


def test_meta_analytics_csv_export(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        data = _setup_multi_tenant_data(session)

        csv_str = MetaAnalyticsService.export_meta_metrics_csv(time_window_days=30)
        assert "Organization ID,Organization Name,Slug" in csv_str
        assert data["org1"].name in csv_str
        assert data["org2"].name in csv_str


def test_meta_analytics_access_control(flask_app, superadmin_client, moderator_client, rama_client, client):
    with flask_app.app_context():
        session = q.get_session()
        data = _setup_multi_tenant_data(session)
        org1_id = data["org1"].id

    # 1. Super Admin access: MUST succeed (200 OK)
    r_overview = superadmin_client.get("/admin/meta-analytics/")
    assert r_overview.status_code == 200
    assert "Cross-Organization Meta-Analytics" in r_overview.text

    r_org = superadmin_client.get(f"/admin/meta-analytics/org/{org1_id}")
    assert r_org.status_code == 200
    assert data["org1"].name in r_org.text

    r_api_vel = superadmin_client.get("/admin/meta-analytics/api/velocity?days=7")
    assert r_api_vel.status_code == 200
    assert "revisions" in r_api_vel.json

    r_api_ev = superadmin_client.get("/admin/meta-analytics/api/events")
    assert r_api_ev.status_code == 200
    assert "events" in r_api_ev.json

    r_export = superadmin_client.get("/admin/meta-analytics/export/csv")
    assert r_export.status_code == 200
    assert "text/csv" in r_export.headers["Content-Type"]

    # 2. Non-super admin roles (moderator, regular proofer, anonymous) MUST be blocked
    for blocked_client in (moderator_client, rama_client, client):
        r = blocked_client.get("/admin/meta-analytics/")
        assert r.status_code in (302, 404)

        r_org_blocked = blocked_client.get(f"/admin/meta-analytics/org/{org1_id}")
        assert r_org_blocked.status_code in (302, 404)

        r_api_blocked = blocked_client.get("/admin/meta-analytics/api/velocity")
        assert r_api_blocked.status_code in (302, 404)


def test_meta_analytics_zero_content_guarantee(flask_app, superadmin_client):
    """Verify that neither HTML pages nor API responses ever leak private document text, OCR text, or images."""
    with flask_app.app_context():
        session = q.get_session()
        data = _setup_multi_tenant_data(session)
        secret_ocr = data["secret_ocr"]
        secret_text = data["secret_text"]
        org1_id = data["org1"].id

    # Check overview page
    r_overview = superadmin_client.get("/admin/meta-analytics/")
    assert r_overview.status_code == 200
    assert secret_ocr not in r_overview.text
    assert secret_text not in r_overview.text
    assert "/page-image/" not in r_overview.text
    assert "/replica" not in r_overview.text

    # Check org detail page
    r_org = superadmin_client.get(f"/admin/meta-analytics/org/{org1_id}")
    assert r_org.status_code == 200
    # Project title and metadata are shown
    assert data["proj1"].display_title in r_org.text
    assert data["proj1"].slug in r_org.text
    # But ZERO document text or OCR
    assert secret_ocr not in r_org.text
    assert secret_text not in r_org.text
    assert "/page-image/" not in r_org.text
    assert "/replica" not in r_org.text
    assert "/editor" not in r_org.text

    # Check API responses
    r_api_vel = superadmin_client.get("/admin/meta-analytics/api/velocity?days=7")
    assert secret_ocr not in r_api_vel.text
    assert secret_text not in r_api_vel.text

    r_api_ev = superadmin_client.get("/admin/meta-analytics/api/events")
    assert secret_ocr not in r_api_ev.text
    assert secret_text not in r_api_ev.text

    r_export = superadmin_client.get("/admin/meta-analytics/export/csv")
    assert secret_ocr not in r_export.text
    assert secret_text not in r_export.text
