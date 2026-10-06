"""Tests for navigation bar order across different user roles."""

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole


def _get_navbar_html(html: str) -> str:
    """Extract <ul id="navbar">...</ul> section from html."""
    start = html.find('<ul id="navbar"')
    if start == -1:
        return ""
    end = html.find("</ul>", start)
    return html[start : end + 5]


def _get_mobile_nav_html(html: str) -> str:
    """Extract the mobile navigation <ul> section from html."""
    marker = '{{ _(\'Navigation\') }}'  # In rendered HTML, text is "Navigation"
    start_heading = html.find("Navigation")
    if start_heading == -1:
        return ""
    start_ul = html.find("<ul", start_heading)
    if start_ul == -1:
        return ""
    end_ul = html.find("</ul>", start_ul)
    return html[start_ul : end_ul + 5]


def test_nav_order_superadmin(superadmin_client):
    resp = superadmin_client.get("/")
    assert resp.status_code == 200
    html = resp.text

    desktop_nav = _get_navbar_html(html)
    assert desktop_nav != ""
    idx_dashboard = desktop_nav.find("/admin/platform/")
    assert idx_dashboard != -1, "Dashboard must appear in desktop navbar"
    # Library, Search, Proofing, and Help must be hidden from super admin
    assert "/books/" not in desktop_nav, "Library must be hidden for superadmin in desktop navbar"
    assert "/search/" not in desktop_nav, "Search must be hidden for superadmin in desktop navbar"
    assert "Help" not in desktop_nav, "Help must be hidden for superadmin in desktop navbar"
    assert "Proofing" not in desktop_nav, "Proofing must be hidden for superadmin in desktop navbar"

    mobile_nav = _get_mobile_nav_html(html)
    assert mobile_nav != ""
    m_idx_dashboard = mobile_nav.find("/admin/platform/")
    assert m_idx_dashboard != -1, "Dashboard must appear in mobile nav"
    assert "/books/" not in mobile_nav, "Library must be hidden for superadmin in mobile nav"
    assert "/search/" not in mobile_nav, "Search must be hidden for superadmin in mobile nav"
    assert "Help" not in mobile_nav, "Help must be hidden for superadmin in mobile nav"
    assert "Proofing" not in mobile_nav, "Proofing must be hidden for superadmin in mobile nav"

    # Tasks (badge, dropdown, section) must be hidden for superadmin
    assert 'title="Active Tasks"' not in html, "Active Tasks badge must be hidden for superadmin"
    assert "Background Tasks" not in html, "Background Tasks section must be hidden for superadmin"
    assert 'title="Tasks"' not in html, "Tasks dropdown must be hidden for superadmin"


