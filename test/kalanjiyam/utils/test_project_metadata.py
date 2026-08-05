"""Tests for the deterministic metadata layer."""

import pytest

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.utils import project_metadata as pm


# Track precedence
# ----------------


def test_track_tier__moderator_user_outranks_plain_user():
    moderators = {7}
    assert pm._track_tier("user:7", moderators) == pm.TIER_REVIEWED
    assert pm._track_tier("user:9", moderators) == pm.TIER_P1


def test_track_tier__roles():
    assert pm._track_tier("role:moderator", set()) == pm.TIER_REVIEWED
    assert pm._track_tier("role:p2", set()) == pm.TIER_P2
    assert pm._track_tier("role:p1", set()) == pm.TIER_P1
    assert pm._track_tier("ocr:chandra", set()) == pm.TIER_OCR


def test_track_tier__legacy_revisions_count_as_p1():
    """`Revision.page_version_id` is nullable, so untracked revisions exist."""
    assert pm._track_tier(pm.LEGACY_TRACK, set()) == pm.TIER_P1


def test_track_tier__malformed_user_key_does_not_raise():
    assert pm._track_tier("user:not-an-int", set()) == pm.TIER_P1


def _row(version_key, char_len=100, order=1):
    return pm.TrackRow(
        page_id=1,
        revision_id=1,
        version_key=version_key,
        content_format="plain",
        char_len=char_len,
        order=order,
        slug="1",
    )


def test_sort_key__edited_text_beats_much_longer_ocr():
    """Quality, not length or recency, decides which track we read."""
    edited = _row("role:p1", char_len=10)
    ocr = _row("ocr:chandra", char_len=99999)
    assert pm._sort_key(edited, set()) < pm._sort_key(ocr, set())


def test_ocr_engine_rank__is_stable_and_puts_unknown_engines_last():
    assert pm._ocr_engine_rank("ocr:chandra") < pm._ocr_engine_rank("ocr:tesseract")
    assert pm._ocr_engine_rank("ocr:unlisted") > pm._ocr_engine_rank("ocr:tesseract")


@pytest.mark.parametrize("key", ["translation:en", "TR:en"])
def test_translation_tracks_are_excluded(key):
    """A title read out of a translation would be the wrong title."""
    assert key.startswith(pm.EXCLUDED_TRACK_PREFIXES)


def test_ocr_tracks_are_not_excluded():
    assert not "ocr:surya".startswith(pm.EXCLUDED_TRACK_PREFIXES)


