"""Tests for metadata extraction orchestration.

The rules that matter here: an extraction never destroys curated data, and a
failed persona call never discards the calls that succeeded.
"""

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.tasks import metadata as metadata_tasks


def _project(session, slug):
    board = db.Board(title=f"board-{slug}")
    session.add(board)
    session.flush()
    project = db.Project(slug=slug, display_title=slug, board_id=board.id)
    session.add(project)
    session.flush()
    return project


# Bibliographic mapping
# ---------------------


def test_apply_bibliographic__fills_empty_columns(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "biblio-empty")

        report = metadata_tasks.apply_bibliographic(
            session,
            project,
            {
                "title": "Avantisundarikatha",
                "author": "Dandin",
                "editor_translator": "M.R. Kale",
                "publisher": "Nirnayasagar",
                "place_of_publication": "Bombay",
                "year": "1924",
                "edition": "2nd",
                "series": "TSS 42",
                "subject": "Sanskrit prose",
                "subtitle": "a gadyakavya",
            },
        )

        assert project.print_title == "Avantisundarikatha"
        assert project.author == "Dandin"
        assert project.editor == "M.R. Kale"
        assert project.publication_year == "1924"
        assert project.place_of_publication == "Bombay"
        assert project.series == "TSS 42"
        assert report["applied"]["print_title"] == "Avantisundarikatha"


def test_apply_bibliographic__never_overwrites_curated_values(flask_app):
    """Human-entered data outranks a fresh generation."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "biblio-curated")
        project.author = "Carefully Typed Name"

        report = metadata_tasks.apply_bibliographic(
            session, project, {"author": "Model Guess", "publisher": "New Publisher"}
        )

        assert project.author == "Carefully Typed Name"
        assert report["skipped"]["author"] == "Model Guess"
        # ...but empty fields are still filled.
        assert project.publisher == "New Publisher"


def test_apply_bibliographic__ignores_blank_and_null_values(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "biblio-blank")

        metadata_tasks.apply_bibliographic(
            session, project, {"author": None, "publisher": "   "}
        )

        assert project.author == ""
        assert project.publisher == ""


def test_apply_bibliographic__does_not_invent_genres(flask_app):
    """Unmatched genres stay as text rather than polluting the lookup table."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "biblio-genre")
        before = len(session.query(db.Genre).all())

        report = metadata_tasks.apply_bibliographic(
            session, project, {"genre": "some-unheard-of-genre"}
        )

        assert project.genre_id is None
        assert report["skipped"]["genre"] == "some-unheard-of-genre"
        assert len(session.query(db.Genre).all()) == before


def test_apply_bibliographic__matches_an_existing_genre_case_insensitively(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "biblio-genre-match")
        genre = db.Genre(name="Kavya")
        session.add(genre)
        session.flush()

        metadata_tasks.apply_bibliographic(session, project, {"genre": "kavya"})

        assert project.genre_id == genre.id


# Saving and staging
# ------------------


def _payload(summary="A summary."):
    return {
        "schema_version": metadata_tasks.SCHEMA_VERSION,
        "languages": [{"code": "sa", "script": "Deva", "role": "primary"}],
        "content": {"summary": summary, "keywords": ["a"], "toc": []},
        "bibliographic": {"author": "Someone"},
        "derived": {"pages_total": 10},
        "provenance": {"status": metadata_tasks.STATUS_OK},
    }


def test_save__first_run_writes_canonical_fields(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "save-first")

        metadata_tasks._save(session, project, _payload(), {})

        data = project.extracted_metadata
        assert data["content"]["summary"] == "A summary."
        assert "staged" not in data
        assert project.author == "Someone"


def test_save__later_run_is_staged_rather_than_applied(flask_app):
    """A regenerate must not silently discard a moderator's edits."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "save-staged")

        metadata_tasks._save(session, project, _payload("first"), {})
        existing = project.extracted_metadata
        metadata_tasks._save(session, project, _payload("second"), existing)

        data = project.extracted_metadata
        assert data["content"]["summary"] == "first"
        assert data["staged"]["content"]["summary"] == "second"


def test_save__derived_stats_always_refresh(flask_app):
    """Computed facts are never stale: only generated fields get staged."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "save-derived")

        metadata_tasks._save(session, project, _payload(), {})
        payload = _payload()
        payload["derived"] = {"pages_total": 99}
        metadata_tasks._save(session, project, payload, project.extracted_metadata)

        assert project.extracted_metadata["derived"]["pages_total"] == 99


def test_accept_staged__promotes_and_clears(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "accept-staged")

        metadata_tasks._save(session, project, _payload("first"), {})
        metadata_tasks._save(
            session, project, _payload("second"), project.extracted_metadata
        )

        assert metadata_tasks.accept_staged(session, project) is True
        data = project.extracted_metadata
        assert data["content"]["summary"] == "second"
        assert "staged" not in data


def test_accept_staged__no_op_without_a_staged_run(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session, "accept-nothing")
        metadata_tasks._save(session, project, _payload(), {})

        assert metadata_tasks.accept_staged(session, project) is False


# Sample hashing
# --------------


def test_sample_hash__is_stable_and_input_sensitive():
    a = metadata_tasks._sample_hash("front", "body", "lang")
    assert a == metadata_tasks._sample_hash("front", "body", "lang")
    assert a != metadata_tasks._sample_hash("front", "body", "different")


def test_sample_hash__separates_fields():
    """Concatenation must not let field boundaries collide."""
    assert metadata_tasks._sample_hash("ab", "c") != metadata_tasks._sample_hash("a", "bc")


# Failure handling
# ----------------


def test_call__records_an_error_for_empty_input():
    errors = []
    assert metadata_tasks._call("persona", "   ", errors) is None
    assert errors and "no text" in errors[0]


def test_usage_of__sums_and_skips_missing_results():
    class _Result:
        def __init__(self, usage):
            self.usage = usage

    total = metadata_tasks._usage_of(
        _Result({"prompt_tokens": 10, "total_tokens": 12}),
        None,
        _Result({"prompt_tokens": 5, "total_tokens": 6}),
    )
    assert total["prompt_tokens"] == 15
    assert total["total_tokens"] == 18
    assert total["calls"] == 2
