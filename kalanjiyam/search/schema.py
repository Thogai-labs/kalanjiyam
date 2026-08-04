"""Index names, analyzers, and mappings.

Naming
------

Search targets are *aliases*; the data lives in *store* indices behind them::

    kalanjiyam-pages-org-3          (alias)
      -> kalanjiyam-store-pages-org-3-v7   (concrete index)

The two namespaces are deliberately disjoint. Searches expand the wildcard
``kalanjiyam-pages-org-*``, which matches only aliases -- so a half-built
``-v8`` index is invisible to readers until the alias is swapped onto it.

Analysis
--------

The corpus spans English plus ~22 mostly Indic languages. OpenSearch ships no
Tamil, Telugu, Malayalam, or Kannada analyzer, so per-language analyzers are
not an option for the primary text field. Instead there is one script-agnostic
chain built on the ICU plugin: ``icu_tokenizer`` finds correct word boundaries
in every script, ``icu_normalizer``/``icu_folding`` normalize width, case, and
diacritics, and ``indic_normalization`` folds the Indic-script spelling
variants that OCR output is full of.

English gets an extra stemmed field (``content_en``), which the indexer
populates only for English pages -- the one language where stemming clearly
pays off.
"""

from __future__ import annotations

#: Document kinds. Each gets its own index per organization.
PAGES = "pages"
PROJECTS = "projects"
DOC_KINDS = (PAGES, PROJECTS)

_ANALYZER = "kalanjiyam_multi"
_TRIGRAM_ANALYZER = "kalanjiyam_trigram"


# Index naming
# ------------


def alias(prefix: str, kind: str, group_id: int) -> str:
    """Name of the alias that searches and writes target."""
    _check_kind(kind)
    return f"{prefix}-{kind}-org-{group_id}"


def store_index(prefix: str, kind: str, group_id: int, version: int) -> str:
    """Name of the concrete index that an alias points at."""
    _check_kind(kind)
    return f"{prefix}-store-{kind}-org-{group_id}-v{version}"


def store_pattern(prefix: str, kind: str, group_id: int) -> str:
    """Wildcard matching every version of one org's store index."""
    _check_kind(kind)
    return f"{prefix}-store-{kind}-org-{group_id}-v*"


def search_pattern(prefix: str, kind: str) -> str:
    """Wildcard matching every org's alias for this document kind.

    Matches aliases only, never store indices, so in-progress rebuilds stay
    invisible to searches.
    """
    _check_kind(kind)
    return f"{prefix}-{kind}-org-*"


def parse_alias(prefix: str, name: str) -> tuple[str, int] | None:
    """Inverse of :func:`alias`. Returns ``(kind, group_id)`` or ``None``."""
    for kind in DOC_KINDS:
        head = f"{prefix}-{kind}-org-"
        if name.startswith(head):
            suffix = name[len(head) :]
            if suffix.isdigit():
                return kind, int(suffix)
    return None


def parse_store_index(prefix: str, name: str) -> tuple[str, int, int] | None:
    """Return ``(kind, group_id, version)`` for a store index name."""
    for kind in DOC_KINDS:
        head = f"{prefix}-store-{kind}-org-"
        if not name.startswith(head):
            continue
        suffix = name[len(head) :]
        group, _, version = suffix.partition("-v")
        if group.isdigit() and version.isdigit():
            return kind, int(group), int(version)
    return None


def _check_kind(kind: str) -> None:
    if kind not in DOC_KINDS:
        raise ValueError(f"Unknown document kind: {kind!r}")


# Settings and mappings
# ---------------------


