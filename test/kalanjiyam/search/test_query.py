"""Query construction and response parsing.

No cluster is involved: these assert the shape of the request we send and the
shape of what we make of the reply.
"""

import pytest

from kalanjiyam.search import acl, query


@pytest.fixture()
def app_ctx(flask_app):
    with flask_app.app_context():
        yield flask_app


@pytest.fixture()
def scope():
    return acl.SearchScope(
        indices="kalanjiyam-pages-org-*",
        filters=[{"term": {"is_public": True}}],
    )


def _filters(body):
    return body["query"]["bool"]["filter"]


def test_scope_filters_are_always_applied(scope):
    """Visibility must ride along with every query, not just some of them."""
    for advanced in (False, True):
        req = query.SearchRequest(q="நோய்", advanced=advanced)
        body = query.build_body(req, scope)
        assert {"term": {"is_public": True}} in _filters(body)


def test_simple_query_uses_simple_query_string(scope):
    body = query.build_body(query.SearchRequest(q="agni"), scope)
    clauses = body["query"]["bool"]["must"][0]["bool"]["should"]

    kinds = {list(c)[0] for c in clauses}
    assert "simple_query_string" in kinds
    # Trigram clause is present but heavily de-boosted.
    trigram = next(c for c in clauses if list(c)[0] == "match")
    assert trigram["match"]["content.trigram"]["boost"] < 1


def test_advanced_query_uses_query_string(scope):
    req = query.SearchRequest(q='project_author:"Agastyar"', advanced=True)
    body = query.build_body(req, scope)
    matcher = body["query"]["bool"]["must"][0]

    assert "query_string" in matcher
    assert matcher["query_string"]["lenient"] is True


def test_facet_selections_become_filters(scope):
    req = query.SearchRequest(
        q="x", project_id=4, author="Agastyar", lang="ta", genre="medicine"
    )
    filters = _filters(query.build_body(req, scope))

    assert {"term": {"project_id": 4}} in filters
    assert {"term": {"project_author.raw": "Agastyar"}} in filters
    assert {"term": {"lang": "ta"}} in filters
    assert {"term": {"genre": "medicine"}} in filters


def test_grouped_view_collapses_by_project(scope):
    body = query.build_body(query.SearchRequest(q="x", view="grouped"), scope)

    assert body["collapse"]["field"] == "project_id"
    assert body["collapse"]["inner_hits"]["size"] == query.PAGES_PER_BOOK
    assert "highlight" in body["collapse"]["inner_hits"]


def test_flat_view_does_not_collapse(scope):
    body = query.build_body(query.SearchRequest(q="x", view="flat"), scope)
    assert "collapse" not in body


def test_pagination_offsets(scope):
    req = query.SearchRequest(q="x", page=3, per_page=20)
    body = query.build_body(req, scope)

    assert body["from"] == 40
    assert body["size"] == 20


def test_highlighting_uses_mark_tags(scope):
    body = query.build_body(query.SearchRequest(q="x"), scope)
    assert body["highlight"]["pre_tags"] == ["<mark>"]
    assert body["highlight"]["fields"]["content"]["number_of_fragments"] == 3


def test_highlight_snippets_are_html_encoded(scope):
    """Snippets render with |safe, so the engine must escape the page text.

    Manuscript transcriptions are proofer-editable; unescaped snippets would
    be stored XSS on the results page.
    """
    body = query.build_body(query.SearchRequest(q="x"), scope)
    assert body["highlight"]["encoder"] == "html"

    grouped = query.build_body(query.SearchRequest(q="x", view="grouped"), scope)
    assert grouped["collapse"]["inner_hits"]["highlight"]["encoder"] == "html"


def test_deep_pagination_is_refused_not_crashed(app_ctx):
    """Past the result window OpenSearch would 500; say so instead."""

    class Anon:
        is_authenticated = False
        organization_id = None

        def has_role(self, role):
            return False

    req = query.SearchRequest(q="x", page=999, per_page=20)
    results = query.search(Anon(), req)

    assert results.error
    assert results.hits == []


