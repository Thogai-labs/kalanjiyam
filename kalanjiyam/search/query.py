"""Building search queries and shaping the results for the UI.

Two result shapes are supported and both come from the same query body:

- **flat** -- one hit per manuscript page, paginated.
- **grouped** -- hits collapsed by project, with the top matching pages of
  each book nested inside, so a reader sees "3 books, 41 pages" rather than
  41 undifferentiated rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kalanjiyam.search import acl, schema
from kalanjiyam.search.client import get_client, get_settings

LOG = logging.getLogger(__name__)

#: Deep pagination past this point needs `search_after`; OpenSearch refuses
#: `from + size` beyond its `max_result_window`.
MAX_RESULT_WINDOW = 10_000

#: Pages nested under each book in the grouped view.
PAGES_PER_BOOK = 3

_FIELDS = [
    "content^1",
    "content_en^1.5",
    "project_title^3",
    "project_author^2",
]

VIEW_GROUPED = "grouped"
VIEW_FLAT = "flat"
VIEWS = (VIEW_GROUPED, VIEW_FLAT)


class QuerySyntaxError(ValueError):
    """The user's advanced query could not be parsed."""


@dataclass
class SearchRequest:
    """Everything the UI can ask for, parsed from the query string."""

    q: str
    view: str = VIEW_GROUPED
    page: int = 1
    per_page: int = 20
    advanced: bool = False
    #: Facet selections.
    project_id: int | None = None
    author: str | None = None
    lang: str | None = None
    genre: str | None = None

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def has_filters(self) -> bool:
        return any([self.project_id, self.author, self.lang, self.genre])


@dataclass
class PageHit:
    project_slug: str
    project_title: str
    project_author: str | None
    page_slug: str
    page_order: int | None
    lang: str | None
    snippets: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class BookGroup:
    project_id: int
    project_slug: str
    project_title: str
    project_author: str | None
    page_count: int
    pages: list[PageHit] = field(default_factory=list)
    score: float = 0.0


@dataclass
class SearchResults:
    total_pages: int = 0
    total_books: int = 0
    #: Whether ``total_pages`` is exact or a lower bound.
    total_is_exact: bool = True
    hits: list[PageHit] = field(default_factory=list)
    groups: list[BookGroup] = field(default_factory=list)
    facets: dict[str, list[dict]] = field(default_factory=dict)
    took_ms: int = 0
    error: str | None = None


# Query construction
# ------------------


