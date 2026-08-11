"""Actions and status reporting behind the admin search dashboards.

The scope of every action is decided here from the caller's identity, never
from the request body: an org admin can only ever touch their own
organization's index.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import kalanjiyam.database as db
from kalanjiyam.models.search import (
    CANCEL_MESSAGE,
    JOB_DROP,
    JOB_REBUILD,
    JOB_SYNC,
    SCOPE_ALL,
    SCOPE_ORG,
    SCOPE_PROJECT,
    STATUS_IN_PROGRESS,
    STATUS_PENDING,
    SearchIndexJob,
)
from kalanjiyam.search import indexer
from kalanjiyam.search.client import get_client, get_settings, health, is_enabled

LOG = logging.getLogger(__name__)

ACTIVE_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS)


class ActionError(Exception):
    """A dashboard action could not be started; the message is user-facing."""


def _active_job_for_scope(session, *, org_id: int | None, project_id: int | None):
    query = session.query(SearchIndexJob).filter(
        SearchIndexJob.status.in_(ACTIVE_STATUSES)
    )
    if project_id:
        query = query.filter(SearchIndexJob.scope_project_id == project_id)
    elif org_id:
        query = query.filter(SearchIndexJob.scope_org_id == org_id)
    else:
        query = query.filter(SearchIndexJob.scope_kind == SCOPE_ALL)
    return query.first()


def start_job(
    session,
    *,
    job_type: str,
    org_id: int | None = None,
    project_id: int | None = None,
    requested_by_id: int | None = None,
) -> SearchIndexJob:
    """Create a job row and dispatch its task.

    Refuses to start a second job for a scope that already has one running --
    two concurrent rebuilds of the same org would fight over the alias.
    """
    if not is_enabled():
        raise ActionError("Search is disabled. Set SEARCH_ENABLED=true to use it.")

    existing = _active_job_for_scope(session, org_id=org_id, project_id=project_id)
    if existing is not None:
        raise ActionError(
            f"Job #{existing.id} is already running for this scope. "
            "Wait for it to finish or cancel it first."
        )

    if project_id:
        scope_kind = SCOPE_PROJECT
    elif org_id:
        scope_kind = SCOPE_ORG
    else:
        scope_kind = SCOPE_ALL

    job = SearchIndexJob(
        job_type=job_type,
        scope_kind=scope_kind,
        scope_org_id=org_id,
        scope_project_id=project_id,
        requested_by_id=requested_by_id,
    )
    session.add(job)
    # Commit before dispatching: the worker looks the job up by id.
    session.commit()

    from kalanjiyam.tasks.search_index import drop_index, rebuild_index, sync_index

    task = {JOB_REBUILD: rebuild_index, JOB_SYNC: sync_index, JOB_DROP: drop_index}[
        job_type
    ]
    try:
        task.apply_async(args=[job.id], queue="search_index")
    except Exception as e:
        LOG.exception("Could not dispatch search index job %s", job.id)
        job.status = "FAILED"
        job.error_message = f"Could not reach the task queue: {e}"
        job.completed_at = datetime.utcnow()
        session.commit()
        raise ActionError(
            "Could not reach the background task queue. Is the worker running?"
        ) from e

    return job


def cancel_job(session, job_id: int) -> bool:
    """Ask a running job to stop. It notices at its next checkpoint."""
    job = session.query(SearchIndexJob).get(job_id)
    if job is None or job.is_terminal:
        return False
    job.error_message = CANCEL_MESSAGE
    session.commit()
    return True


def recent_jobs(session, *, org_id: int | None = None, limit: int = 20):
    query = session.query(SearchIndexJob)
    if org_id is not None:
        query = query.filter(SearchIndexJob.scope_org_id == org_id)
    return query.order_by(SearchIndexJob.id.desc()).limit(limit).all()


def job_summary(job: SearchIndexJob) -> dict[str, Any]:
    """Serializable shape for the polling endpoint."""
    return {
        "id": job.id,
        "job_type": job.job_type,
        "scope_kind": job.scope_kind,
        "scope_org_id": job.scope_org_id,
        "scope_project_id": job.scope_project_id,
        "status": job.status,
        "total_docs": job.total_docs,
        "processed_docs": job.processed_docs,
        "failed_docs": job.failed_docs,
        "percent": round(job.percent, 1),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "error_message": job.error_message,
        "is_terminal": job.is_terminal,
    }


def dashboard_state(session, org_ids: list[int]) -> dict[str, Any]:
    """Everything the dashboard shows, for the given organizations.

    Never raises: the dashboard has to render even with the cluster down,
    because that is exactly when someone needs to look at it.
    """
    info = health()
    state: dict[str, Any] = {
        "health": info,
        "orgs": [],
        "total_pages": 0,
        "total_projects": 0,
        "total_size_bytes": 0,
        "ungrouped_projects": 0,
    }

    try:
        state["ungrouped_projects"] = indexer.ungrouped_project_count(session)
    except Exception:
        LOG.warning("Could not count ungrouped projects", exc_info=True)

    groups = {g.id: g for g in session.query(db.Group).filter(db.Group.id.in_(org_ids))} if org_ids else {}

    if not (info["enabled"] and info["reachable"]):
        state["orgs"] = [
            {
                "id": org_id,
                "name": getattr(groups.get(org_id), "name", f"Organization {org_id}"),
                "slug": getattr(groups.get(org_id), "slug", ""),
                "pages_count": None,
                "projects_count": None,
                "size_bytes": None,
            }
            for org_id in org_ids
        ]
        return state

    settings = get_settings()
    client = get_client()
    for org_id in org_ids:
        try:
            stats = indexer.org_stats(client, settings.index_prefix, org_id)
        except Exception:
            LOG.warning("Could not read index stats for org %s", org_id, exc_info=True)
            continue
        size = stats["pages_size_bytes"] + stats["projects_size_bytes"]
        group = groups.get(org_id)
        state["orgs"].append(
            {
                "id": org_id,
                "name": getattr(group, "name", f"Organization {org_id}"),
                "slug": getattr(group, "slug", ""),
                "pages_count": stats["pages_count"],
                "projects_count": stats["projects_count"],
                "size_bytes": size,
                "indices": stats["pages_indices"] + stats["projects_indices"],
            }
        )
        state["total_pages"] += stats["pages_count"]
        state["total_projects"] += stats["projects_count"]
        state["total_size_bytes"] += size

    return state


def indexable_org_ids(session) -> list[int]:
    """Organizations that own at least one project."""
    return indexer.all_group_ids(session)


def projects_for_picker(session, org_ids: list[int]) -> list[db.Project]:
    """Projects an admin may reindex individually."""
    if not org_ids:
        return []
    return (
        session.query(db.Project)
        .filter(db.Project.groups.any(db.Group.id.in_(org_ids)))
        .order_by(db.Project.display_title)
        .all()
    )


def ensure_indices(session, org_ids: list[int]) -> int:
    """Create empty indices and aliases for organizations missing them."""
    if not is_enabled():
        raise ActionError("Search is disabled. Set SEARCH_ENABLED=true to use it.")
    settings = get_settings()
    client = get_client()
    for org_id in org_ids:
        indexer.ensure_org_indices(client, settings.index_prefix, org_id)
    return len(org_ids)


def project_is_in_orgs(session, project_id: int, org_ids: list[int]) -> bool:
    """Guard: an admin may only reindex projects inside their own scope."""
    if not org_ids:
        return False
    return (
        session.query(db.ProjectGroups)
        .filter(
            db.ProjectGroups.project_id == project_id,
            db.ProjectGroups.group_id.in_(org_ids),
        )
        .first()
        is not None
    )