def analysis_settings() -> dict:
    """Analyzer definitions shared by both document kinds."""
    return {
        "char_filter": {
            "kalanjiyam_icu_normalizer": {
                "type": "icu_normalizer",
                "name": "nfkc_cf",
                "mode": "compose",
            }
        },
        "tokenizer": {
            "kalanjiyam_trigram_tokenizer": {
                "type": "ngram",
                "min_gram": 3,
                "max_gram": 3,
                "token_chars": ["letter", "digit"],
            }
        },
        "filter": {"kalanjiyam_icu_folding": {"type": "icu_folding"}},
        "analyzer": {
            _ANALYZER: {
                "type": "custom",
                "char_filter": ["kalanjiyam_icu_normalizer"],
                "tokenizer": "icu_tokenizer",
                "filter": [
                    "indic_normalization",
                    "lowercase",
                    "kalanjiyam_icu_folding",
                ],
            },
            # Used only at index time on content.trigram; queries hit it
            # through the parent field, so noisy 3-gram query expansion
            # stays under our control.
            _TRIGRAM_ANALYZER: {
                "type": "custom",
                "char_filter": ["kalanjiyam_icu_normalizer"],
                "tokenizer": "kalanjiyam_trigram_tokenizer",
                "filter": ["lowercase"],
            },
        },
    }


def _text(analyzer: str = _ANALYZER, **extra) -> dict:
    field = {"type": "text", "analyzer": analyzer}
    field.update(extra)
    return field


def _sortable_text() -> dict:
    """Analyzed for matching, with a ``.raw`` keyword for facets and display."""
    return _text(fields={"raw": {"type": "keyword", "ignore_above": 256}})


#: Fields every document carries, used to enforce visibility at query time.
#: ``group_ids`` is the primary boundary; ``is_public`` is what lets anonymous
#: readers search at all; ``creator_id`` is stored so a per-creator filter can
#: be switched on later without a reindex.
def _access_fields() -> dict:
    return {
        "group_ids": {"type": "integer"},
        "is_public": {"type": "boolean"},
        "creator_id": {"type": "integer"},
    }


def page_mappings() -> dict:
    return {
        "dynamic": "strict",
        "properties": {
            "content": _text(
                fields={"trigram": {"type": "text", "analyzer": _TRIGRAM_ANALYZER}}
            ),
            "content_en": _text(analyzer="english"),
            "lang": {"type": "keyword"},
            "page_id": {"type": "integer"},
            "page_slug": {"type": "keyword"},
            "page_order": {"type": "integer"},
            "project_id": {"type": "integer"},
            "project_slug": {"type": "keyword"},
            "project_title": _sortable_text(),
            "project_author": _sortable_text(),
            "genre": {"type": "keyword"},
            "revision_id": {"type": "integer"},
            "revision_created": {"type": "date"},
            "status": {"type": "keyword"},
            "indexed_at": {"type": "date"},
            **_access_fields(),
        },
    }


def project_mappings() -> dict:
    return {
        "dynamic": "strict",
        "properties": {
            "project_id": {"type": "integer"},
            "slug": {"type": "keyword"},
            "display_title": _text(
                fields={
                    "raw": {"type": "keyword", "ignore_above": 256},
                    "suggest": {"type": "completion"},
                }
            ),
            "print_title": _text(),
            "author": _sortable_text(),
            "editor": _text(),
            "publisher": _text(),
            "publication_year": {"type": "keyword"},
            "description": _text(),
            "genre": {"type": "keyword"},
            "page_count": {"type": "integer"},
            "ocr_page_count": {"type": "integer"},
            "created_at": {"type": "date"},
            "indexed_at": {"type": "date"},
            **_access_fields(),
        },
    }


MAPPINGS = {PAGES: page_mappings, PROJECTS: project_mappings}


def index_body(kind: str) -> dict:
    """Full create-index body for one document kind."""
    _check_kind(kind)
    return {
        "settings": {
            "index": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
                "refresh_interval": "5s",
            },
            "analysis": analysis_settings(),
        },
        "mappings": MAPPINGS[kind](),
    }


def page_doc_id(page_id: int) -> str:
    return f"page:{page_id}"


def project_doc_id(project_id: int) -> str:
    return f"project:{project_id}"
