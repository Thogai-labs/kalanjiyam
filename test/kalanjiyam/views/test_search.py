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


def test_search_page_includes_alpine_and_debounce_directives(client):
    """Ensure Alpine.js reactivity and debounced input are present on empty and query states."""
    # Landing page (empty query)
    resp_empty = client.get("/search/")
    assert resp_empty.status_code == 200
    assert b"x-data" in resp_empty.data
    assert b"@input.debounce" in resp_empty.data
    assert b"fetchSuggestions()" in resp_empty.data
    assert b"suggestions" in resp_empty.data

    # Query SERP page (with query)
    resp_serp = client.get("/search/?q=siddha")
    assert resp_serp.status_code == 200
    assert b"x-data" in resp_serp.data
    assert b"@input.debounce" in resp_serp.data
    assert b"fetchSuggestions()" in resp_serp.data
    assert b"suggestions" in resp_serp.data


def test_search_page_includes_material_advanced_toggle(client):
    """Ensure Google Material UI switch for advanced syntax toggling is present."""
    # When advanced syntax is off
    resp_off = client.get("/search/?q=siddha")
    assert resp_off.status_code == 200
    assert b'role="switch"' in resp_off.data
    assert b'aria-checked="false"' in resp_off.data
    assert b"m3-switch" in resp_off.data
    assert b"advanced=1" in resp_off.data

    # When advanced syntax is on
    resp_on = client.get("/search/?q=siddha&advanced=1")
    assert resp_on.status_code == 200
    assert b'role="switch"' in resp_on.data
    assert b'aria-checked="true"' in resp_on.data
    assert b"m3-switch" in resp_on.data

