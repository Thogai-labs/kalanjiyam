"""Background tasks for maintaining the search index.

Progress lives in :class:`~kalanjiyam.models.search.SearchIndexJob` rows
rather than in Celery's result backend, so the admin dashboard keeps showing
accurate state across worker restarts and the CLI can report on jobs it did
not start.

Every task takes integer primary keys: the Celery serializer is JSON-only.
"""

from __future__ import annotations

import contextlib
import logging
import os
from datetime import datetime

from flask import current_app, has_app_context

import kalanjiyam.database as db
import kalanjiyam.queries as q
from config import create_config_only_app
from kalanjiyam.models.search import (
    CANCEL_MESSAGE,
    SCOPE_ORG,
    SCOPE_PROJECT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    SearchIndexJob,
)
from kalanjiyam.search import indexer
from kalanjiyam.search.client import get_client, get_settings, is_enabled
from kalanjiyam.tasks import app

LOG = logging.getLogger(__name__)


def _get_app_context():
    """Run under a Flask app context whether called from Celery, CLI, or web."""
    if has_app_context():
        return contextlib.nullcontext()
    app_env = os.environ.get(
        "KALANJIYAM_DEPLOYMENT_ENV", os.environ.get("FLASK_ENV", "development")
    )
    return create_config_only_app(app_env).app_context()


# Job bookkeeping
# ---------------


def _start_job(session, job: SearchIndexJob, task_id: str | None) -> None:
    job.status = STATUS_IN_PROGRESS
    job.started_at = datetime.utcnow()
    job.celery_task_id = task_id
    session.commit()


def _finish_job(session, job: SearchIndexJob, status: str, error: str | None = None) -> None:
    job.status = status
    job.completed_at = datetime.utcnow()
    if error:
        job.error_message = error[:2000]
    session.commit()


def _is_cancelled(session, job_id: int) -> bool:
    """Cooperative cancellation: the dashboard writes the marker, we notice.

    Queried as a bare column so the answer comes from the database rather
    than the identity map, without expiring the objects the rebuild is
    currently iterating over.
    """
    row = (
        session.query(SearchIndexJob.error_message)
        .filter(SearchIndexJob.id == job_id)
        .first()
    )
    return bool(row and row[0] == CANCEL_MESSAGE)


def _progress_recorder(session, job: SearchIndexJob):
    def record(processed: int) -> None:
        job.processed_docs = processed
        session.commit()

    return record


def _target_group_ids(session, job: SearchIndexJob) -> list[int]:
    """Organizations a job touches."""
    if job.scope_kind == SCOPE_ORG and job.scope_org_id:
        return [job.scope_org_id]
    if job.scope_kind == SCOPE_PROJECT and job.scope_project_id:
        project = session.query(db.Project).get(job.scope_project_id)
        return indexer.project_group_ids(project) if project else []
    return indexer.all_group_ids(session)


def _estimate_total(session, group_ids: list[int]) -> int:
    """Rough document count, used only to drive the progress bar."""
    if not group_ids:
        return 0
    return (
        session.query(db.Revision.page_id)
        .join(db.Project, db.Project.id == db.Revision.project_id)
        .join(db.ProjectGroups, db.ProjectGroups.project_id == db.Project.id)
        .filter(db.ProjectGroups.group_id.in_(group_ids))
        .distinct()
        .count()
    )


# Tasks
# -----


@app.task(bind=True)
def rebuild_index(self, job_id: int):
    """Rebuild indices from scratch, swapping aliases only on success."""
    with _get_app_context():
        session = q.get_session()
        job = session.query(SearchIndexJob).get(job_id)
        if job is None:
            LOG.warning("SearchIndexJob %s not found", job_id)
            return
        if not is_enabled():
            _finish_job(session, job, STATUS_FAILED, "SEARCH_ENABLED is false")
            return

        _start_job(session, job, getattr(self.request, "id", None))
        settings = get_settings()
        try:
            client = get_client()

            # A single project does not justify rebuilding its whole org;
            # upserting it in place gives the same result far more cheaply.
            if job.scope_kind == SCOPE_PROJECT and job.scope_project_id:
                project = session.query(db.Project).get(job.scope_project_id)
                if project is None:
                    _finish_job(session, job, STATUS_FAILED, "Project no longer exists")
                    return
                written = indexer.index_project(session, project, client=client)
                job.total_docs = written
                job.processed_docs = written
                session.commit()
                _finish_job(session, job, STATUS_COMPLETED)
                return

            group_ids = _target_group_ids(session, job)
            job.total_docs = _estimate_total(session, group_ids)
            session.commit()

            record = _progress_recorder(session, job)
            offset = 0
            failed_total = 0
            for group_id in group_ids:
                if _is_cancelled(session, job_id):
                    _finish_job(session, job, STATUS_CANCELLED)
                    return
                base = offset

                _succeeded, failed = indexer.rebuild_org(
                    client,
                    session,
                    settings.index_prefix,
                    group_id,
                    chunk_size=settings.bulk_chunk_size,
                    on_progress=lambda n, base=base: record(base + n),
                    should_stop=lambda: _is_cancelled(session, job_id),
                )
                failed_total += failed
                offset = job.processed_docs
            job.failed_docs = failed_total
            session.commit()
            _finish_job(session, job, STATUS_COMPLETED)
        except Exception as e:
            LOG.exception("Search index rebuild failed for job %s", job_id)
            _finish_job(session, job, STATUS_FAILED, str(e))
            raise


