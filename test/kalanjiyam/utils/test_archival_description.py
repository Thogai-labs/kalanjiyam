"""Tests for the description read/curate/write-down layer.

Three properties carry the weight:

* Curation lives in its own rows, so a re-run cannot destroy it.
* A curated value hides the generated one without deleting it.
* The write-down never overwrites a column a human filled in.
"""

import pytest

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.utils import archival_description as ad
from kalanjiyam.utils import archival_taxonomy as at


@pytest.fixture(autouse=True)
def _empty_description(flask_app):
    """Start each test with no runs and no curation.

    `flask_app` is session-scoped and shares one database across the whole file,
    so without this a curated TITLE in one test is still there in the next.
    """
    with flask_app.app_context():
        session = get_session()
        session.query(db.MetadataEvidence).delete()
        session.query(db.MetadataField).delete()
        session.query(db.MetadataWindow).delete()
        session.query(db.MetadataExtractionRun).delete()
        session.commit()
    yield


def _project(session):
    return session.query(db.Project).filter_by(slug="test-project").first()


def _run(session, project_id, status="COMPLETED"):
    run = db.MetadataExtractionRun(
        project_id=project_id,
        status=status,
        taxonomy_version=at.TAXONOMY_VERSION,
        pages_total=10,
        pages_read=10,
    )
    session.add(run)
    session.flush()
    return run


def _field(session, run, project_id, code, value, **kw):
    field = db.MetadataField(
        run_id=run.id, project_id=project_id, tag_code=code, value=value, **kw
    )
    session.add(field)
    session.flush()
    return field


# Curation
# --------


def test_set_curated__creates_a_row_without_any_run(flask_app):
    """The write-locked tags must be enterable before anything is extracted."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        ad.set_curated(session, project.id, "CUSTODIAL HISTORY", "Held at Quetta.")
        session.commit()

        view = ad.describe(session, project.id)
        assert view["run"] is None
        assert view["views"]["CUSTODIAL HISTORY"].value == "Held at Quetta."
        assert view["views"]["CUSTODIAL HISTORY"].is_curated


def test_set_curated__is_idempotent_on_the_same_tag(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        ad.set_curated(session, project.id, "ACCESS", "Open.")
        ad.set_curated(session, project.id, "ACCESS", "Restricted.")
        session.commit()

        rows = (
            session.query(db.MetadataField)
            .filter_by(project_id=project.id, tag_code="ACCESS", run_id=None)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].curated_value == "Restricted."


def test_set_curated__records_who_and_when(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        row = ad.set_curated(session, project.id, "REFERENCE", "IOR/R/1/1", user_id=7)
        session.commit()

        assert row.curated_by_id == 7
        assert row.curated_at is not None
        assert row.source == at.SOURCE_CURATED


def test_set_curated__an_empty_value_clears_rather_than_stores_a_blank(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        ad.set_curated(session, project.id, "ACCESS", "Open.")
        session.commit()
        ad.set_curated(session, project.id, "ACCESS", "   ")
        session.commit()

        assert not ad.describe(session, project.id)["views"]["ACCESS"].is_curated


def test_set_curated__rejects_an_unknown_tag(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        try:
            ad.set_curated(session, project.id, "NOT A TAG", "x")
        except ValueError:
            return
        raise AssertionError("an unknown tag should not be storable")


def test_clear_curated__reports_whether_there_was_anything(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        assert ad.clear_curated(session, project.id, "ACCESS") is False
        ad.set_curated(session, project.id, "ACCESS", "Open.")
        session.commit()
        assert ad.clear_curated(session, project.id, "ACCESS") is True


# Merging
# -------


def test_describe__curation_outranks_the_generated_value(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        run = _run(session, project.id)
        _field(session, run, project.id, "TITLE", "A misread title", confidence=0.4)
        ad.set_curated(session, project.id, "TITLE", "The real title")
        session.commit()

        view = ad.describe(session, project.id)["views"]["TITLE"]
        assert view.value == "The real title"
        # The generated value is hidden, not lost -- an override the archivist
        # cannot see behind is indistinguishable from an empty extraction.
        assert view.superseded == "A misread title"


def test_describe__a_curated_value_is_not_scored(flask_app):
    """A human's entry has no confidence, which is not the same as 1.0."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        run = _run(session, project.id)
        _field(session, run, project.id, "TITLE", "Generated", confidence=0.9)
        ad.set_curated(session, project.id, "TITLE", "Typed")
        session.commit()

        view = ad.describe(session, project.id)["views"]["TITLE"]
        assert view.confidence is None
        assert view.source == at.SOURCE_CURATED


