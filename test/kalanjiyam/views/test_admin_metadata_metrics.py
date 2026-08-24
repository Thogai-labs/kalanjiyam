"""The archival extraction metrics dashboard.

The load-bearing assertions are the org-scoping ones. An extraction run carries
a document's title, dates and the names of everyone in it, so one org admin
seeing another org's runs is a content leak, not just an untidy list.
"""

from datetime import datetime

import pytest

import kalanjiyam.database as db
from kalanjiyam.enums import SiteRole
from kalanjiyam.queries import get_session
from kalanjiyam.utils import archival_taxonomy as at

PLATFORM = "/admin/platform/metadata_metrics"
ORG = "/admin/org/metadata_metrics"


@pytest.fixture()
def two_orgs_with_runs(flask_app):
    """Two organizations, each with an admin, a project, and one run."""
    with flask_app.app_context():
        session = get_session()
        made = {}
        for name in ("alpha", "beta"):
            org = session.query(db.Group).filter_by(slug=f"{name}-mdorg").first()
            if org is None:
                org = db.Group(name=name.title(), slug=f"{name}-mdorg")
                session.add(org)
                session.flush()

            admin = session.query(db.User).filter_by(username=f"{name}-mdadmin").first()
            if admin is None:
                admin = db.User(
                    username=f"{name}-mdadmin", email=f"{name}-md@test.local"
                )
                admin.set_password("pw")
                session.add(admin)
                session.flush()
                role = (
                    session.query(db.Role)
                    .filter_by(name=SiteRole.ORG_ADMIN.value)
                    .one()
                )
                admin.roles.append(role)
            admin.organization_id = org.id

            project = session.query(db.Project).filter_by(slug=f"{name}-mdbook").first()
            if project is None:
                board = db.Board(title=f"{name}-mdboard")
                session.add(board)
                session.flush()
                project = db.Project(
                    slug=f"{name}-mdbook",
                    display_title=f"{name.title()} Secret File",
                    board_id=board.id,
                )
                session.add(project)
                session.flush()
                session.add(db.ProjectGroups(project_id=project.id, group_id=org.id))
                session.flush()

            run = db.MetadataExtractionRun(
                project_id=project.id,
                status="COMPLETED",
                taxonomy_version=at.TAXONOMY_VERSION,
                model_name="gemma-3-27b-it",
                pages_total=10,
                pages_read=10,
                fields_filled=15,
                fields_total=22,
                total_prompt_tokens=1000,
                total_completion_tokens=200,
            )
            session.add(run)
            session.flush()
            session.commit()
            made[name] = {
                "org_id": org.id,
                "admin_id": admin.id,
                "project_id": project.id,
                "run_id": run.id,
            }
        return made


def _client_for(flask_app, user_id):
    session = get_session()
    return flask_app.test_client(user=session.query(db.User).get(user_id))


# Access
# ------


def test_platform_dashboard_requires_super_admin(client, rama_client):
    assert client.get(PLATFORM).status_code in (302, 404)
    assert rama_client.get(PLATFORM).status_code in (302, 404)


def test_super_admin_sees_every_run(superadmin_client, two_orgs_with_runs):
    resp = superadmin_client.get(PLATFORM)
    assert resp.status_code == 200
    assert "Alpha Secret File" in resp.text
    assert "Beta Secret File" in resp.text


def test_org_admin_sees_only_their_own_runs(flask_app, two_orgs_with_runs):
    """A run names the people in the document, so this is a content boundary."""
    alpha = _client_for(flask_app, two_orgs_with_runs["alpha"]["admin_id"])
    resp = alpha.get(ORG)

    assert resp.status_code == 200
    assert "Alpha Secret File" in resp.text
    assert "Beta Secret File" not in resp.text


def test_org_admin_cannot_reach_the_platform_dashboard(flask_app, two_orgs_with_runs):
    alpha = _client_for(flask_app, two_orgs_with_runs["alpha"]["admin_id"])
    assert alpha.get(PLATFORM).status_code in (302, 404)


