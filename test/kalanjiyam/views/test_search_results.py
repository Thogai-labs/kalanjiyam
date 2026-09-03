"""Rendering of real result sets.

SEARCH_ENABLED is off in tests, so the results template would otherwise never
be exercised. These stub the search call and assert the page renders both
views, the facets, and the pagination correctly.
"""

import pytest

from kalanjiyam.search import query as search_query
from kalanjiyam.views import search as search_view


def _hit(page_slug="12", snippet="a <mark>நோய்</mark> b", is_public=True):
    return search_query.PageHit(
        project_slug="siddha-fixture",
        project_title="Siddha Fixture",
        project_author="Agastyar",
        page_slug=page_slug,
        page_order=int(page_slug),
        lang="ta",
        snippets=[snippet],
        score=3.0,
        is_public=is_public,
    )


def _results(view, is_public=True):
    results = search_query.SearchResults(
        total_pages=41,
        total_books=2,
        took_ms=7,
        facets={
            "books": [{"key": 1, "label": "Siddha Fixture", "count": 30}],
            "authors": [{"key": "Agastyar", "label": "Agastyar", "count": 30}],
            "languages": [{"key": "ta", "label": "ta", "count": 41}],
            "genres": [],
        },
    )
    if view == "grouped":
        results.groups = [
            search_query.BookGroup(
                project_id=1,
                project_slug="siddha-fixture",
                project_title="Siddha Fixture",
                project_author="Agastyar",
                page_count=9,
                is_public=is_public,
                pages=[
                    _hit("12", is_public=is_public),
                    _hit("13", is_public=is_public),
                ],
            )
        ]
    else:
        results.hits = [
            _hit("12", is_public=is_public),
            _hit("13", is_public=is_public),
        ]
    return results


@pytest.fixture()
def with_results(monkeypatch):
    """Pretend search is on and returns a fixed result set."""

    def install(view="grouped", results=None, is_public=True):
        monkeypatch.setattr(search_view, "is_enabled", lambda: True)
        monkeypatch.setattr(
            search_view.search_query,
            "search",
            lambda user, req: (
                results
                if results is not None
                else _results(req.view, is_public=is_public)
            ),
        )

    return install


def test_grouped_view_renders_books_and_snippets(client, with_results):
    with_results()
    resp = client.get("/search/?q=நோய்&view=grouped")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Siddha Fixture" in body
    assert "Agastyar" in body
    # Highlight markup from OpenSearch must survive to the page.
    assert "<mark>" in body
    # Deep link points at the reader, carrying the query for in-page highlight.
    assert "/books/siddha-fixture/12/?q=" in body
    # The book has more matches than are nested, so offer the drill-down.
    assert "See all 9 matching pages" in body


def test_flat_view_renders_one_row_per_page(client, with_results):
    with_results()
    resp = client.get("/search/?q=நோய்&view=flat")

    body = resp.data.decode()
    assert resp.status_code == 200
    assert "/books/siddha-fixture/12/?q=" in body
    assert "/books/siddha-fixture/13/?q=" in body
    assert "See all" not in body


def test_facets_link_back_into_the_search(client, with_results):
    with_results()
    body = client.get("/search/?q=நோய்").data.decode()

    assert "author=Agastyar" in body
    assert "lang=ta" in body


def test_active_facet_offers_removal(client, with_results):
    with_results()
    body = client.get("/search/?q=நோய்&author=Agastyar").data.decode()

    # The chip is shown...
    assert "Filtered by:" in body
    # ...and the facet link now clears the filter rather than re-applying it.
    assert "author=Agastyar" not in body.split("Filtered by:")[1].split("</div>")[0]


def test_view_toggle_preserves_the_query(client, with_results):
    with_results()
    body = client.get("/search/?q=நோய்&author=Agastyar").data.decode()

    assert "view=flat" in body
    assert "author=Agastyar" in body


def test_pagination_appears_only_when_there_is_more(client, with_results):
    with_results()
    # Two results on a 20-per-page setting: no next link.
    body = client.get("/search/?q=நோய்&view=flat").data.decode()
    assert "page=2" not in body

    # Page 2 always offers a way back.
    body = client.get("/search/?q=நோய்&view=flat&page=2").data.decode()
    assert "Previous" in body


def test_numbered_pagination_appears_when_multiple_pages_exist(client, with_results):
    """When a search returns hits equal to per_page, numbered pagination buttons appear."""
    many_hits = [_hit(str(i), is_public=True) for i in range(1, 21)]
    results = search_query.SearchResults(
        total_pages=45,
        total_books=5,
        hits=many_hits,
    )
    with_results(results=results)

    body = client.get("/search/?q=நோய்&view=flat").data.decode()
    assert "page=2" in body
    assert "Next" in body
    assert "google-page-btn" in body
    assert 'aria-current="page"' in body



def test_error_from_the_engine_shows_the_banner(client, with_results):
    with_results(results=search_query.SearchResults(error="Search is temporarily unavailable."))
    resp = client.get("/search/?q=நோய்")

    assert resp.status_code == 200
    assert b"Full-text search is unavailable" in resp.data


def test_no_matches_renders_an_empty_state(client, with_results):
    with_results(results=search_query.SearchResults())
    body = client.get("/search/?q=நோய்").data.decode()

    assert "No pages matched that search." in body


# Where results link
# ------------------
#
# /books/ aborts 404 on a book that is not publicly viewable, so a result for
# such a book has to point into /proofing/ instead. Search has already
# established the viewer may see it.


def test_non_public_books_link_into_proofing(client, with_results):
    with_results(is_public=False)
    body = client.get("/search/?q=நோய்&view=grouped").data.decode()

    assert "/proofing/siddha-fixture/" in body
    assert "/books/siddha-fixture/" not in body


def test_non_public_pages_link_into_proofing(client, with_results):
    with_results(is_public=False)
    body = client.get("/search/?q=நோய்&view=flat").data.decode()

    assert "/proofing/siddha-fixture/12/" in body
    assert "/books/siddha-fixture/12/" not in body


def test_public_books_still_link_to_the_reader(client, with_results):
    """Anonymous visitors searching public books must not be sent to proofing."""
    with_results(is_public=True)

    grouped = client.get("/search/?q=நோய்&view=grouped").data.decode()
    assert "/books/siddha-fixture/" in grouped
    assert "/proofing/siddha-fixture/" not in grouped

    flat = client.get("/search/?q=நோய்&view=flat").data.decode()
    assert "/books/siddha-fixture/12/" in flat
    assert "/proofing/siddha-fixture/12/" not in flat
