"""Tests for disabled unregistered (guest) access.

When ENABLE_GUEST_ACCESS is False:
- Unauthenticated (guest) users are blocked and redirected to /sign-in on any search or proofing route.
- Search and proofing navigation and forms are hidden on public pages (header, footer, home page).
- Authenticated users retain full access to search and proofing.
"""

import pytest


def test_guest_access_enabled_by_default(client):
    """When guest access is enabled, unregistered guests can view search and proofing."""
    resp_search = client.get("/search/")
    assert resp_search.status_code == 200

    resp_proofing = client.get("/proofing/")
    assert resp_proofing.status_code == 200

    resp_home = client.get("/")
    assert resp_home.status_code == 200
    assert b'action="/search/"' in resp_home.data
    assert b"/proofing/" in resp_home.data


def test_search_routes_redirect_unregistered_when_disabled(flask_app, client):
    """Unregistered user attempting to access search routes via URL gets redirected to /sign-in."""
    flask_app.config["ENABLE_GUEST_ACCESS"] = False
    try:
        # Search landing
        resp = client.get("/search/", follow_redirects=False)
        assert resp.status_code == 302
        assert "/sign-in" in resp.headers["Location"]

        # Search with query
        resp_q = client.get("/search/?q=siddha", follow_redirects=False)
        assert resp_q.status_code == 302
        assert "/sign-in" in resp_q.headers["Location"]

        # Search URL without trailing slash
        resp_noslash = client.get("/search", follow_redirects=False)
        assert resp_noslash.status_code in (301, 302, 308)
        if resp_noslash.status_code == 302:
            assert "/sign-in" in resp_noslash.headers["Location"]
        else:
            # If 308 redirect followed, hits /sign-in
            followed = client.get("/search", follow_redirects=True)
            assert followed.status_code == 200
            assert "/sign-in" in followed.request.path or b"Sign In" in followed.data

        # Search suggest
        resp_suggest = client.get("/search/suggest?q=test", follow_redirects=False)
        assert resp_suggest.status_code == 302
        assert "/sign-in" in resp_suggest.headers["Location"]
    finally:
        flask_app.config["ENABLE_GUEST_ACCESS"] = True


def test_proofing_routes_redirect_unregistered_when_disabled(flask_app, client):
    """Unregistered user attempting to access proofing routes via URL gets redirected to /sign-in."""
    flask_app.config["ENABLE_GUEST_ACCESS"] = False
    try:
        for url in (
            "/proofing/",
            "/proofing",
            "/proofing/create-project",
            "/proofing/help",
            "/proofing/help/complete-guide",
            "/proofing/recent-changes",
        ):
            resp = client.get(url, follow_redirects=False)
            assert resp.status_code in (301, 302, 308), f"Expected redirect for {url}"
            if resp.status_code == 302:
                assert "/sign-in" in resp.headers["Location"], f"Expected sign-in redirect for {url}"
            else:
                followed = client.get(url, follow_redirects=True)
                assert followed.status_code == 200
                assert "/sign-in" in followed.request.path or b"Sign In" in followed.data
    finally:
        flask_app.config["ENABLE_GUEST_ACCESS"] = True


def test_ui_elements_hidden_for_unregistered_when_disabled(flask_app, client):
    """Search and proofing links/forms must be hidden in home, header, and footer when guest access is disabled."""
    flask_app.config["ENABLE_GUEST_ACCESS"] = False
    try:
        resp = client.get("/")
        assert resp.status_code == 200

        # Search form on home page should NOT be present
        assert b'action="/search/"' not in resp.data

        # Proofreading quick action pill should NOT be present
        assert b"Proofreading Workspace" not in resp.data
        assert b"Upload Scans / PDF" not in resp.data

        # Header Search, Proofing, and Help links should NOT be present in navbar
        assert b'href="/search/"' not in resp.data
        assert b'href="/proofing/"' not in resp.data
        assert b'href="/proofing/help"' not in resp.data

        # Sign-in prompt/button is shown
        assert b"/sign-in" in resp.data
    finally:
        flask_app.config["ENABLE_GUEST_ACCESS"] = True


def test_authenticated_users_can_access_when_guest_disabled(flask_app, rama_client, moderator_client):
    """Registered / logged-in users still have full access when guest access is disabled."""
    flask_app.config["ENABLE_GUEST_ACCESS"] = False
    try:
        # Search route is accessible
        resp_search = rama_client.get("/search/")
        assert resp_search.status_code == 200

        # Proofing routes are accessible
        resp_proofing = rama_client.get("/proofing/")
        assert resp_proofing.status_code == 200

        # P1/P2 user cannot access create-project (redirected)
        resp_create = rama_client.get("/proofing/create-project")
        assert resp_create.status_code == 302

        # Moderator can access create-project
        resp_create_mod = moderator_client.get("/proofing/create-project")
        assert resp_create_mod.status_code == 200

        # Home page shows search and proofing for authenticated users
        resp_home = rama_client.get("/")
        assert resp_home.status_code == 200
        assert b'action="/search/"' in resp_home.data
        assert b'href="/search/"' in resp_home.data
        assert b'href="/proofing/"' in resp_home.data
    finally:
        flask_app.config["ENABLE_GUEST_ACCESS"] = True
