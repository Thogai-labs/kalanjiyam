import pytest

from kalanjiyam.search import schema

PREFIX = "kalanjiyam"


def test_alias_and_store_names_are_disjoint():
    """A store index must never be matched by the search wildcard.

    This is what keeps a half-built rebuild invisible to readers.
    """
    alias = schema.alias(PREFIX, schema.PAGES, 3)
    store = schema.store_index(PREFIX, schema.PAGES, 3, 2)
    pattern = schema.search_pattern(PREFIX, schema.PAGES)

    assert alias == "kalanjiyam-pages-org-3"
    assert store == "kalanjiyam-store-pages-org-3-v2"

    head = pattern.rstrip("*")
    assert alias.startswith(head)
    assert not store.startswith(head)


def test_store_pattern_matches_every_version():
    pattern = schema.store_pattern(PREFIX, schema.PAGES, 3)
    head = pattern.rstrip("*")
    for version in (1, 2, 17):
        assert schema.store_index(PREFIX, schema.PAGES, 3, version).startswith(head)


def test_parse_alias_round_trip():
    for kind in schema.DOC_KINDS:
        name = schema.alias(PREFIX, kind, 42)
        assert schema.parse_alias(PREFIX, name) == (kind, 42)


def test_parse_alias_rejects_store_indices():
    """Otherwise a store index would be mistaken for a live org alias."""
    name = schema.store_index(PREFIX, schema.PAGES, 42, 1)
    assert schema.parse_alias(PREFIX, name) is None


def test_parse_store_index_round_trip():
    name = schema.store_index(PREFIX, schema.PROJECTS, 7, 12)
    assert schema.parse_store_index(PREFIX, name) == (schema.PROJECTS, 7, 12)


def test_parse_ignores_other_prefixes():
    assert schema.parse_alias(PREFIX, "other-pages-org-1") is None
    assert schema.parse_store_index(PREFIX, "other-store-pages-org-1-v1") is None


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        schema.alias(PREFIX, "chapters", 1)


def test_page_mapping_covers_every_access_field():
    """Visibility filtering reads these fields; a missing one would fail open."""
    props = schema.page_mappings()["properties"]
    for field in ("group_ids", "is_public", "creator_id"):
        assert field in props
    assert schema.project_mappings()["properties"]["group_ids"] == {"type": "integer"}


def test_mappings_are_strict():
    """Strict mappings turn a document-builder mistake into a loud failure."""
    assert schema.page_mappings()["dynamic"] == "strict"
    assert schema.project_mappings()["dynamic"] == "strict"


def test_content_has_a_trigram_subfield_for_ocr_noise():
    content = schema.page_mappings()["properties"]["content"]
    assert content["analyzer"] == "kalanjiyam_multi"
    assert content["fields"]["trigram"]["analyzer"] == "kalanjiyam_trigram"


def test_analyzer_is_script_agnostic():
    """One ICU chain has to serve English plus ~22 mostly Indic languages."""
    analysis = schema.analysis_settings()
    multi = analysis["analyzer"]["kalanjiyam_multi"]
    assert multi["tokenizer"] == "icu_tokenizer"
    assert "indic_normalization" in multi["filter"]
    assert "kalanjiyam_icu_folding" in multi["filter"]


def test_index_body_includes_settings_and_mappings():
    for kind in schema.DOC_KINDS:
        body = schema.index_body(kind)
        assert "analysis" in body["settings"]
        assert body["mappings"]["dynamic"] == "strict"


def test_doc_ids_are_namespaced():
    assert schema.page_doc_id(5) == "page:5"
    assert schema.project_doc_id(5) == "project:5"
