"""Tests for the full-text windowing used by archival extraction."""

from kalanjiyam.utils import project_metadata as pm


def _rows(count, char_len=1000, start=1):
    return [
        pm.TrackRow(
            page_id=i,
            revision_id=i,
            version_key="ocr:surya",
            content_format="plain",
            char_len=char_len,
            order=i,
            slug=str(i),
        )
        for i in range(start, start + count)
    ]


# Planning
# --------


def test_plan_windows__fills_to_the_budget():
    plan = pm.plan_windows(_rows(9), budget_chars=3000, overlap=0)
    assert [[r.slug for r in w] for w in plan] == [
        ["1", "2", "3"],
        ["4", "5", "6"],
        ["7", "8", "9"],
    ]


def test_plan_windows__repeats_the_seam_page():
    """A signature block on the next page must be seen with its letter."""
    plan = pm.plan_windows(_rows(6), budget_chars=3000, overlap=1)
    assert plan[0][-1].slug == plan[1][0].slug


def test_plan_windows__covers_every_page():
    plan = pm.plan_windows(_rows(25), budget_chars=4000)
    seen = {r.slug for window in plan for r in window}
    assert seen == {str(i) for i in range(1, 26)}


def test_plan_windows__never_splits_a_page():
    """A page larger than the budget gets a window rather than being dropped."""
    plan = pm.plan_windows(_rows(1, char_len=99_999), budget_chars=3000)
    assert [[r.slug for r in w] for w in plan] == [["1"]]


def test_plan_windows__skips_blank_pages():
    rows = _rows(2) + _rows(1, char_len=pm.MIN_SAMPLE_CHARS - 1, start=3)
    plan = pm.plan_windows(rows, budget_chars=10_000)
    assert [r.slug for r in plan[0]] == ["1", "2"]


def test_plan_windows__nothing_to_do():
    assert pm.plan_windows([], budget_chars=3000) == []


def test_plan_windows__orders_by_page_order():
    rows = list(reversed(_rows(4)))
    plan = pm.plan_windows(rows, budget_chars=10_000)
    assert [r.slug for r in plan[0]] == ["1", "2", "3", "4"]


# Block shaping
# -------------


def _row():
    return _rows(1)[0]


def test_blocks_for_request__uses_the_structured_document():
    document = {
        "blocks": [
            {"id": "b2", "type": "paragraph", "reading_order": 2, "content": "second"},
            {"id": "b1", "type": "heading", "reading_order": 1, "content": "first"},
        ]
    }
    blocks = pm.blocks_for_request(_row(), "ignored", document)
    assert [b["id"] for b in blocks] == ["b1", "b2"]
    assert [b["text"] for b in blocks] == ["first", "second"]


def test_blocks_for_request__falls_back_to_plain_text():
    """A page with no block document still contributes its words."""
    blocks = pm.blocks_for_request(_row(), "just text", None)
    assert len(blocks) == 1
    assert blocks[0]["text"] == "just text"
    # No id: the quote is still verifiable, it just cannot link to an image region.
    assert blocks[0]["id"] == ""


def test_blocks_for_request__drops_empty_blocks():
    document = {"blocks": [{"id": "b1", "content": "  "}, {"id": "b2", "content": "x"}]}
    assert [b["id"] for b in pm.blocks_for_request(_row(), "", document)] == ["b2"]


def test_blocks_for_request__blank_page_yields_nothing():
    assert pm.blocks_for_request(_row(), "", None) == []


# Hashing
# -------


def test_window_hash__is_stable_and_content_sensitive():
    pages = [{"page_slug": "1", "blocks": [{"id": "b1", "text": "alpha"}]}]
    changed = [{"page_slug": "1", "blocks": [{"id": "b1", "text": "beta"}]}]
    assert pm.window_hash(pages) == pm.window_hash(pages)
    assert pm.window_hash(pages) != pm.window_hash(changed)


def test_window_hash__notices_a_reblocked_page():
    """Same words, different block ids: re-OCR changed the evidence anchors."""
    before = [{"page_slug": "1", "blocks": [{"id": "b1", "text": "alpha"}]}]
    after = [{"page_slug": "1", "blocks": [{"id": "x9", "text": "alpha"}]}]
    assert pm.window_hash(before) != pm.window_hash(after)


def test_window_hash__notices_a_moved_page():
    a = [{"page_slug": "1", "blocks": [{"id": "b1", "text": "alpha"}]}]
    b = [{"page_slug": "2", "blocks": [{"id": "b1", "text": "alpha"}]}]
    assert pm.window_hash(a) != pm.window_hash(b)


def test_window_hash__empty_is_stable():
    assert pm.window_hash([]) == pm.window_hash([])


# Budget fitting
# --------------


def test_fit_blocks__takes_whole_blocks_while_they_fit():
    blocks = [{"text": "aaaa"}, {"text": "bbbb"}]
    assert pm._fit_blocks(blocks, 8) == blocks


def test_fit_blocks__truncates_only_the_last_block():
    blocks = [{"text": "aaaa"}, {"text": "bbbb"}]
    fitted = pm._fit_blocks(blocks, 6)
    assert [b["text"] for b in fitted] == ["aaaa", "bb"]


def test_fit_blocks__no_room_at_all():
    assert pm._fit_blocks([{"text": "aaaa"}], 0) == []
