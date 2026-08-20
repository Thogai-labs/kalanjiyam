import pytest
from datetime import datetime

import kalanjiyam.database as db
from kalanjiyam.enums import SiteRole
from kalanjiyam.queries import get_session
from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
from kalanjiyam.admin import (
    _format_source_size_bytes,
    _format_ocr_time_mins,
    _batch_ocr_summary_dict,
)

PLATFORM_BATCH_OCR = "/admin/platform/cli_batch_ocr"
ORG_BATCH_OCR = "/admin/org/cli_batch_ocr"


def test_format_helpers():
    # Size formatting
    assert _format_source_size_bytes(None) == "0 MB"
    assert _format_source_size_bytes(0) == "0 MB"
    assert _format_source_size_bytes(500) == "500 B"
    assert _format_source_size_bytes(2048) == "2.0 KB"
    assert _format_source_size_bytes(5 * 1024 * 1024) == "5.00 MB"
    assert _format_source_size_bytes(2 * 1024 * 1024 * 1024) == "2.00 GB"

    # Time formatting
    assert _format_ocr_time_mins(None) == "0.0 mins"
    assert _format_ocr_time_mins(0) == "0.0 mins"
    assert _format_ocr_time_mins(0.05) == "0.05 mins"
    assert _format_ocr_time_mins(12.34) == "12.3 mins"

    # Summary dict
    summary = _batch_ocr_summary_dict(
        total_ocr_ms=120000.0,  # 2 minutes = 120s = 120000ms
        total_pages=50,
        total_source_size_bytes=10 * 1024 * 1024,
        total_extraction_ms=60000.0,  # 1 minute
    )
    assert summary["total_ocr_time_mins"] == 2.0
    assert summary["total_ocr_time_formatted"] == "2.0 mins"
    assert summary["total_extract_ocr_time_mins"] == 3.0
    assert summary["total_extract_ocr_time_formatted"] == "3.0 mins"
    assert summary["total_pages"] == 50
    assert summary["total_pages_formatted"] == "50"
    assert summary["total_source_size_bytes"] == 10 * 1024 * 1024
    assert summary["total_source_size_formatted"] == "10.00 MB"


import uuid

@pytest.fixture()
def setup_batch_jobs_with_metrics(flask_app):
    with flask_app.app_context():
        session = get_session()
        uid = uuid.uuid4().hex[:8]

        # Org & Admin
        org = db.Group(name=f"KPI Test Org {uid}", slug=f"kpi-test-org-{uid}")
        session.add(org)
        session.flush()

        admin = db.User(username=f"kpi-org-admin-{uid}", email=f"kpi-{uid}@test.local")
        admin.set_password("pw")
        admin.organization_id = org.id
        session.add(admin)
        session.flush()
        role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).one()
        admin.roles.append(role)

        # Project
        board = db.Board(title=f"KPI Test Board {uid}")
        session.add(board)
        session.flush()

        project = db.Project(
            slug=f"kpi-test-proj-{uid}",
            display_title=f"KPI Test Project {uid}",
            board_id=board.id,
        )
        session.add(project)
        session.flush()
        session.add(db.ProjectGroups(project_id=project.id, group_id=org.id))
        session.flush()

        # Batch Job 1: 2 items, OCR
        job1 = BatchJob(
            target_uri=f"ui://project/kpi-test-proj-{uid}",
            job_type="UI_BATCH_OCR",
            status="COMPLETED",
            created_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        session.add(job1)
        session.flush()

        item1 = BatchItem(
            job_id=job1.id,
            project_id=project.id,
            file_path="doc1.pdf",
            total_pages=10,
            total_ocr_latency_ms=60000.0,  # 1 min
            extraction_latency_ms=30000.0,  # 0.5 min
            source_size_bytes=4 * 1024 * 1024,  # 4 MB
            status="COMPLETED",
        )
        item2 = BatchItem(
            job_id=job1.id,
            project_id=project.id,
            file_path="doc2.pdf",
            total_pages=15,
            total_ocr_latency_ms=120000.0,  # 2 mins
            extraction_latency_ms=30000.0,  # 0.5 min
            source_size_bytes=6 * 1024 * 1024,  # 6 MB
            status="COMPLETED",
        )
        session.add_all([item1, item2])
        session.flush()

        # Add page records for item1
        p1 = BatchOcrPage(
            batch_item_id=item1.id,
            page_number=1,
            ocr_latency_ms=30000.0,
            status="COMPLETED",
        )
        p2 = BatchOcrPage(
            batch_item_id=item1.id,
            page_number=2,
            ocr_latency_ms=30000.0,
            status="COMPLETED",
        )
        session.add_all([p1, p2])

        session.commit()
        return {
            "org_id": org.id,
            "admin_id": admin.id,
            "project_id": project.id,
            "job1_id": job1.id,
        }


def test_platform_batch_ocr_kpi_cards(superadmin_client, setup_batch_jobs_with_metrics):
    resp = superadmin_client.get(PLATFORM_BATCH_OCR)
    assert resp.status_code == 200
    html = resp.text

    # Verify KPI card labels
    assert "Total OCR Time" in html
    assert "Total Extraction + OCR Time" in html
    assert "Total Pages" in html
    assert "Total Source Size" in html

    # Total OCR time: 60000 + 120000 ms = 180000 ms = 3.0 mins
    assert "3.0 mins" in html
    # Total Extract + OCR time: 180000 + 60000 ms = 240000 ms = 4.0 mins
    assert "4.0 mins" in html
    # Total pages: 10 + 15 = 25 pages
    assert "25" in html
    # Total source size: 4MB + 6MB = 10MB
    assert "10.00 MB" in html


def test_platform_batch_ocr_single_job_kpi_cards(superadmin_client, setup_batch_jobs_with_metrics):
    job_id = setup_batch_jobs_with_metrics["job1_id"]
    resp = superadmin_client.get(f"{PLATFORM_BATCH_OCR}/{job_id}")
    assert resp.status_code == 200
    html = resp.text

    # Verify KPI card labels on single job details page
    assert "Total OCR Time" in html
    assert "Total Extraction + OCR Time" in html
    assert "Total Pages" in html
    assert "Total Source Size" in html
    assert "3.0 mins" in html
    assert "4.0 mins" in html
    assert "25" in html
    assert "10.00 MB" in html


def test_org_batch_ocr_kpi_cards(flask_app, setup_batch_jobs_with_metrics):
    admin_id = setup_batch_jobs_with_metrics["admin_id"]
    session = get_session()
    org_client = flask_app.test_client(user=session.query(db.User).get(admin_id))

    resp = org_client.get(ORG_BATCH_OCR)
    assert resp.status_code == 200
    html = resp.text

    assert "Total OCR Time" in html
    assert "Total Extraction + OCR Time" in html
    assert "Total Pages" in html
    assert "Total Source Size" in html
    assert "3.0 mins" in html
    assert "4.0 mins" in html
    assert "25" in html
    assert "10.00 MB" in html

    # Test single job view for org admin
    job_id = setup_batch_jobs_with_metrics["job1_id"]
    resp_job = org_client.get(f"{ORG_BATCH_OCR}/{job_id}")
    assert resp_job.status_code == 200
    assert "Total OCR Time" in resp_job.text
    assert "Total Extraction + OCR Time" in resp_job.text
    assert "Total Pages" in resp_job.text
    assert "Total Source Size" in resp_job.text
    assert "3.0 mins" in resp_job.text
    assert "4.0 mins" in resp_job.text
    assert "25" in resp_job.text
    assert "10.00 MB" in resp_job.text