def test_org_export_is_scoped_too(flask_app, two_orgs_with_runs):
    """The CSV is the easier boundary to forget, and leaks more per row."""
    alpha = _client_for(flask_app, two_orgs_with_runs["alpha"]["admin_id"])
    body = alpha.get(f"{ORG}/export_csv").text

    assert "alpha-mdbook" in body
    assert "beta-mdbook" not in body


# Content
# -------


def test_the_dashboard_reports_tokens_and_coverage(
    superadmin_client, two_orgs_with_runs
):
    resp = superadmin_client.get(PLATFORM)
    assert "gemma-3-27b-it" in resp.text
    assert "15" in resp.text  # fields filled
    assert "1000" in resp.text  # prompt tokens
    assert "Engine Time" in resp.text
    assert "Time Taken" in resp.text
    assert "Time / Win" in resp.text
    assert "Avg Engine Time" in resp.text
    assert "Avg Time Taken" in resp.text


def test_filtering_by_status_excludes_the_others(superadmin_client, two_orgs_with_runs):
    assert (
        "Alpha Secret File"
        in superadmin_client.get(f"{PLATFORM}?status=completed").text
    )
    assert (
        "Alpha Secret File"
        not in superadmin_client.get(f"{PLATFORM}?status=failed").text
    )


def test_searching_by_project_narrows_the_list(superadmin_client, two_orgs_with_runs):
    resp = superadmin_client.get(f"{PLATFORM}?q=alpha-mdbook")
    assert "Alpha Secret File" in resp.text
    assert "Beta Secret File" not in resp.text


def test_the_csv_carries_the_derived_token_rates(superadmin_client, two_orgs_with_runs):
    body = superadmin_client.get(f"{PLATFORM}/export_csv").text
    assert "Tokens / Page Read" in body
    assert "Total Tokens" in body
    assert "Time Taken (Sec)" in body
    assert "Avg Time / Window (Sec)" in body
    # 1200 tokens over 10 pages read.
    assert "120.0" in body


def test_an_empty_dashboard_says_so(superadmin_client):
    resp = superadmin_client.get(f"{PLATFORM}?status=failed&q=nothing-matches-this")
    assert resp.status_code == 200
    assert "No extraction runs match" in resp.text