def test_describe__covers_every_tag_including_the_empty_ones(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        view = ad.describe(session, project.id)

        assert view["total"] == len(at.TAGS)
        assert view["filled"] == 0
        assert all(v.is_empty for v in view["views"].values())


def test_describe__reads_the_newest_usable_run(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        old = _run(session, project.id)
        _field(session, old, project.id, "TITLE", "Old")
        new = _run(session, project.id)
        _field(session, new, project.id, "TITLE", "New")
        session.commit()

        assert ad.describe(session, project.id)["views"]["TITLE"].value == "New"


def test_describe__a_failed_run_does_not_replace_a_good_one(flask_app):
    """A failure is reported separately rather than blanking the description."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        good = _run(session, project.id)
        _field(session, good, project.id, "TITLE", "Still here")
        _run(session, project.id, status="FAILED")
        session.commit()

        view = ad.describe(session, project.id)
        assert view["views"]["TITLE"].value == "Still here"
        assert view["attempt"].status == "FAILED"


def test_describe__marks_the_write_locked_tags(flask_app):
    with flask_app.app_context():
        session = get_session()
        views = ad.describe(session, _project(session).id)["views"]

        assert {c for c, v in views.items() if v.is_locked} == at.WRITE_LOCKED


def test_evidence_for__separates_spans_by_entity(flask_app):
    """A citation for a list of forty names is no citation at all."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session)

        run = _run(session, project.id)
        field = _field(
            session,
            run,
            project.id,
            "PERSON NAME",
            [{"label": "A"}, {"label": "B"}],
        )
        session.add_all(
            [
                db.MetadataEvidence(
                    field_id=field.id,
                    value_index=0,
                    page_slug="1",
                    quote="A",
                    verified=True,
                ),
                db.MetadataEvidence(
                    field_id=field.id,
                    value_index=1,
                    page_slug="2",
                    quote="B",
                    verified=False,
                ),
            ]
        )
        session.commit()

        view = ad.describe(session, project.id)["views"]["PERSON NAME"]
        assert [s.quote for s in view.evidence_for(0)] == ["A"]
        assert view.evidence_for(1)[0].verified is False
        assert view.unindexed_evidence == []


# Write-down
# ----------


def _fields(**kw):
    return {code: {"value": value} for code, value in kw.items()}


def test_write_down__seeds_empty_columns(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.print_title = ""
        project.author = ""

        ad.write_down(
            session,
            project,
            {
                "TITLE": {"value": "Kalat file 17"},
                "CREATOR": {"value": "A.G.G. Baluchistan"},
            },
        )
        session.commit()

        assert project.print_title == "Kalat file 17"
        assert project.author == "A.G.G. Baluchistan"


def test_write_down__never_overwrites_what_a_human_typed(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.print_title = "Typed by a moderator"

        report = ad.write_down(session, project, {"TITLE": {"value": "Generated"}})
        session.commit()

        assert project.print_title == "Typed by a moderator"
        assert report["skipped"]["print_title"] == "Generated"


def test_write_down__takes_the_top_ranked_entity_label(flask_app):
    """`merge_entities` ranks by mention frequency, so the first entry leads."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.author = ""

        ad.write_down(
            session,
            project,
            {"CREATOR": {"value": [{"label": "Foreign Dept"}, {"label": "Other"}]}},
        )
        session.commit()

        assert project.author == "Foreign Dept"


def test_write_down__fills_summary_keywords_and_languages(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.extracted_metadata = {}

        report = ad.write_down(
            session,
            project,
            {
                "SCOPE CONTENT": {"value": "Correspondence on a commission."},
                "SUBJECT": {"value": [{"label": "Honours"}, {"label": "Kalat"}]},
                "LANGUAGE": {"value": [{"label": "fa", "note": "primary"}]},
            },
        )
        session.commit()

        data = project.extracted_metadata
        assert data["content"]["summary"] == "Correspondence on a commission."
        assert data["content"]["keywords"] == ["Honours", "Kalat"]
        assert data["languages"] == [{"code": "fa", "role": "primary"}]
        assert set(report["metadata_applied"]) == {"summary", "keywords", "languages"}


def test_write_down__leaves_an_existing_summary_alone(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.extracted_metadata = {"content": {"summary": "Mine."}}

        report = ad.write_down(
            session, project, {"SCOPE CONTENT": {"value": "Generated."}}
        )
        session.commit()

        assert project.extracted_metadata["content"]["summary"] == "Mine."
        assert "summary" not in report["metadata_applied"]


def test_write_down__an_empty_description_changes_nothing(flask_app):
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.print_title = ""

        report = ad.write_down(session, project, {})
        session.commit()

        assert project.print_title == ""
        assert report["applied"] == {}
        assert report["metadata_applied"] == []


def test_write_down__ignores_tags_with_no_column(flask_app):
    """PERSON NAME is every person named anywhere -- not the author."""
    with flask_app.app_context():
        session = get_session()
        project = _project(session)
        project.author = ""

        ad.write_down(
            session, project, {"PERSON NAME": {"value": [{"label": "A Clerk"}]}}
        )
        session.commit()

        assert project.author == ""
