"""Document construction and corpus traversal.

These run against the in-memory test database; no cluster is involved.
"""

import pytest

import kalanjiyam.database as db
from kalanjiyam.queries import get_session
from kalanjiyam.search import indexer, schema


@pytest.fixture()
def app_ctx(flask_app):
    with flask_app.app_context():
        yield flask_app


@pytest.fixture()
def org(app_ctx):
    session = get_session()
    group = session.query(db.Group).filter_by(slug="search-test-org").first()
    if group is None:
        group = db.Group(name="Search Test Org", slug="search-test-org")
        session.add(group)
        session.commit()
    return group


@pytest.fixture()
def project(app_ctx, org):
    """A two-page project: one page with text, one with no revision at all."""
    session = get_session()
    existing = session.query(db.Project).filter_by(slug="search-fixture").first()
    if existing is not None:
        return existing

    board = db.Board(title="search-fixture-board")
    session.add(board)
    session.flush()

    project = db.Project(
        slug="search-fixture",
        display_title="Siddha Fixture",
        author="Agastyar",
        board_id=board.id,
        is_publicly_viewable=True,
    )
    session.add(project)
    session.flush()

    status = session.query(db.PageStatus).filter_by(name="reviewed-0").one()

    page = db.Page(project_id=project.id, slug="1", order=1, status_id=status.id)
    empty_page = db.Page(project_id=project.id, slug="2", order=2, status_id=status.id)
    session.add_all([page, empty_page])
    session.flush()

    session.add(
        db.Revision(
            project_id=project.id,
            page_id=page.id,
            status_id=status.id,
            content="ignored legacy text",
            document={
                "content_format": "plain",
                "blocks": [
                    {
                        "id": "b1",
                        "type": "paragraph",
                        "bbox": [0, 0, 1, 1],
                        "reading_order": 0,
                        "content": "கடுக்காய்",
                        "language": "ta",
                    }
                ],
            },
        )
    )
    session.add(db.ProjectGroups(project_id=project.id, group_id=org.id))
    session.commit()
    return project


def test_project_group_ids(project, org):
    assert indexer.project_group_ids(project) == [org.id]


def test_page_doc_prefers_block_text_over_legacy_content(project):
    session = get_session()
    docs = dict(indexer.iter_page_docs(session, project))

    page = next(p for p in project.pages if p.slug == "1")
    source = docs[schema.page_doc_id(page.id)]
    assert source["content"] == "கடுக்காய்"
    assert "ignored legacy text" not in source["content"]


def test_page_without_a_revision_is_not_indexed(project):
    """An empty document would inflate result counts for no benefit."""
    session = get_session()
    docs = dict(indexer.iter_page_docs(session, project))

    empty = next(p for p in project.pages if p.slug == "2")
    assert schema.page_doc_id(empty.id) not in docs
    assert len(docs) == 1


def test_page_doc_carries_access_fields(project, org):
    session = get_session()
    _doc_id, source = next(iter(indexer.iter_page_docs(session, project)))

    assert source["group_ids"] == [org.id]
    assert source["is_public"] is True
    assert source["project_slug"] == "search-fixture"
    assert source["project_title"] == "Siddha Fixture"
    assert source["project_author"] == "Agastyar"


def test_page_language_comes_from_block_metadata(project):
    session = get_session()
    _doc_id, source = next(iter(indexer.iter_page_docs(session, project)))
    assert source["lang"] == "ta"
    # Only English gets the stemmed field.
    assert "content_en" not in source


def test_english_pages_get_a_stemmed_field(app_ctx, org):
    session = get_session()
    board = db.Board(title="en-board")
    session.add(board)
    session.flush()
    project = db.Project(
        slug="search-fixture-en", display_title="English Fixture", board_id=board.id
    )
    session.add(project)
    session.flush()
    status = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
    page = db.Page(project_id=project.id, slug="1", order=1, status_id=status.id)
    session.add(page)
    session.flush()
    session.add(
        db.Revision(
            project_id=project.id,
            page_id=page.id,
            status_id=status.id,
            content="Healing herbs",
            document={
                "blocks": [
                    {
                        "id": "b1",
                        "type": "paragraph",
                        "bbox": [0, 0, 1, 1],
                        "reading_order": 0,
                        "content": "Healing herbs",
                        "language": "en",
                    }
                ]
            },
        )
    )
    session.add(db.ProjectGroups(project_id=project.id, group_id=org.id))
    session.commit()

    _doc_id, source = next(iter(indexer.iter_page_docs(session, project)))
    assert source["lang"] == "en"
    assert source["content_en"] == "Healing herbs"


def test_projects_with_no_organization_are_skipped(app_ctx):
    """Groupless projects are a data defect, not a supported state."""
    session = get_session()
    project = session.query(db.Project).filter_by(slug="test-project").one()
    assert indexer.project_group_ids(project) == []
    assert list(indexer.iter_page_docs(session, project)) == []
    assert indexer.build_project_source(session, project) is None


def test_ungrouped_project_count_reports_the_defect(app_ctx):
    session = get_session()
    assert indexer.ungrouped_project_count(session) >= 1


def test_latest_revision_matches_what_the_reader_shows(project):
    """The reader renders `page.revisions[-1]`; the index must agree."""
    session = get_session()
    page = next(p for p in project.pages if p.slug == "1")
    status = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
    session.add(
        db.Revision(
            project_id=project.id,
            page_id=page.id,
            status_id=status.id,
            content="newest text",
        )
    )
    session.commit()
    session.refresh(page)

    latest = indexer.latest_revision_ids(session, project.id)
    assert latest[page.id] == page.revisions[-1].id

    docs = dict(indexer.iter_page_docs(session, project))
    assert docs[schema.page_doc_id(page.id)]["content"] == "newest text"


def test_project_doc_counts_pages(project):
    session = get_session()
    source = indexer.build_project_source(session, project)

    assert source["project_id"] == project.id
    assert source["display_title"] == "Siddha Fixture"
    assert source["page_count"] == 2
    # Only one of the two pages has a revision.
    assert source["ocr_page_count"] == 1


def test_projects_for_org_finds_the_project(project, org):
    session = get_session()
    slugs = {p.slug for p in indexer.projects_for_org(session, org.id)}
    assert "search-fixture" in slugs
    assert org.id in indexer.all_group_ids(session)