@app.task(bind=True)
def sync_index(self, job_id: int):
    """Reconcile the index against the database without a full rebuild."""
    with _get_app_context():
        session = q.get_session()
        job = session.query(SearchIndexJob).get(job_id)
        if job is None:
            LOG.warning("SearchIndexJob %s not found", job_id)
            return
        if not is_enabled():
            _finish_job(session, job, STATUS_FAILED, "SEARCH_ENABLED is false")
            return

        _start_job(session, job, getattr(self.request, "id", None))
        settings = get_settings()
        try:
            client = get_client()
            group_ids = _target_group_ids(session, job)
            written = 0
            for group_id in group_ids:
                if _is_cancelled(session, job_id):
                    _finish_job(session, job, STATUS_CANCELLED)
                    return
                result = indexer.sync_org(
                    client,
                    session,
                    settings.index_prefix,
                    group_id,
                    chunk_size=settings.bulk_chunk_size,
                )
                written += result.get("written", 0)
                job.processed_docs = written
                session.commit()
            job.total_docs = written
            session.commit()
            _finish_job(session, job, STATUS_COMPLETED)
        except Exception as e:
            LOG.exception("Search index sync failed for job %s", job_id)
            _finish_job(session, job, STATUS_FAILED, str(e))
            raise


@app.task(bind=True)
def drop_index(self, job_id: int):
    """Delete an organization's indices (or all of them)."""
    with _get_app_context():
        session = q.get_session()
        job = session.query(SearchIndexJob).get(job_id)
        if job is None:
            LOG.warning("SearchIndexJob %s not found", job_id)
            return
        if not is_enabled():
            _finish_job(session, job, STATUS_FAILED, "SEARCH_ENABLED is false")
            return

        _start_job(session, job, getattr(self.request, "id", None))
        settings = get_settings()
        try:
            client = get_client()
            removed = []
            for group_id in _target_group_ids(session, job):
                removed += indexer.drop_org_indices(
                    client, settings.index_prefix, group_id
                )
            job.total_docs = len(removed)
            job.processed_docs = len(removed)
            session.commit()
            _finish_job(session, job, STATUS_COMPLETED)
        except Exception as e:
            LOG.exception("Search index drop failed for job %s", job_id)
            _finish_job(session, job, STATUS_FAILED, str(e))
            raise


@app.task(bind=True)
def index_project(self, project_id: int):
    """Upsert one project and all of its pages.

    Fired after bulk imports and whenever a project's metadata, visibility, or
    organization changes.
    """
    with _get_app_context():
        if not is_enabled():
            return
        session = q.get_session()
        project = session.query(db.Project).get(project_id)
        if project is None:
            LOG.info("Project %s no longer exists; skipping index", project_id)
            return
        try:
            indexer.index_project(session, project)
        except Exception:
            LOG.exception("Failed to index project %s", project_id)
            raise


@app.task(bind=True)
def index_page(self, page_id: int):
    """Upsert a single page after an edit."""
    with _get_app_context():
        if not is_enabled():
            return
        session = q.get_session()
        page = session.query(db.Page).get(page_id)
        if page is None:
            LOG.info("Page %s no longer exists; skipping index", page_id)
            return
        try:
            indexer.index_page(session, page)
        except Exception:
            LOG.exception("Failed to index page %s", page_id)
            raise


@app.task(bind=True)
def remove_project(self, project_id: int):
    """Delete a project's documents after the project itself is deleted."""
    with _get_app_context():
        if not is_enabled():
            return
        settings = get_settings()
        try:
            indexer.delete_project_docs(
                get_client(), settings.index_prefix, project_id
            )
        except Exception:
            LOG.exception("Failed to remove project %s from the index", project_id)


# Enqueue helpers
# ---------------
#
# Callers in request paths use these rather than `.delay()` directly: search
# must never be able to break a page save, so a broker outage is swallowed.


def enqueue_page(page_id: int) -> None:
    """Best-effort: schedule a single page for reindexing."""
    _enqueue(index_page, page_id, what=f"page {page_id}")


def enqueue_project(project_id: int) -> None:
    """Best-effort: schedule a whole project for reindexing."""
    _enqueue(index_project, project_id, what=f"project {project_id}")


def enqueue_project_removal(project_id: int) -> None:
    """Best-effort: schedule removal of a deleted project's documents."""
    _enqueue(remove_project, project_id, what=f"removal of project {project_id}")


def _enqueue(task, arg: int, *, what: str) -> None:
    try:
        if not current_app.config.get("SEARCH_ENABLED"):
            return
    except RuntimeError:
        # No app context (e.g. a seed script). Nothing to schedule.
        return
    try:
        task.apply_async(args=[arg], queue="search_index")
    except Exception as e:
        LOG.warning("Could not schedule search indexing for %s: %s", what, e)