def test_filtering_by_date_range(superadmin_client, flask_app, two_orgs_with_runs):
    with flask_app.app_context():
        session = get_session()
        run_jan = db.MetadataExtractionRun(
            project_id=two_orgs_with_runs["alpha"]["project_id"],
            status="COMPLETED",
            taxonomy_version=at.TAXONOMY_VERSION,
            model_name="gemma-3-27b-it",
            pages_total=10,
            pages_read=10,
            fields_filled=15,
            fields_total=22,
            total_prompt_tokens=1000,
            total_completion_tokens=200,
            created_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        run_jun = db.MetadataExtractionRun(
            project_id=two_orgs_with_runs["beta"]["project_id"],
            status="COMPLETED",
            taxonomy_version=at.TAXONOMY_VERSION,
            model_name="gemma-3-27b-it",
            pages_total=10,
            pages_read=10,
            fields_filled=15,
            fields_total=22,
            total_prompt_tokens=1000,
            total_completion_tokens=200,
            created_at=datetime(2025, 6, 15, 10, 0, 0),
        )
        session.add(run_jan)
        session.add(run_jun)
        session.commit()
        jan_id = run_jan.id
        jun_id = run_jun.id

    # Filter with start_date only
    resp = superadmin_client.get(f"{PLATFORM}?start_date=2025-06-01")
    assert f"#{jun_id}" in resp.text
    assert f"#{jan_id}" not in resp.text

    # Filter with end_date only
    resp = superadmin_client.get(f"{PLATFORM}?end_date=2025-02-01")
    assert f"#{jan_id}" in resp.text
    assert f"#{jun_id}" not in resp.text

    # Filter with both start_date and end_date
    resp = superadmin_client.get(
        f"{PLATFORM}?start_date=2025-06-01&end_date=2025-06-30"
    )
    assert f"#{jun_id}" in resp.text
    assert f"#{jan_id}" not in resp.text

    # Date range with no match
    resp = superadmin_client.get(
        f"{PLATFORM}?start_date=2020-01-01&end_date=2020-01-31"
    )
    assert f"#{jan_id}" not in resp.text
    assert f"#{jun_id}" not in resp.text
    assert "No extraction runs match" in resp.text


def test_csv_export_respects_date_range_and_filters(
    superadmin_client, flask_app, two_orgs_with_runs
):
    with flask_app.app_context():
        session = get_session()
        run_jan = db.MetadataExtractionRun(
            project_id=two_orgs_with_runs["alpha"]["project_id"],
            status="COMPLETED",
            taxonomy_version=at.TAXONOMY_VERSION,
            model_name="gemma-3-27b-it",
            pages_total=5,
            pages_read=5,
            fields_filled=10,
            fields_total=20,
            total_prompt_tokens=500,
            total_completion_tokens=100,
            created_at=datetime(2025, 1, 15, 10, 0, 0),
        )
        session.add(run_jan)
        session.commit()
        jan_id = run_jan.id

    body = superadmin_client.get(
        f"{PLATFORM}/export_csv?start_date=2025-01-01&end_date=2025-01-31"
    ).text
    assert f"{jan_id},alpha-mdbook" in body

    body_empty = superadmin_client.get(
        f"{PLATFORM}/export_csv?start_date=2020-01-01&end_date=2020-01-31"
    ).text
    assert f"{jan_id},alpha-mdbook" not in body_empty


def test_org_admin_date_range_filtering_is_scoped(
    flask_app, two_orgs_with_runs
):
    with flask_app.app_context():
        session = get_session()
        run_alpha_old = db.MetadataExtractionRun(
            project_id=two_orgs_with_runs["alpha"]["project_id"],
            status="COMPLETED",
            taxonomy_version=at.TAXONOMY_VERSION,
            model_name="gemma-3-27b-it",
            pages_total=10,
            pages_read=10,
            fields_filled=15,
            fields_total=22,
            created_at=datetime(2025, 3, 1, 10, 0, 0),
        )
        run_beta_old = db.MetadataExtractionRun(
            project_id=two_orgs_with_runs["beta"]["project_id"],
            status="COMPLETED",
            taxonomy_version=at.TAXONOMY_VERSION,
            model_name="gemma-3-27b-it",
            pages_total=10,
            pages_read=10,
            fields_filled=15,
            fields_total=22,
            created_at=datetime(2025, 3, 1, 10, 0, 0),
        )
        session.add(run_alpha_old)
        session.add(run_beta_old)
        session.commit()
        alpha_id = run_alpha_old.id
        beta_id = run_beta_old.id

    alpha = _client_for(flask_app, two_orgs_with_runs["alpha"]["admin_id"])
    resp = alpha.get(f"{ORG}?start_date=2025-02-01&end_date=2025-04-01")
    assert resp.status_code == 200
    assert f"#{alpha_id}" in resp.text
    assert f"#{beta_id}" not in resp.text

    csv_body = alpha.get(
        f"{ORG}/export_csv?start_date=2025-02-01&end_date=2025-04-01"
    ).text
    assert "alpha-mdbook" in csv_body
    assert "beta-mdbook" not in csv_body


def test_invalid_date_format_does_not_error(
    superadmin_client, two_orgs_with_runs
):
    resp = superadmin_client.get(
        f"{PLATFORM}?start_date=not-a-date&end_date=also-bad"
    )
    assert resp.status_code == 200
    assert "Alpha Secret File" in resp.text
