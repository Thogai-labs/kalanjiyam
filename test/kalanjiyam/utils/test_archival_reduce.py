"""Tests for evidence verification and the window reduce.

The verification tests matter more than they look: three of the OCR engines in
service produce no confidence signal at all, so for documents they touch this is
the only objective quality measure there is.
"""

from kalanjiyam.utils import archival_reduce as ar
from kalanjiyam.utils import archival_taxonomy as at


def _pages():
    return [
        {
            "page_slug": "3",
            "ocr_confidence": 0.9,
            "blocks": [{"id": "b1", "text": "Lt. Shahzada Ahmad Yar Khan of Kalat"}],
        },
        {
            "page_slug": "4",
            "ocr_confidence": None,
            "blocks": [{"id": "b2", "text": "dated 11th March 1932"}],
        },
    ]


# Quote normalisation
# -------------------


def test_normalize_quote__folds_case_and_whitespace():
    assert ar.normalize_quote("  Ahmad   YAR  Khan\n") == "ahmad yar khan"


def test_normalize_quote__folds_unicode_composition():
    assert ar.normalize_quote("ﬁle") == ar.normalize_quote("file")


def test_normalize_quote__empty_is_empty():
    assert ar.normalize_quote("") == ""
    assert ar.normalize_quote(None) == ""


# Span verification
# -----------------


def test_verify_span__finds_a_real_quote():
    index = ar.build_page_index(_pages())
    span = {"page_slug": "3", "quote": "Shahzada Ahmad Yar Khan"}
    assert ar.verify_span(span, index) is True


def test_verify_span__rejects_an_invented_quote():
    index = ar.build_page_index(_pages())
    assert (
        ar.verify_span({"page_slug": "3", "quote": "Nobody Wrote This"}, index) is False
    )


def test_verify_span__quote_on_the_wrong_page_fails():
    """Right words, wrong page: the span is wrong about its own provenance."""
    index = ar.build_page_index(_pages())
    assert ar.verify_span({"page_slug": "4", "quote": "Kalat"}, index) is False


def test_verify_span__page_we_never_sent_fails():
    index = ar.build_page_index(_pages())
    assert ar.verify_span({"page_slug": "99", "quote": "Kalat"}, index) is False


def test_verify_span__no_quote_abstains():
    """A derived value cites pages, not words; there is nothing to check."""
    index = ar.build_page_index(_pages())
    assert ar.verify_span({"page_slug": "3"}, index) is None


def test_verify_span__without_a_slug_searches_the_window():
    index = ar.build_page_index(_pages())
    assert ar.verify_span({"quote": "11th March 1932"}, index) is True
    assert ar.verify_span({"quote": "not present"}, index) is False


# Field-level verification
# ------------------------


def test_verify_evidence__marks_each_entity_span():
    fields = {
        "PERSON NAME": {
            "confidence": 0.8,
            "source": at.SOURCE_RECORD,
            "value": [
                {
                    "label": "Ahmad Yar Khan",
                    "source": at.SOURCE_RECORD,
                    "evidence": [{"page_slug": "3", "quote": "Ahmad Yar Khan"}],
                },
                {
                    "label": "Invented Person",
                    "source": at.SOURCE_RECORD,
                    "evidence": [{"page_slug": "3", "quote": "Nobody Wrote This"}],
                },
            ],
        }
    }
    stats = ar.verify_evidence(fields, _pages())
    values = fields["PERSON NAME"]["value"]
    assert values[0]["evidence"][0]["verified"] is True
    assert values[1]["evidence"][0]["verified"] is False
    assert stats == {
        "evidence_spans": 2,
        "evidence_verified": 1,
        "unsupported_fields": 0,
    }


def test_verify_evidence__record_value_with_no_evidence_is_zeroed():
    """An uncited claim is not a low-confidence claim; it is an unsupported one."""
    fields = {
        "CREATOR": {"value": "A.G.G.", "confidence": 0.95, "source": at.SOURCE_RECORD}
    }
    stats = ar.verify_evidence(fields, _pages())
    assert fields["CREATOR"]["confidence"] == 0.0
    assert fields["CREATOR"]["unsupported"] is True
    assert stats["unsupported_fields"] == 1