def build_query(request: SearchRequest, scope: acl.SearchScope) -> dict:
    """Build the ``query`` clause: what to match, and what the user may see."""
    if request.advanced:
        # Full Lucene syntax: field:value, ~fuzzy, ^boost, ranges, booleans.
        matcher = {
            "query_string": {
                "query": request.q,
                "fields": _FIELDS,
                "default_operator": "AND",
                "lenient": True,
            }
        }
    else:
        matcher = {
            "bool": {
                "should": [
                    {
                        "simple_query_string": {
                            "query": request.q,
                            "fields": _FIELDS,
                            "default_operator": "AND",
                        }
                    },
                    # A low-boost trigram clause so OCR noise and spelling
                    # variants still surface, without drowning exact matches.
                    {
                        "match": {
                            "content.trigram": {
                                "query": request.q,
                                "boost": 0.1,
                                "minimum_should_match": "80%",
                            }
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }

    filters = list(scope.filters)
    if request.project_id:
        filters.append({"term": {"project_id": request.project_id}})
    if request.author:
        filters.append({"term": {"project_author.raw": request.author}})
    if request.lang:
        filters.append({"term": {"lang": request.lang}})
    if request.genre:
        filters.append({"term": {"genre": request.genre}})

    return {"bool": {"must": [matcher], "filter": filters}}


def _highlight() -> dict:
    return {
        "pre_tags": ["<mark>"],
        "post_tags": ["</mark>"],
        # Snippets are rendered with |safe so the <mark> tags survive, so the
        # page text they wrap MUST be escaped first. Page content is proofer-
        # editable; without this, `<script>` in a manuscript transcription
        # would execute on the results page.
        "encoder": "html",
        "fields": {
            "content": {
                "type": "unified",
                "fragment_size": 160,
                "number_of_fragments": 3,
                "no_match_size": 0,
            }
        },
    }


def _aggregations() -> dict:
    return {
        "books": {
            "terms": {"field": "project_id", "size": 20},
            "aggs": {
                "title": {"terms": {"field": "project_title.raw", "size": 1}}
            },
        },
        "authors": {"terms": {"field": "project_author.raw", "size": 15}},
        "languages": {"terms": {"field": "lang", "size": 15}},
        "genres": {"terms": {"field": "genre", "size": 15}},
        "book_count": {"cardinality": {"field": "project_id"}},
    }


def build_body(request: SearchRequest, scope: acl.SearchScope) -> dict:
    body: dict[str, Any] = {
        "query": build_query(request, scope),
        "highlight": _highlight(),
        "aggs": _aggregations(),
        "track_total_hits": MAX_RESULT_WINDOW,
        "_source": [
            "project_id",
            "project_slug",
            "project_title",
            "project_author",
            "page_slug",
            "page_order",
            "lang",
        ],
        "from": request.offset,
        "size": request.per_page,
    }

    if request.view == VIEW_GROUPED:
        # One row per book, with its best-matching pages nested inside.
        body["collapse"] = {
            "field": "project_id",
            "inner_hits": {
                "name": "pages",
                "size": PAGES_PER_BOOK,
                "highlight": _highlight(),
                "_source": ["page_slug", "page_order", "lang"],
            },
        }
    return body


# Result parsing
# --------------


def _snippets(hit: dict) -> list[str]:
    return list((hit.get("highlight") or {}).get("content") or [])


def _page_hit(hit: dict, *, parent: dict | None = None) -> PageHit:
    source = hit.get("_source") or {}
    parent_source = (parent or {}).get("_source") or {}
    return PageHit(
        project_slug=source.get("project_slug") or parent_source.get("project_slug", ""),
        project_title=source.get("project_title") or parent_source.get("project_title", ""),
        project_author=source.get("project_author") or parent_source.get("project_author"),
        page_slug=source.get("page_slug", ""),
        page_order=source.get("page_order"),
        lang=source.get("lang"),
        snippets=_snippets(hit),
        score=hit.get("_score") or 0.0,
    )


def _facets(aggs: dict) -> dict[str, list[dict]]:
    def buckets(name, label_of=None):
        raw = (aggs.get(name) or {}).get("buckets") or []
        out = []
        for b in raw:
            label = label_of(b) if label_of else b["key"]
            out.append({"key": b["key"], "label": label, "count": b["doc_count"]})
        return out

    def book_label(bucket):
        titles = (bucket.get("title") or {}).get("buckets") or []
        return titles[0]["key"] if titles else str(bucket["key"])

    return {
        "books": buckets("books", book_label),
        "authors": buckets("authors"),
        "languages": buckets("languages"),
        "genres": buckets("genres"),
    }


def parse_response(response: dict, request: SearchRequest) -> SearchResults:
    hits = response.get("hits") or {}
    total = hits.get("total") or {}
    aggs = response.get("aggregations") or {}

    results = SearchResults(
        total_pages=total.get("value", 0),
        total_is_exact=total.get("relation", "eq") == "eq",
        total_books=(aggs.get("book_count") or {}).get("value", 0),
        facets=_facets(aggs),
        took_ms=response.get("took", 0),
    )

    rows = hits.get("hits") or []
    if request.view == VIEW_GROUPED:
        for row in rows:
            source = row.get("_source") or {}
            inner = (
                (row.get("inner_hits") or {}).get("pages", {}).get("hits", {})
            )
            nested = inner.get("hits") or []
            nested_total = (inner.get("total") or {}).get("value", len(nested))
            results.groups.append(
                BookGroup(
                    project_id=source.get("project_id"),
                    project_slug=source.get("project_slug", ""),
                    project_title=source.get("project_title", ""),
                    project_author=source.get("project_author"),
                    page_count=nested_total,
                    pages=[_page_hit(h, parent=row) for h in nested],
                    score=row.get("_score") or 0.0,
                )
            )
    else:
        results.hits = [_page_hit(h) for h in rows]

    return results


# Entry point
# -----------


def search(user, request: SearchRequest) -> SearchResults:
    """Run a search on the user's behalf.

    Returns a :class:`SearchResults` with ``error`` set rather than raising,
    so the view can render a banner instead of a 500.
    """
    if not request.q.strip():
        return SearchResults()

    settings = get_settings()
    scope = acl.search_scope(user, settings.index_prefix, schema.PAGES)

    if request.offset + request.per_page > MAX_RESULT_WINDOW:
        return SearchResults(
            error=(
                "That is further than search can page. Narrow the query with a "
                "filter or a more specific phrase."
            )
        )

    body = build_body(request, scope)
    try:
        client = get_client()
        response = client.search(
            index=scope.indices,
            body=body,
            ignore_unavailable=True,
            allow_no_indices=True,
        )
    except Exception as e:
        LOG.warning("Search failed for %r: %s", request.q, e)
        message = str(e)
        if request.advanced and (
            "parse_exception" in message or "query_shard_exception" in message
        ):
            return SearchResults(
                error="That advanced query could not be parsed. Check the syntax."
            )
        return SearchResults(error="Search is temporarily unavailable.")

    return parse_response(response, request)


def suggest(user, prefix_text: str, limit: int = 8) -> list[dict]:
    """Title completions for the home-page search box."""
    if not prefix_text.strip():
        return []

    settings = get_settings()
    scope = acl.search_scope(user, settings.index_prefix, schema.PROJECTS)
    body = {
        "size": limit,
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": prefix_text,
                            "fields": ["display_title^3", "author"],
                            "type": "bool_prefix",
                        }
                    }
                ],
                "filter": scope.filters,
            }
        },
        "_source": ["display_title", "author", "slug"],
    }
    try:
        response = get_client().search(
            index=scope.indices,
            body=body,
            ignore_unavailable=True,
            allow_no_indices=True,
        )
    except Exception as e:
        LOG.warning("Suggest failed for %r: %s", prefix_text, e)
        return []

    out = []
    for hit in (response.get("hits") or {}).get("hits") or []:
        source = hit.get("_source") or {}
        out.append(
            {
                "title": source.get("display_title", ""),
                "author": source.get("author"),
                "slug": source.get("slug"),
            }
        )
    return out