def test_empty_query_short_circuits(app_ctx):
    class Anon:
        is_authenticated = False
        organization_id = None

        def has_role(self, role):
            return False

    results = query.search(Anon(), query.SearchRequest(q="   "))
    assert results.total_pages == 0
    assert results.error is None


# Response parsing
# ----------------

FLAT_RESPONSE = {
    "took": 7,
    "hits": {
        "total": {"value": 2, "relation": "eq"},
        "hits": [
            {
                "_score": 3.2,
                "_source": {
                    "project_id": 1,
                    "project_slug": "siddha-fixture",
                    "project_title": "Siddha Fixture",
                    "project_author": "Agastyar",
                    "page_slug": "12",
                    "page_order": 12,
                    "lang": "ta",
                },
                "highlight": {"content": ["a <mark>நோய்</mark> b"]},
            }
        ],
    },
    "aggregations": {
        "book_count": {"value": 1},
        "books": {
            "buckets": [
                {
                    "key": 1,
                    "doc_count": 2,
                    "title": {"buckets": [{"key": "Siddha Fixture"}]},
                }
            ]
        },
        "authors": {"buckets": [{"key": "Agastyar", "doc_count": 2}]},
        "languages": {"buckets": [{"key": "ta", "doc_count": 2}]},
        "genres": {"buckets": []},
    },
}


def test_parse_flat_response():
    req = query.SearchRequest(q="நோய்", view="flat")
    results = query.parse_response(FLAT_RESPONSE, req)

    assert results.total_pages == 2
    assert results.total_books == 1
    assert results.took_ms == 7
    assert len(results.hits) == 1

    hit = results.hits[0]
    assert hit.project_slug == "siddha-fixture"
    assert hit.page_slug == "12"
    assert hit.snippets == ["a <mark>நோய்</mark> b"]


def test_parse_facets_uses_book_titles_not_ids():
    results = query.parse_response(FLAT_RESPONSE, query.SearchRequest(q="x", view="flat"))
    books = results.facets["books"]

    assert books[0]["key"] == 1
    assert books[0]["label"] == "Siddha Fixture"
    assert books[0]["count"] == 2


def test_parse_grouped_response():
    response = {
        "took": 4,
        "hits": {
            "total": {"value": 41, "relation": "eq"},
            "hits": [
                {
                    "_score": 5.0,
                    "_source": {
                        "project_id": 1,
                        "project_slug": "siddha-fixture",
                        "project_title": "Siddha Fixture",
                        "project_author": "Agastyar",
                    },
                    "inner_hits": {
                        "pages": {
                            "hits": {
                                "total": {"value": 9},
                                "hits": [
                                    {
                                        "_source": {"page_slug": "3", "page_order": 3},
                                        "highlight": {"content": ["<mark>x</mark>"]},
                                    }
                                ],
                            }
                        }
                    },
                }
            ],
        },
        "aggregations": {"book_count": {"value": 1}},
    }
    results = query.parse_response(response, query.SearchRequest(q="x", view="grouped"))

    assert len(results.groups) == 1
    group = results.groups[0]
    assert group.project_title == "Siddha Fixture"
    # The book has 9 matching pages; only the top ones are nested.
    assert group.page_count == 9
    assert len(group.pages) == 1
    # Nested hits inherit the book identity from their parent.
    assert group.pages[0].project_slug == "siddha-fixture"
    assert group.pages[0].snippets == ["<mark>x</mark>"]


def test_inexact_totals_are_flagged():
    response = {
        "hits": {"total": {"value": 10000, "relation": "gte"}, "hits": []},
        "aggregations": {},
    }
    results = query.parse_response(response, query.SearchRequest(q="x", view="flat"))
    assert results.total_is_exact is False