def test_resolve_extraction_tracks__prefers_edit_and_falls_back_to_ocr(flask_app):
    """One page edited, one page OCR-only: each resolves to its best track."""
    with flask_app.app_context():
        session = get_session()
        status = session.query(db.PageStatus).first()
        board = db.Board(title="metadata-board")
        session.add(board)
        session.flush()

        project = db.Project(
            slug="track-precedence",
            display_title="Track precedence",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        edited_page = db.Page(
            project_id=project.id, slug="1", order=1, status_id=status.id
        )
        ocr_page = db.Page(
            project_id=project.id, slug="2", order=2, status_id=status.id
        )
        session.add_all([edited_page, ocr_page])
        session.flush()

        # Page 1 has both an OCR track and a human edit.
        for page, key, content in (
            (edited_page, "ocr:chandra", "ocr text " * 50),
            (edited_page, "role:p2", "edited text"),
            (ocr_page, "ocr:chandra", "only ocr text"),
        ):
            version = db.PageVersion(page_id=page.id, version_key=key)
            session.add(version)
            session.flush()
            session.add(
                db.Revision(
                    project_id=project.id,
                    page_id=page.id,
                    page_version_id=version.id,
                    status_id=status.id,
                    content=content,
                )
            )
        session.commit()

        tracks = pm.resolve_extraction_tracks(session, project.id)

        assert tracks[edited_page.id].version_key == "role:p2"
        assert tracks[ocr_page.id].version_key == "ocr:chandra"
        assert tracks[ocr_page.id].is_ocr is True
        assert tracks[edited_page.id].is_ocr is False


# Text normalization
# ------------------


def test_to_plain_text__strips_html():
    assert pm.to_plain_text("<p>hello <b>world</b></p>", "html") == "hello world"


def test_to_plain_text__strips_markup_from_html_engines_regardless_of_format():
    """A chandra track can claim `plain` but still carry tags."""
    assert pm.to_plain_text("<p>abc</p>", "plain", "ocr:chandra") == "abc"


def test_to_plain_text__unescapes_entities():
    assert pm.to_plain_text("<p>a &amp; b</p>", "html") == "a & b"


def test_to_plain_text__strips_markdown_from_markdown_engines():
    got = pm.to_plain_text("## Title\n**bold** text", "plain", "ocr:deepseek")
    assert got == "Title\nbold text"


def test_to_plain_text__leaves_indic_text_alone():
    assert pm.to_plain_text("नमः शिवाय", "plain") == "नमः शिवाय"


def test_to_plain_text__handles_empty():
    assert pm.to_plain_text("", "plain") == ""


# Script detection
# ----------------


@pytest.mark.parametrize(
    "char,script",
    [("அ", "Taml"), ("न", "Deva"), ("అ", "Telu"), ("ಅ", "Knda"), ("A", "Latn")],
)
def test_script_of(char, script):
    assert pm._script_of(char) == script


def test_script_of__digits_are_unmapped():
    assert pm._script_of("5") is None


def test_dominant_script():
    assert pm.dominant_script({"Taml": 0.7, "Latn": 0.3}) == "Taml"
    assert pm.dominant_script({}) == "Latn"


# Token budgeting
# ---------------


def test_estimate_tokens__indic_costs_more_than_latin():
    """Conjuncts and matras tokenize poorly, so a char cap is not a token cap."""
    text = "x" * 1000
    assert pm.estimate_tokens(text, {"Deva": 1.0}) > pm.estimate_tokens(
        text, {"Latn": 1.0}
    )


def test_estimate_tokens__empty():
    assert pm.estimate_tokens("", {}) == 0


def test_budget_chars__indic_gets_a_smaller_allowance():
    assert pm._budget_chars("content_analysis", {"Deva": 1.0}) < pm._budget_chars(
        "content_analysis", {"Latn": 1.0}
    )


@pytest.mark.parametrize("scripts", [{"Deva": 1.0}, {"Latn": 1.0}, {}])
@pytest.mark.parametrize("persona", ["front_matter", "content_analysis", "language_id"])
def test_budget_round_trips_under_its_token_cap(persona, scripts):
    """A sample filling the char budget must still fit the token budget."""
    budget = pm._budget_chars(persona, scripts)
    assert (
        pm.estimate_tokens("x" * budget, scripts) <= pm.INPUT_TOKEN_BUDGETS[persona]
    )


def test_budgets_leave_room_for_output_in_the_context_window():
    """Input + output must stay inside the model's context."""
    assert pm.INPUT_TOKEN_BUDGETS["content_analysis"] + 4096 < pm.MODEL_CONTEXT_TOKENS


# Sampling
# --------


def test_tier_for__front_matter_widens_with_book_size():
    """Large books print their own TOC, so we read more front matter."""
    assert pm._tier_for(30) == (5, 6)
    assert pm._tier_for(200) == (5, 12)
    assert pm._tier_for(800) == (10, 16)
    assert pm._tier_for(5000) == (15, 20)


def test_body_sample_count_does_not_grow_with_book_size():
    """LLM cost is flat in page count by construction."""
    _front_small, body_small = pm._tier_for(200)
    _front_huge, body_huge = pm._tier_for(20000)
    assert body_huge <= body_small * 2


def test_usable__drops_blank_pages():
    """Otherwise a partly-OCR'd book samples mostly empty pages."""
    rows = [_row("role:p1", char_len=10, order=1), _row("role:p1", char_len=500, order=2)]
    usable = pm._usable(rows)
    assert len(usable) == 1
    assert usable[0].char_len == 500


# Form round-trips
# ----------------


def test_languages_round_trip():
    languages = [
        {"code": "sa", "script": "Deva", "role": "primary"},
        {"code": "en", "script": "Latn", "role": "translation"},
    ]
    assert pm.parse_languages(pm.format_languages(languages)) == languages


def test_parse_languages__accepts_a_bare_code():
    assert pm.parse_languages("ta") == [{"code": "ta", "script": None, "role": None}]


def test_parse_languages__ignores_blank_lines():
    assert pm.parse_languages("\n   \n") == []


def test_toc_round_trip():
    toc = [{"label": "Sarga 1", "page": "12"}, {"label": "Preface", "page": None}]
    assert pm.parse_toc(pm.format_toc(toc)) == toc


def test_parse_toc__entry_without_a_page():
    assert pm.parse_toc("Introduction") == [{"label": "Introduction", "page": None}]


def test_parse_list__dedupes_and_preserves_order():
    assert pm.parse_list("kavya, kavya, alankara") == ["kavya", "alankara"]


def test_parse_list__accepts_newlines():
    assert pm.parse_list("a\nb") == ["a", "b"]


def test_format_list__handles_none():
    assert pm.format_list(None) == ""