def test_verify_evidence__all_quotes_fabricated_is_zeroed():
    fields = {
        "TITLE": {
            "value": "Invented",
            "confidence": 0.99,
            "source": at.SOURCE_RECORD,
            "evidence": [{"page_slug": "3", "quote": "never appears"}],
        }
    }
    ar.verify_evidence(fields, _pages())
    assert fields["TITLE"]["confidence"] == 0.0


def test_verify_evidence__derived_values_are_not_penalised():
    """A summary cannot quote one span, so it must not be scored as if it could."""
    fields = {
        "SCOPE CONTENT": {
            "value": "A summary.",
            "confidence": 0.7,
            "source": at.SOURCE_DERIVED,
            "evidence": [{"page_slug": "3"}],
        }
    }
    stats = ar.verify_evidence(fields, _pages())
    assert fields["SCOPE CONTENT"]["confidence"] == 0.7
    assert fields["SCOPE CONTENT"]["evidence"][0]["verified"] is None
    assert stats["unsupported_fields"] == 0


def test_verify_evidence__ignores_unknown_tags():
    fields = {"NOT A TAG": {"value": "x", "source": at.SOURCE_RECORD}}
    assert ar.verify_evidence(fields, _pages())["evidence_spans"] == 0


# Null-safe statistics
# --------------------


def test_mean__averages_only_present_values():
    assert ar.mean([0.8, None, 0.6]) == 0.7


def test_mean__all_missing_stays_none():
    """Never 0.0: a confidence-blind engine has no score, not a bad one."""
    assert ar.mean([None, None]) is None
    assert ar.mean([]) is None


def test_minimum_and_count_missing():
    assert ar.minimum([0.8, None, 0.6]) == 0.6
    assert ar.minimum([None]) is None
    assert ar.count_missing([0.8, None, None]) == 2


def test_window_metrics__reports_missing_ocr_confidence_separately():
    fields = {"TITLE": {"value": "x", "confidence": 0.5, "source": at.SOURCE_DERIVED}}
    metrics = ar.window_metrics(
        fields, _pages(), {"evidence_spans": 0, "evidence_verified": 0}
    )
    assert metrics["source_ocr_confidence"] == 0.9
    assert metrics["pages_without_confidence"] == 1
    assert metrics["low_conf_field_count"] == 1


def test_window_metrics__no_confidence_anywhere_is_none():
    pages = [{"page_slug": "1", "ocr_confidence": None, "blocks": [{"text": "x"}]}]
    metrics = ar.window_metrics({}, pages, {})
    assert metrics["source_ocr_confidence"] is None
    assert metrics["pages_without_confidence"] == 1


# Dates
# -----


def test_merge_dates__produces_an_inclusive_span():
    assert ar.merge_dates(["1932-03-11", "1933-01-27", "1921"]) == "1921/1933"


def test_merge_dates__single_year_is_not_a_span():
    assert ar.merge_dates(["1932", "1932-03-11"]) == "1932"


def test_merge_dates__unparseable_keeps_the_longest_raw_value():
    assert ar.merge_dates(["n.d.", "undated"]) == "undated"


def test_merge_dates__nothing_at_all():
    assert ar.merge_dates([]) is None
    assert ar.merge_dates([None, ""]) is None


# Entity merging
# --------------


def test_merge_entities__folds_spelling_variants_and_counts_mentions():
    merged = ar.merge_entities(
        [
            [{"label": "Ahmad Yar Khan", "evidence": [{"page_slug": "3"}]}],
            [{"label": "ahmad yar khan.", "evidence": [{"page_slug": "9"}]}],
            [{"label": "Cater"}],
        ]
    )
    assert [e["label"] for e in merged] == ["Ahmad Yar Khan", "Cater"]
    assert merged[0]["mentions"] == 2
    # Every citation survives the merge -- a name on three folios cites three.
    assert len(merged[0]["evidence"]) == 2
    assert "ahmad yar khan." in merged[0]["variants"]


def test_merge_entities__ranks_by_mention_count():
    merged = ar.merge_entities([[{"label": "A"}, {"label": "B"}], [{"label": "B"}]])
    assert [e["label"] for e in merged] == ["B", "A"]


def test_merge_entities__fills_gaps_from_later_windows():
    merged = ar.merge_entities(
        [[{"label": "Kalat"}], [{"label": "Kalat", "dates": "1666-1955"}]]
    )
    assert merged[0]["dates"] == "1666-1955"


