"""The public /search page.

SEARCH_ENABLED is false in the test config, so these exercise the degraded
path -- which is exactly the path that must never break the site.
"""


def test_search_page_renders_without_a_query(client):
    resp = client.get("/search/")
    assert resp.status_code == 200
    assert b"What are you looking for?" in resp.data


def test_search_falls_back_to_metadata_when_disabled(client):
    resp = client.get("/search/?q=test")
    assert resp.status_code == 200
    # The banner tells the reader page text is not being searched.
    assert b"Full-text search is unavailable" in resp.data


def test_search_handles_a_query_with_no_matches(client):
    resp = client.get("/search/?q=zzzznotathing")
    assert resp.status_code == 200
    assert b"No books matched" in resp.data


def test_view_toggle_is_accepted(client):
    for view in ("grouped", "flat"):
        assert client.get(f"/search/?q=test&view={view}").status_code == 200


def test_garbage_parameters_do_not_500(client):
    """Hand-edited URLs are a fact of life; none of these should crash."""
    for qs in (
        "?q=test&page=abc",
        "?q=test&page=-5",
        "?q=test&page=99999",
        "?q=test&view=nonsense",
        "?q=test&book=notanumber",
        "?q=test&advanced=1",
        "?q=%00",
    ):
        resp = client.get(f"/search/{qs}")
        assert resp.status_code == 200, qs


def test_suggest_returns_empty_json_when_disabled(client):
    resp = client.get("/search/suggest?q=sid")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_home_page_search_box_points_at_search(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b'action="/search/"' in resp.data
