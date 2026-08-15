"""Tests for the archival extraction client.

Two properties are load-bearing: a write-locked tag can never leave in a request
or arrive in a result, and a malformed generation degrades to a failed window
rather than an exception.
"""

from kalanjiyam.utils import archival_taxonomy as at
from kalanjiyam.utils import metadata_client as mc


def _pages():
    return [
        {
            "page_slug": "61",
            "ocr_confidence": 0.94,
            "blocks": [
                {"id": "b1", "type": "heading", "reading_order": 1, "text": "Kalat"}
            ],
        }
    ]


# Request building
# ----------------


def test_build_request__defaults_to_the_extractable_tags():
    request = mc.build_request(
        unit_id="u", window_index=1, window_total=4, pages=_pages()
    )
    assert set(request["tags"]) == {t.code for t in at.extractable_tags()}
    assert set(request["tags"]).isdisjoint(at.WRITE_LOCKED)


def test_build_request__an_explicit_tag_list_cannot_widen_the_lock():
    request = mc.build_request(
        unit_id="u",
        window_index=1,
        window_total=1,
        pages=_pages(),
        tags=["ACCESS", "CUSTODIAL HISTORY", "TITLE"],
    )
    assert request["tags"] == ["TITLE"]


def test_build_request__carries_the_window_and_taxonomy_version():
    request = mc.build_request(
        unit_id="kalanjiyam:project/x", window_index=3, window_total=24, pages=_pages()
    )
    assert request["window"] == {"index": 3, "total": 24, "page_slugs": ["61"]}
    assert request["taxonomy_version"] == at.TAXONOMY_VERSION
    assert request["contract_version"] == mc.CONTRACT_VERSION


def test_build_request__preserves_a_null_ocr_confidence():
    """Confidence-blind engines send null; it must reach the service unchanged."""
    pages = [{"page_slug": "1", "ocr_confidence": None, "blocks": []}]
    request = mc.build_request(unit_id="u", window_index=1, window_total=1, pages=pages)
    assert request["pages"][0]["ocr_confidence"] is None


# Response parsing
# ----------------


def _payload(fields):
    return {
        "contract_version": "1.0",
        "status": "success",
        "engine": "kalanjiyam-archival",
        "model": {"name": "gemma-3-27b-it", "version": "1.0"},
        "taxonomy_version": at.TAXONOMY_VERSION,
        "chars_in": 100,
        "engine_latency_ms": 12.5,
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "fields": fields,
    }


def test_parse_response__reads_a_well_formed_payload():
    result = mc.parse_response(
        _payload(
            {
                "TITLE": {
                    "value": "Kalat file",
                    "confidence": 0.9,
                    "source": "record",
                    "evidence": [
                        {"page_slug": "61", "block_id": "b1", "quote": "Kalat"}
                    ],
                }
            }
        ),
        ["TITLE"],
    )
    assert result.ok
    assert result.fields["TITLE"]["value"] == "Kalat file"
    assert result.fields["TITLE"]["evidence"][0]["block_id"] == "b1"
    assert result.model_name == "gemma-3-27b-it"
    assert result.engine_latency_ms == 12.5


def test_parse_response__quarantines_write_locked_tags():
    result = mc.parse_response(
        _payload({"ACCESS": {"value": "Open"}, "TITLE": {"value": "x"}}), ["TITLE"]
    )
    assert "ACCESS" not in result.fields
    assert result.locked_tags_returned == ["ACCESS"]


def test_parse_response__quarantines_tags_that_were_not_requested():
    result = mc.parse_response(_payload({"PLACE": {"value": ["Kalat"]}}), ["TITLE"])
    assert result.fields == {}
    assert result.unknown_tags == ["PLACE"]


def test_parse_response__coerces_entity_shapes():
    """Models emit a bare string where a list of objects was specified."""
    result = mc.parse_response(_payload({"PLACE": {"value": "Kalat"}}), ["PLACE"])
    assert result.fields["PLACE"]["value"] == [{"label": "Kalat"}]


def test_parse_response__accepts_a_bare_value_without_the_wrapper():
    result = mc.parse_response(_payload({"TITLE": "Kalat file"}), ["TITLE"])
    assert result.fields["TITLE"]["value"] == "Kalat file"


def test_parse_response__empty_value_counts_as_declined():
    result = mc.parse_response(
        _payload({"TITLE": {"value": "   "}, "PLACE": {"value": []}}),
        ["TITLE", "PLACE"],
    )
    assert result.fields == {}


def test_parse_response__unstated_source_defaults_to_record():
    """The strict reading: an unlabelled value is subject to quote verification."""
    result = mc.parse_response(_payload({"TITLE": {"value": "x"}}), ["TITLE"])
    assert result.fields["TITLE"]["source"] == at.SOURCE_RECORD


def test_parse_response__keeps_a_declared_source():
    result = mc.parse_response(
        _payload({"SCOPE CONTENT": {"value": "summary", "source": "derived"}}),
        ["SCOPE CONTENT"],
    )
    assert result.fields["SCOPE CONTENT"]["source"] == at.SOURCE_DERIVED


def test_parse_response__normalises_alternate_evidence_spellings():
    result = mc.parse_response(
        _payload(
            {
                "TITLE": {
                    "value": "x",
                    "evidence": [{"page": "61", "block": "b1", "text": "Kalat"}],
                }
            }
        ),
        ["TITLE"],
    )
    span = result.fields["TITLE"]["evidence"][0]
    assert span == {"page_slug": "61", "block_id": "b1", "quote": "Kalat"}


def test_parse_response__drops_evidence_with_neither_page_nor_quote():
    result = mc.parse_response(
        _payload({"TITLE": {"value": "x", "evidence": [{"block_id": "b1"}]}}),
        ["TITLE"],
    )
    assert result.fields["TITLE"]["evidence"] == []


# Malformed payloads degrade rather than raise
# --------------------------------------------


def test_parse_response__missing_fields_object_is_a_failed_window():
    result = mc.parse_response({"status": "success"}, ["TITLE"])
    assert not result.ok
    assert "fields" in result.error


def test_parse_response__fields_of_the_wrong_type_is_a_failed_window():
    result = mc.parse_response(_payload(["TITLE"]), ["TITLE"])
    assert not result.ok


def test_parse_response__non_object_payload_is_a_failed_window():
    result = mc.parse_response("nope", ["TITLE"])
    assert not result.ok


def test_parse_response__truncated_field_object_does_not_raise():
    """A generation cut off mid-object still yields whatever parsed."""
    result = mc.parse_response(
        _payload({"TITLE": {"value": "x"}, "PLACE": None}), ["TITLE", "PLACE"]
    )
    assert result.ok
    assert set(result.fields) == {"TITLE"}


def test_parse_response__non_numeric_metrics_become_none():
    payload = _payload({"TITLE": {"value": "x"}})
    payload["engine_latency_ms"] = "fast"
    payload["chars_in"] = None
    result = mc.parse_response(payload, ["TITLE"])
    assert result.engine_latency_ms is None
    assert result.chars_in is None