def test_nav_order_org_admin(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = session.query(db.Group).filter_by(slug="nav-test-org").first()
        if not org:
            org = db.Group(name="Nav Test Org", slug="nav-test-org")
            session.add(org)
            session.flush()

        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        user = session.query(db.User).filter_by(username="u-orgadmin-navtest").first()
        if not user:
            user = db.User(
                username="u-orgadmin-navtest",
                email="orgadmin_navtest@test.local",
                organization_id=org.id,
            )
            user.set_password("pass123")
            user.roles.append(org_role)
            session.add(user)
            session.flush()
            session.add(db.UserGroups(user_id=user.id, group_id=org.id))
            session.commit()

        client = flask_app.test_client(user=user)
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text

        desktop_nav = _get_navbar_html(html)
        idx_dashboard = desktop_nav.find("/admin/org/")
        idx_search = desktop_nav.find("/search/")
        assert idx_dashboard != -1
        assert idx_search != -1
        assert idx_dashboard < idx_search, "Dashboard must appear before Search in desktop navbar"

        if "/books/" in desktop_nav:
            idx_books = desktop_nav.find("/books/")
            assert idx_dashboard < idx_books, "Dashboard must appear before Library in desktop navbar"

        mobile_nav = _get_mobile_nav_html(html)
        m_idx_dashboard = mobile_nav.find("/admin/org/")
        m_idx_search = mobile_nav.find("/search/")
        assert m_idx_dashboard != -1
        assert m_idx_search != -1
        assert m_idx_dashboard < m_idx_search, "Dashboard must appear before Search in mobile nav"

        # Verify Library/Search/Help/Tasks are retained for non-superadmin roles like org admin
        assert "Help" in desktop_nav
        assert "Background Tasks" in html
        assert 'title="Tasks"' in html


def test_nav_order_master_user(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        master_role = session.query(db.Role).filter_by(name=SiteRole.MASTER_USER.value).first()
        user = session.query(db.User).filter_by(username="u-master-navtest").first()
        if not user:
            user = db.User(
                username="u-master-navtest",
                email="master_navtest@test.local",
            )
            user.set_password("pass123")
            user.roles.append(master_role)
            session.add(user)
            session.commit()

        client = flask_app.test_client(user=user)
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text

        desktop_nav = _get_navbar_html(html)
        idx_metrics = desktop_nav.find("/admin/master_metrics/cli_batch_ocr")
        idx_search = desktop_nav.find("/search/")
        assert idx_metrics != -1
        assert idx_search != -1
        assert idx_metrics < idx_search, "Metrics must appear before Search in desktop navbar"

        mobile_nav = _get_mobile_nav_html(html)
        m_idx_metrics = mobile_nav.find("/admin/master_metrics/cli_batch_ocr")
        m_idx_search = mobile_nav.find("/search/")
        assert m_idx_metrics != -1
        assert m_idx_search != -1
        assert m_idx_metrics < m_idx_search, "Metrics must appear before Search in mobile nav"


def test_nav_order_meta_analyst(meta_analyst_client):
    resp = meta_analyst_client.get("/")
    assert resp.status_code == 200
    html = resp.text

    desktop_nav = _get_navbar_html(html)
    idx_meta = desktop_nav.find("/admin/meta-analytics/")
    idx_search = desktop_nav.find("/search/")
    assert idx_meta != -1
    assert idx_search != -1
    assert idx_meta < idx_search, "Meta-Analytics must appear before Search in desktop navbar"

    mobile_nav = _get_mobile_nav_html(html)
    m_idx_meta = mobile_nav.find("/admin/meta-analytics/")
    m_idx_search = mobile_nav.find("/search/")
    assert m_idx_meta != -1
    assert m_idx_search != -1
    assert m_idx_meta < m_idx_search, "Meta-Analytics must appear before Search in mobile nav"


def test_nav_order_admin(admin_client):
    # u-admin in test fixtures has super_admin role, so it behaves as superadmin
    resp = admin_client.get("/")
    assert resp.status_code == 200
    html = resp.text

    desktop_nav = _get_navbar_html(html)
    assert desktop_nav != ""
    assert "/admin/platform/" in desktop_nav
    assert "/search/" not in desktop_nav
    assert "/books/" not in desktop_nav
    assert "Help" not in desktop_nav
    assert "Proofing" not in desktop_nav

    mobile_nav = _get_mobile_nav_html(html)
    assert mobile_nav != ""
    assert "/admin/platform/" in mobile_nav
    assert "/search/" not in mobile_nav
    assert "/books/" not in mobile_nav
    assert "Help" not in mobile_nav
    assert "Proofing" not in mobile_nav

    assert 'title="Active Tasks"' not in html
    assert "Background Tasks" not in html
    assert 'title="Tasks"' not in html


def test_nav_order_guest_and_proofer_no_dashboard(client, rama_client):
    # Guest
    resp_guest = client.get("/")
    assert resp_guest.status_code == 200
    guest_desktop_nav = _get_navbar_html(resp_guest.text)
    assert "/admin/" not in guest_desktop_nav
    assert "Dashboard" not in guest_desktop_nav

    guest_mobile_nav = _get_mobile_nav_html(resp_guest.text)
    assert "/admin/" not in guest_mobile_nav

    # Basic proofer (p1/p2)
    resp_proofer = rama_client.get("/")
    assert resp_proofer.status_code == 200
    proofer_desktop_nav = _get_navbar_html(resp_proofer.text)
    assert "/admin/" not in proofer_desktop_nav
    assert "Dashboard" not in proofer_desktop_nav

    proofer_mobile_nav = _get_mobile_nav_html(resp_proofer.text)
    assert "/admin/" not in proofer_mobile_nav
