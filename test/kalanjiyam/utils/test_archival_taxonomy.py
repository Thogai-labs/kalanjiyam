"""Tests for the archival description taxonomy.

The invariants here are the ones that protect the catalogue: the write-locked
tags must never reach the extraction service, and documents written under the
old short-tag codes must still resolve after the rename.
"""

from kalanjiyam.utils import archival_taxonomy as at

# Shape of the schema
# -------------------


def test_schema__is_the_clients_twenty_two_tags():
    assert len(at.TAGS) == 22
    assert len(at.BY_CODE) == 22


def test_schema__level_was_dropped_and_event_added():
    assert "LEVEL" not in at.BY_CODE
    assert "EVENT" in at.BY_CODE


def test_schema__places_are_one_tag():
    assert "PLACE" in at.BY_CODE
    assert "LOC.adm" not in at.BY_CODE
    assert "LOC.phys" not in at.BY_CODE


def test_schema__every_tag_has_a_renderable_kind():
    kinds = {at.KIND_TEXT, at.KIND_PROSE, at.KIND_ENTITIES, at.KIND_RELATIONS}
    for tag in at.TAGS:
        assert tag.kind in kinds, tag.code
        # Every kind must have a shape hint, or `build_prompt` would KeyError.
        assert tag.kind in at._SHAPE


def test_schema__groups_and_flat_view_agree():
    from_groups = [tag.code for _, tags in at.GROUPS for tag in tags]
    assert from_groups == [tag.code for tag in at.TAGS]


# The write lock
# --------------


def test_write_locked__are_real_tags():
    assert at.WRITE_LOCKED <= set(at.BY_CODE)


def test_extractable_tags__excludes_the_write_locked_ones():
    codes = {tag.code for tag in at.extractable_tags()}
    assert codes.isdisjoint(at.WRITE_LOCKED)
    assert len(codes) == len(at.TAGS) - len(at.WRITE_LOCKED)


def test_build_prompt__never_mentions_a_write_locked_tag():
    prompt = at.build_prompt()
    for code in at.WRITE_LOCKED:
        assert code not in prompt


def test_build_prompt__cannot_be_talked_into_a_write_locked_tag():
    """Asking for a locked tag by name must not unlock it."""
    prompt = at.build_prompt(codes=["ACCESS", "TITLE"])
    assert "ACCESS" not in prompt
    assert "TITLE" in prompt


def test_build_prompt__requires_evidence_for_recorded_values():
    prompt = at.build_prompt()
    assert "evidence" in prompt
    assert at.SOURCE_RECORD in prompt


# Legacy migration
# ----------------


def test_migrate_document__renames_old_codes():
    out = at.migrate_document({"REF": "IOR/R/1/34/17", "SUBJ": ["war funds"]})
    assert out["REFERENCE"] == "IOR/R/1/34/17"
    assert out["SUBJECT"] == ["war funds"]
    assert "REF" not in out


def test_migrate_document__merges_the_two_place_tags_and_keeps_kinds():
    out = at.migrate_document(
        {
            "LOC.adm": [{"label": "Kalat State"}],
            "LOC.phys": [{"label": "Camp Dhadar"}],
        }
    )
    assert [e["label"] for e in out["PLACE"]] == ["Kalat State", "Camp Dhadar"]
    assert [e["kind"] for e in out["PLACE"]] == [
        at.PLACE_ADMINISTRATIVE,
        at.PLACE_PHYSICAL,
    ]


def test_migrate_document__drops_tags_no_longer_in_the_schema():
    assert at.migrate_document({"LEVEL": "file"}) == {}


def test_migrate_document__current_codes_win_over_legacy_ones():
    out = at.migrate_document({"REF": "old", "REFERENCE": "new"})
    assert out["REFERENCE"] == "new"


def test_migrate_document__is_idempotent():
    once = at.migrate_document({"REF": "x", "LOC.phys": [{"label": "Camp"}]})
    assert at.migrate_document(once) == once


def test_migrate_document__tolerates_none_and_empty():
    assert at.migrate_document(None) == {}
    assert at.migrate_document({}) == {}


# Coverage and normalisation
# --------------------------


def test_coverage__sample_fills_the_whole_schema():
    result = at.coverage(at.SAMPLE)
    assert result["empty_codes"] == []
    assert result["filled"] == result["total"] == len(at.TAGS)


def test_coverage__counts_blanks_as_absent():
    result = at.coverage({"TITLE": "  ", "SUBJECT": [], "DATE": "1932"})
    assert result["filled_codes"] == ["DATE"]


def test_normalize_entities__accepts_the_shapes_models_actually_emit():
    assert at.normalize_entities("Kalat") == [{"label": "Kalat"}]
    assert at.normalize_entities(["Kalat"]) == [{"label": "Kalat"}]
    assert at.normalize_entities({"name": "Kalat"}) == [{"label": "Kalat"}]
    assert at.normalize_entities(None) == []


def test_normalize_entities__keeps_evidence_and_source():
    span = [{"page_slug": "3", "block_id": "b1", "quote": "Kalat"}]
    out = at.normalize_entities(
        [{"label": "Kalat", "source": at.SOURCE_RECORD, "evidence": span}]
    )
    assert out[0]["source"] == at.SOURCE_RECORD
    assert out[0]["evidence"] == span


def test_normalize_relations__carries_provenance_through():
    span = [{"page_slug": "9", "block_id": "b2", "quote": "sanctioned"}]
    out = at.normalize_relations(
        [
            {
                "from": "A",
                "relation": "sanctioned",
                "to": "B",
                "source": at.SOURCE_RECORD,
                "evidence": span,
            }
        ]
    )
    assert out == [
        {
            "subject": "A",
            "type": "sanctioned",
            "object": "B",
            "note": "",
            "source": at.SOURCE_RECORD,
            "evidence": span,
        }
    ]


def test_normalize_relations__drops_triples_with_no_endpoints():
    assert at.normalize_relations([{"type": "sanctioned"}]) == []


# The sample document
# -------------------


def test_sample__uses_only_current_codes():
    assert set(at.SAMPLE) <= set(at.BY_CODE)


def test_sample__enrichment_is_flagged_as_such():
    coords = [p for p in at.SAMPLE["PLACE"] if p["label"] == "Kalat House, Quetta"]
    assert coords[0]["source"] == at.SOURCE_ENRICHMENT