def test_merge_entities__drops_unlabelled_rows():
    assert ar.merge_entities([[{"label": ""}, {"note": "orphan"}]]) == []


def test_merge_relations__dedupes_on_the_triple():
    merged = ar.merge_relations(
        [
            [
                {
                    "subject": "A",
                    "type": "sanctioned",
                    "object": "B",
                    "evidence": [{"page_slug": "1"}],
                }
            ],
            [
                {
                    "subject": "a",
                    "type": "Sanctioned",
                    "object": "b",
                    "evidence": [{"page_slug": "7"}],
                }
            ],
        ]
    )
    assert len(merged) == 1
    assert len(merged[0]["evidence"]) == 2


# The reduce
# ----------


def test_reduce_windows__unions_entities_and_spans_dates():
    reduced = ar.reduce_windows(
        [
            {
                "PERSON NAME": {"value": [{"label": "A"}], "confidence": 0.8},
                "DATE": {"value": "1932", "confidence": 0.9},
            },
            {
                "PERSON NAME": {"value": [{"label": "B"}], "confidence": 0.6},
                "DATE": {"value": "1935", "confidence": 0.7},
            },
        ]
    )
    assert {e["label"] for e in reduced["PERSON NAME"]["value"]} == {"A", "B"}
    assert reduced["DATE"]["value"] == "1932/1935"
    assert reduced["PERSON NAME"]["confidence"] == 0.7
    assert reduced["PERSON NAME"]["windows"] == 2


def test_reduce_windows__scalar_tags_take_the_most_confident_window():
    reduced = ar.reduce_windows(
        [
            {"TITLE": {"value": "weak", "confidence": 0.3}},
            {"TITLE": {"value": "strong", "confidence": 0.9}},
        ]
    )
    assert reduced["TITLE"]["value"] == "strong"


def test_reduce_windows__ties_favour_the_earlier_window():
    """Earliest wins on a tie, which favours the title page for TITLE/CREATOR."""
    reduced = ar.reduce_windows(
        [
            {"TITLE": {"value": "first", "confidence": 0.5}},
            {"TITLE": {"value": "second", "confidence": 0.5}},
        ]
    )
    assert reduced["TITLE"]["value"] == "first"


def test_reduce_windows__drops_empty_values_and_unknown_tags():
    reduced = ar.reduce_windows(
        [{"TITLE": {"value": "  "}, "NOT A TAG": {"value": "x"}}]
    )
    assert reduced == {}


def test_reduce_windows__nothing_in_nothing_out():
    assert ar.reduce_windows([]) == {}


# Run rollups
# -----------


def test_run_metrics__computes_coverage_and_verification_rate():
    stats = [
        {
            "evidence_spans": 4,
            "evidence_verified": 3,
            "source_ocr_confidence": 0.9,
            "pages_without_confidence": 1,
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "engine_latency_ms": 500.0,
        },
        {
            "evidence_spans": 2,
            "evidence_verified": 1,
            "source_ocr_confidence": None,
            "pages_without_confidence": 3,
            "prompt_tokens": 50,
            "completion_tokens": 10,
            "engine_latency_ms": 250.0,
        },
    ]
    fields = {"TITLE": {"confidence": 0.9}, "DATE": {"confidence": 0.5}}
    metrics = ar.run_metrics(stats, fields, pages_total=10)

    assert metrics["evidence_verified_rate"] == 4 / 6
    assert metrics["fields_filled"] == 2
    assert metrics["fields_total"] == len(at.TAGS)
    assert metrics["low_conf_field_count"] == 1
    # Averaged over the window that had a score, not over both.
    assert metrics["avg_source_ocr_confidence"] == 0.9
    assert metrics["pages_without_confidence"] == 4
    assert metrics["total_prompt_tokens"] == 150
    assert metrics["total_engine_latency_ms"] == 750.0


def test_run_metrics__no_spans_means_no_rate_rather_than_zero():
    metrics = ar.run_metrics([{"evidence_spans": 0, "evidence_verified": 0}], {}, 5)
    assert metrics["evidence_verified_rate"] is None
    assert metrics["avg_source_ocr_confidence"] is None
