"""Tests for Master User role, multi-org assignment, project access, and search scope."""

from types import SimpleNamespace

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.admin_user import sync_user_org_and_roles
from kalanjiyam.enums import SiteRole
from kalanjiyam.search import acl
from kalanjiyam.tasks.projects import _add_project_to_database
from kalanjiyam.utils.org_access import (
    user_can_access_project,
    user_can_view_proofing_project,
    user_organization_ids,
)


def _make_org(session, slug: str, name: str) -> db.Group:
    org = db.Group(name=name, slug=slug)
    session.add(org)
    session.flush()
    return org


def _make_user(session, username: str, roles: list[str]) -> db.User:
    user = db.User(username=username, email=f"{username}@test.local")
    user.set_password("test-password")
    session.add(user)
    session.flush()
    for role_name in roles:
        role = session.query(db.Role).filter_by(name=role_name).one()
        user.roles.append(role)
    session.add(user)
    session.flush()
    return user


def test_master_user_role_and_mixin(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        master = _make_user(session, "master_test1", [SiteRole.MASTER_USER.value])
        regular = _make_user(session, "regular_test1", [SiteRole.P1.value])
        session.commit()

        assert master.is_master_user is True
        assert regular.is_master_user is False


def test_sync_user_org_and_roles_multi_org(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org1 = _make_org(session, "m-org-1", "Master Org 1")
        org2 = _make_org(session, "m-org-2", "Master Org 2")
        org3 = _make_org(session, "m-org-3", "Master Org 3")
        master_role = session.query(db.Role).filter_by(name=SiteRole.MASTER_USER.value).one()

        user = db.User(username="master_sync_test", email="master_sync@test.local")
        user.set_password("initial")
        session.add(user)
        session.flush()

        # Simulate form data with multiple orgs
        form = SimpleNamespace(
            password=SimpleNamespace(data="test-pass"),
            role_ids=SimpleNamespace(data=[master_role.id]),
            organization_pick=SimpleNamespace(data=org1.id),
            organization_ids=SimpleNamespace(data=[org1.id, org2.id]),
            organization_id=SimpleNamespace(data=None),
        )

        sync_user_org_and_roles(form, user, session, is_created=True)
        session.commit()

        # Verify UserGroups has both orgs
        user_groups = session.query(db.UserGroups).filter_by(user_id=user.id).all()
        assigned_org_ids = {ug.group_id for ug in user_groups}
        assert assigned_org_ids == {org1.id, org2.id}
        assert org3.id not in assigned_org_ids

        org_ids_resolved = user_organization_ids(user)
        assert org1.id in org_ids_resolved
        assert org2.id in org_ids_resolved


def test_master_user_project_creation_and_access(flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        org_a = _make_org(session, "target-org-a", "Target Org A")
        org_b = _make_org(session, "target-org-b", "Target Org B")
        org_c = _make_org(session, "target-org-c", "Target Org C")

        master_user = _make_user(session, "master_creator", [SiteRole.MASTER_USER.value])
        master_user.organization_id = org_a.id
        session.add(db.UserGroups(user_id=master_user.id, group_id=org_a.id))
        session.add(db.UserGroups(user_id=master_user.id, group_id=org_b.id))
        session.commit()

        # Create project targeted to org_b
        _add_project_to_database(
            display_title="Master Book In Org B",
            slug="master-book-b",
            num_pages=5,
            creator_id=master_user.id,
            require_org=True,
            org_slug=org_b.slug,
        )

        # Create project targeted to org_a
        _add_project_to_database(
            display_title="Master Book In Org A",
            slug="master-book-a",
            num_pages=5,
            creator_id=master_user.id,
            require_org=True,
            org_slug=org_a.slug,
        )

        # Create project in unassigned org_c
        _add_project_to_database(
            display_title="Master Book In Org C",
            slug="master-book-c",
            num_pages=5,
            creator_id=None,
            require_org=True,
            org_slug=org_c.slug,
        )

        proj_a = session.query(db.Project).filter_by(slug="master-book-a").one()
        proj_b = session.query(db.Project).filter_by(slug="master-book-b").one()
        proj_c = session.query(db.Project).filter_by(slug="master-book-c").one()

        # Check projects are assigned to the correct groups
        assert any(g.id == org_a.id for g in proj_a.groups)
        assert any(g.id == org_b.id for g in proj_b.groups)
        assert any(g.id == org_c.id for g in proj_c.groups)

        # Master user can access both assigned projects, but not unassigned org_c
        assert user_can_access_project(master_user, proj_a) is True
        assert user_can_access_project(master_user, proj_b) is True
        assert user_can_access_project(master_user, proj_c) is False
        assert user_can_view_proofing_project(master_user, proj_a) is True
        assert user_can_view_proofing_project(master_user, proj_b) is True
        assert user_can_view_proofing_project(master_user, proj_c) is False


def test_master_user_search_scope(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org_x = _make_org(session, "search-org-x", "Search Org X")
        org_y = _make_org(session, "search-org-y", "Search Org Y")

        master_user = _make_user(session, "master_searcher", [SiteRole.MASTER_USER.value])
        master_user.organization_id = org_x.id
        session.add(db.UserGroups(user_id=master_user.id, group_id=org_x.id))
        session.add(db.UserGroups(user_id=master_user.id, group_id=org_y.id))
        session.commit()

        scope = acl.search_scope(master_user, "kalanjiyam-test")
        assert scope.unrestricted is False

        # Should match both orgs in the should clause
        filter_should = scope.filters[0]["bool"]["should"]
        matching_terms = [clause for clause in filter_should if "terms" in clause]
        assert len(matching_terms) == 1
        assert sorted(matching_terms[0]["terms"]["group_ids"]) == sorted([org_x.id, org_y.id])


def test_seed_lookup_role_cleans_obsolete_and_adds_master_user(flask_app):
    from kalanjiyam.seed.lookup import role as seed_role

    with flask_app.app_context():
        session = q.get_session()
        # Artificially insert obsolete role
        obsolete_role = db.Role(name="obsolete_test_role")
        session.add(obsolete_role)
        session.commit()

        # Run role seed
        seed_role.run()

        # Obsolete role should be deleted and master_user present
        role_names = {r.name for r in session.query(db.Role).all()}
        assert "obsolete_test_role" not in role_names
        assert "master_user" in role_names
        assert "super_admin" in role_names
        assert "admin" not in role_names


def test_master_user_proofing_dashboard_org_filtering_and_tags(client, flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        org1 = _make_org(session, "dashboard-org-1", "Dashboard Org 1")
        org2 = _make_org(session, "dashboard-org-2", "Dashboard Org 2")

        master_user = _make_user(session, "master_dash_user", [SiteRole.MASTER_USER.value])
        master_user.organization_id = org1.id
        session.add(db.UserGroups(user_id=master_user.id, group_id=org1.id))
        session.add(db.UserGroups(user_id=master_user.id, group_id=org2.id))
        session.commit()

        _add_project_to_database(
            display_title="Project Alpha In Org 1",
            slug="proj-alpha-1",
            num_pages=3,
            creator_id=master_user.id,
            require_org=True,
            org_slug=org1.slug,
        )
        _add_project_to_database(
            display_title="Project Beta In Org 2",
            slug="proj-beta-2",
            num_pages=4,
            creator_id=master_user.id,
            require_org=True,
            org_slug=org2.slug,
        )
        _add_project_to_database(
            display_title="Project Gamma Guest",
            slug="proj-gamma-guest",
            num_pages=2,
            creator_id=None,
            fingerprint_id="test-fp-123",
            require_org=True,
            org_slug=org1.slug,
        )

        with client.session_transaction() as sess:
            sess["_user_id"] = str(master_user.id)
            sess["_fresh"] = True

        # Test index without org filter (shows projects and org badges, but not creator_mode badges)
        res = client.get("/proofing/")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "Project Alpha In Org 1" in html
        assert "Project Beta In Org 2" in html
        assert "Project Gamma Guest" in html
        assert "Dashboard Org 1" in html
        assert "Dashboard Org 2" in html
        assert "Enterprise" not in html
        assert "Unregistered" not in html

        # Test filter by org1
        res_org1 = client.get(f"/proofing/?org={org1.slug}")
        assert res_org1.status_code == 200
        html_org1 = res_org1.get_data(as_text=True)
        assert "Project Alpha In Org 1" in html_org1
        assert "Project Beta In Org 2" not in html_org1

        # Test filter by org2
        res_org2 = client.get(f"/proofing/?org={org2.slug}")
        assert res_org2.status_code == 200
        html_org2 = res_org2.get_data(as_text=True)
        assert "Project Beta In Org 2" in html_org2
        assert "Project Alpha In Org 1" not in html_org2


def test_master_user_metrics_access_and_restrictions(client, flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        org1 = _make_org(session, "metrics-org-1", "Metrics Org 1")
        org2 = _make_org(session, "metrics-org-2", "Metrics Org 2")
        other_org = _make_org(session, "metrics-other-org", "Other Org")

        master_user = _make_user(session, "master_metrics_user", [SiteRole.MASTER_USER.value])
        master_user.organization_id = org1.id
        session.add(db.UserGroups(user_id=master_user.id, group_id=org1.id))
        session.add(db.UserGroups(user_id=master_user.id, group_id=org2.id))
        session.commit()

        # Create batch jobs for org1 and other_org
        from kalanjiyam.models.batch import BatchJob, BatchItem

        job1 = BatchJob(
            target_uri="test/uri/job1",
            job_type="BATCH_OCR",
            status="COMPLETED",
        )
        job_other = BatchJob(
            target_uri="test/uri/job_other",
            job_type="BATCH_OCR",
            status="COMPLETED",
        )
        session.add(job1)
        session.add(job_other)
        session.flush()

        _add_project_to_database(
            display_title="Master Proj 1",
            slug="m-proj-1",
            num_pages=3,
            creator_id=master_user.id,
            require_org=True,
            org_slug=org1.slug,
        )
        _add_project_to_database(
            display_title="Other Proj",
            slug="m-proj-other",
            num_pages=3,
            creator_id=None,
            require_org=True,
            org_slug=other_org.slug,
        )

        proj1 = session.query(db.Project).filter_by(slug="m-proj-1").one()
        proj_other = session.query(db.Project).filter_by(slug="m-proj-other").one()

        item1 = BatchItem(
            job_id=job1.id,
            project_id=proj1.id,
            file_path="page1.pdf",
            total_pages=5,
            total_ocr_latency_ms=1200.0,
            status="COMPLETED",
        )
        item_other = BatchItem(
            job_id=job_other.id,
            project_id=proj_other.id,
            file_path="other_page.pdf",
            total_pages=5,
            total_ocr_latency_ms=1500.0,
            status="COMPLETED",
        )
        session.add(item1)
        session.add(item_other)
        session.commit()

        with client.session_transaction() as sess:
            sess["_user_id"] = str(master_user.id)
            sess["_fresh"] = True

        # 1. /admin/ should redirect to master metrics
        res_admin = client.get("/admin/")
        assert res_admin.status_code == 302
        assert "/admin/master_metrics/cli_batch_ocr" in res_admin.location

        # 2. /admin/master_metrics/cli_batch_ocr should list job1 but not job_other
        res_batch = client.get("/admin/master_metrics/cli_batch_ocr")
        assert res_batch.status_code == 200
        batch_html = res_batch.get_data(as_text=True)
        assert "OCR & Translation Metrics" in batch_html
        assert f"Job #{job1.id}" in batch_html
        assert f"Job #{job_other.id}" not in batch_html

        # 3. Job details for accessible job
        res_job1 = client.get(f"/admin/master_metrics/cli_batch_ocr/{job1.id}")
        assert res_job1.status_code == 200
        job1_html = res_job1.get_data(as_text=True)
        assert "page1.pdf" in job1_html
        assert "test/uri/job1" in job1_html

        # 4. Job details for unassigned job should be 403
        res_job_other = client.get(f"/admin/master_metrics/cli_batch_ocr/{job_other.id}")
        assert res_job_other.status_code == 403

        # 5. Metadata metrics access & CSV export
        res_meta = client.get("/admin/master_metrics/metadata_metrics")
        assert res_meta.status_code == 200
        assert "Metadata Extraction Metrics" in res_meta.get_data(as_text=True)

        res_meta_csv = client.get("/admin/master_metrics/metadata_metrics/export_csv")
        assert res_meta_csv.status_code == 200
        assert res_meta_csv.content_type.startswith("text/csv")

        # 6. Telemetry metrics & API
        res_telemetry = client.get("/admin/master_metrics/metrics")
        assert res_telemetry.status_code == 200
        telemetry_html = res_telemetry.get_data(as_text=True)
        assert "System Metrics & Logs" in telemetry_html
        assert "Clear Logs" not in telemetry_html  # Clear logs button hidden for master user

        res_api = client.get("/admin/master_metrics/metrics/api?tab=queues")
        assert res_api.status_code == 200
        assert res_api.is_json

        # 7. Restricted platform routes (Master user should NOT have full admin access)
        res_platform = client.get("/admin/platform/")
        assert res_platform.status_code in (403, 404)

        res_users = client.get("/admin/user/")
        assert res_users.status_code in (403, 404)

        res_groups = client.get("/admin/groups/")
        assert res_groups.status_code in (403, 404)




