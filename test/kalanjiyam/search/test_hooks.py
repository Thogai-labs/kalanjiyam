"""Incremental indexing hooks.

The hooks live in the write path for page saves, imports, and membership
changes. The property that matters is that they can never break those
operations -- not when search is off, not when the broker is down.
"""

import pytest

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.queries import get_session
from kalanjiyam.tasks import search_index
from kalanjiyam.utils.revisions import add_revision


@pytest.fixture()
def app_ctx(flask_app):
    with flask_app.app_context():
        yield flask_app


@pytest.fixture()
def page(app_ctx):
    session = get_session()
    project = session.query(db.Project).filter_by(slug="test-project").one()
    return project.pages[0]


def test_enqueue_is_a_no_op_when_search_is_disabled(app_ctx, monkeypatch):
    """SEARCH_ENABLED is false in tests; nothing should reach the broker."""
    called = []
    monkeypatch.setattr(
        search_index.index_page,
        "apply_async",
        lambda *a, **kw: called.append(a),
    )

    search_index.enqueue_page(1)
    assert called == []


def test_enqueue_dispatches_to_the_search_queue_when_enabled(app_ctx, monkeypatch):
    """Without an explicit queue the task would silently land on `default`."""
    calls = []
    monkeypatch.setitem(app_ctx.config, "SEARCH_ENABLED", True)
    monkeypatch.setattr(
        search_index.index_page,
        "apply_async",
        lambda *a, **kw: calls.append(kw),
    )

    search_index.enqueue_page(7)
    assert calls == [{"args": [7], "queue": "search_index"}]


def test_a_broker_outage_does_not_propagate(app_ctx, monkeypatch):
    """A dead broker must not turn into a failed page save."""
    monkeypatch.setitem(app_ctx.config, "SEARCH_ENABLED", True)

    def explode(*args, **kwargs):
        raise ConnectionError("redis is down")

    monkeypatch.setattr(search_index.index_page, "apply_async", explode)
    monkeypatch.setattr(search_index.index_project, "apply_async", explode)

    # Neither call raises.
    search_index.enqueue_page(7)
    search_index.enqueue_project(7)


def test_saving_a_page_enqueues_it(app_ctx, page, monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "kalanjiyam.tasks.search_index.enqueue_page", lambda pid: enqueued.append(pid)
    )

    add_revision(
        page,
        summary="test",
        content="new content",
        status="reviewed-0",
        version=page.versions[0].version if page.versions else 0,
        author_id=None,
        version_key="role:p1" if page.versions else "role:p1",
    )

    assert enqueued == [page.id]


def test_page_save_survives_a_failing_hook(app_ctx, page, monkeypatch):
    """A save must succeed even if indexing blows up entirely.

    Not just "the row lands" -- `add_revision` must not raise, or the proofer
    sees a 500 on a save that actually worked.
    """

    def explode(_page_id):
        raise RuntimeError("indexing exploded")

    monkeypatch.setattr("kalanjiyam.tasks.search_index.enqueue_page", explode)

    before = len(page.revisions)
    add_revision(
        page,
        summary="test",
        content="content that must still be saved",
        status="reviewed-0",
        version=page.versions[0].version if page.versions else 0,
        author_id=None,
    )

    get_session().refresh(page)
    assert len(page.revisions) == before + 1


def test_group_membership_changes_trigger_a_reindex(app_ctx, monkeypatch):
    """A group change moves a project between per-organization indices."""
    enqueued = []
    monkeypatch.setattr(
        "kalanjiyam.tasks.search_index.enqueue_project",
        lambda pid: enqueued.append(pid),
    )

    session = get_session()
    project = session.query(db.Project).filter_by(slug="test-project").one()
    group = session.query(db.Group).filter_by(slug="hook-test-org").first()
    if group is None:
        group = db.Group(name="Hook Test Org", slug="hook-test-org")
        session.add(group)
        session.commit()

    q.add_project_to_group(project.id, group.id)
    q.remove_project_from_group(project.id, group.id)

    assert enqueued == [project.id, project.id]
