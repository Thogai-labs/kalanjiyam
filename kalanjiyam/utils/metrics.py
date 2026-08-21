"""Utility functions for logging and querying system metrics, task queues, latencies, and error logs."""

import json
import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from flask import Flask, g, request
from flask_login import current_user
from sqlalchemy import func, desc

import kalanjiyam.database as db
import kalanjiyam.queries as q

LOG = logging.getLogger(__name__)


def record_metric(
    category: str,
    name: str,
    user_id: Optional[int] = None,
    group_id: Optional[int] = None,
    status: Optional[str] = None,
    latency_ms: Optional[float] = None,
    trace_id: Optional[str] = None,
    error_level: Optional[str] = None,
    error_message: Optional[str] = None,
    traceback_str: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[db.SystemMetricLog]:
    """Persist a metric log entry to the database, auto-capturing OpenTelemetry trace_id."""
    try:
        session = q.get_session()
        # Fallback user_id / group_id from context if available
        if user_id is None and current_user and hasattr(current_user, "id") and current_user.is_authenticated:
            user_id = current_user.id
        if group_id is None and current_user and hasattr(current_user, "organization_id") and current_user.is_authenticated:
            group_id = current_user.organization_id

        if not trace_id:
            from kalanjiyam.utils.otel import get_current_trace_id
            trace_id = get_current_trace_id()

        details_json = json.dumps(details) if isinstance(details, dict) else details

        metric = db.SystemMetricLog(
            category=category,
            name=name,
            user_id=user_id,
            group_id=group_id,
            status=status,
            latency_ms=latency_ms,
            trace_id=trace_id,
            error_level=error_level,
            error_message=error_message,
            traceback=traceback_str,
            details=details_json,
            created_at=datetime.utcnow(),
        )
        session.add(metric)
        session.commit()
        return metric
    except Exception as e:
        LOG.warning("Failed to record metric log: %s", e)
        return None


_CELERY_CACHE_TIME = 0
_CELERY_CACHE_DATA = None

def get_active_celery_queues(force_refresh: bool = False) -> Dict[str, Any]:
    """Query Celery workers for real-time task queue states with caching and ping fallback."""
    global _CELERY_CACHE_TIME, _CELERY_CACHE_DATA

    now = time.time()
    if not force_refresh and _CELERY_CACHE_DATA and (now - _CELERY_CACHE_TIME) < 3.0:
        return _CELERY_CACHE_DATA

    active_tasks = []
    reserved_tasks = []
    scheduled_tasks = []
    worker_nodes = []
    celery_online = False

    try:
        from kalanjiyam.tasks import app as celery_app
        inspect = celery_app.control.inspect(timeout=0.8)
        
        active_dict = inspect.active()
        reserved_dict = inspect.reserved()
        scheduled_dict = inspect.scheduled()

        if active_dict is not None:
            celery_online = True
            for node, tasks in active_dict.items():
                if node not in worker_nodes:
                    worker_nodes.append(node)
                for t in tasks:
                    t_info = _decorate_celery_task(t, "RUNNING", node)
                    active_tasks.append(t_info)

        if reserved_dict is not None:
            celery_online = True
            for node, tasks in reserved_dict.items():
                if node not in worker_nodes:
                    worker_nodes.append(node)
                for t in tasks:
                    t_info = _decorate_celery_task(t, "PENDING", node)
                    reserved_tasks.append(t_info)

        if scheduled_dict is not None:
            celery_online = True
            for node, tasks in scheduled_dict.items():
                if node not in worker_nodes:
                    worker_nodes.append(node)
                for t in tasks:
                    t_info = _decorate_celery_task(t, "SCHEDULED", node)
                    scheduled_tasks.append(t_info)

        # Fallback check: if no active tasks, ping workers to check online status
        if not worker_nodes:
            ping_dict = inspect.ping()
            if ping_dict:
                celery_online = True
                worker_nodes = list(ping_dict.keys())

    except Exception as e:
        LOG.warning("Celery inspect error (workers offline or broker error): %s", e)

    # Combine with recent DB queue metric logs
    session = q.get_session()
    db_recent_tasks = (
        session.query(db.SystemMetricLog)
        .filter(db.SystemMetricLog.category == "queue")
        .order_by(desc(db.SystemMetricLog.created_at))
        .limit(50)
        .all()
    )

    db_tasks_list = [m.to_dict() for m in db_recent_tasks]

    # Calculate queue breakdown totals
    all_live = active_tasks + reserved_tasks + scheduled_tasks
    queues_breakdown = {}
    for task in all_live:
        q_name = task.get("queue", "default")
        queues_breakdown[q_name] = queues_breakdown.get(q_name, 0) + 1

    res = {
        "celery_online": celery_online,
        "worker_nodes": worker_nodes,
        "active_count": len(active_tasks),
        "pending_count": len(reserved_tasks) + len(scheduled_tasks),
        "active_tasks": active_tasks,
        "reserved_tasks": reserved_tasks,
        "scheduled_tasks": scheduled_tasks,
        "db_tasks": db_tasks_list,
        "queues_breakdown": queues_breakdown,
    }
    _CELERY_CACHE_DATA = res
    _CELERY_CACHE_TIME = now
    return res


def _decorate_celery_task(task_dict: Dict[str, Any], status: str, worker_node: str) -> Dict[str, Any]:
    """Decorate raw Celery task dictionary with user and enterprise metadata."""
    args = task_dict.get("args", [])
    kwargs = task_dict.get("kwargs", {})
    task_id = task_dict.get("id", "")
    task_name = task_dict.get("name", "Unknown Task")

    # Extract user_id and group_id if passed in kwargs or args
    user_id = kwargs.get("user_id") or task_dict.get("user_id")
    group_id = kwargs.get("group_id") or kwargs.get("organization_id") or task_dict.get("group_id")
    project_slug = kwargs.get("project_slug") or task_dict.get("project_slug")

    user_name = None
    group_name = None

    if user_id:
        u = q.user(user_id) if isinstance(user_id, int) else None
        if u:
            user_name = u.username
    if group_id:
        g = q.group(group_id) if isinstance(group_id, int) else None
        if g:
            group_name = g.name

    delivery_info = task_dict.get("delivery_info", {})
    queue_name = delivery_info.get("routing_key") or delivery_info.get("queue") or "default"

    time_start = task_dict.get("time_start")
    elapsed_sec = round(time.time() - time_start, 2) if time_start else None

    return {
        "id": task_id,
        "name": task_name,
        "status": status,
        "worker": worker_node,
        "queue": queue_name,
        "user_id": user_id,
        "username": user_name or "System",
        "group_id": group_id,
        "group_name": group_name or "—",
        "project_slug": project_slug,
        "elapsed_sec": elapsed_sec,
        "args": str(args)[:100],
        "kwargs": str(kwargs)[:100],
    }


def get_latency_metrics_summary(days: int = 7) -> Dict[str, Any]:
    """Query average and P95 latency metrics grouped by service and enterprise."""
    session = q.get_session()
    cutoff = datetime.utcnow() - timedelta(days=days)

    metrics_rows = (
        session.query(
            db.SystemMetricLog.name,
            db.SystemMetricLog.latency_ms,
            db.SystemMetricLog.category,
        )
        .filter(
            db.SystemMetricLog.category.in_(["latency", "ocr", "translation"]),
            db.SystemMetricLog.created_at >= cutoff,
            db.SystemMetricLog.latency_ms.isnot(None),
        )
        .all()
    )

    if not metrics_rows:
        return {
            "avg_web_latency": 0,
            "avg_ocr_latency": 0,
            "avg_translation_latency": 0,
            "p95_web_latency": 0,
            "by_service": [],
            "recent_latencies": [],
        }

    web_latencies = [row.latency_ms for row in metrics_rows if "web" in row.name or "route" in row.name or row.name.startswith("/")]
    ocr_latencies = [row.latency_ms for row in metrics_rows if "ocr" in row.name]
    trans_latencies = [row.latency_ms for row in metrics_rows if "translation" in row.name or "translate" in row.name]

    def _avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else 0

    def _p95(lst):
        if not lst:
            return 0
        sorted_lst = sorted(lst)
        idx = int(0.95 * len(sorted_lst))
        return round(sorted_lst[min(idx, len(sorted_lst) - 1)], 2)

    # Group by service/name
    services_dict = {}
    for row in metrics_rows:
        s_name = row.name
        if s_name not in services_dict:
            services_dict[s_name] = []
        services_dict[s_name].append(row.latency_ms)

    by_service = []
    for s_name, lat_list in services_dict.items():
        by_service.append({
            "service": s_name,
            "count": len(lat_list),
            "avg_latency": _avg(lat_list),
            "p95_latency": _p95(lat_list),
            "min_latency": round(min(lat_list), 2),
            "max_latency": round(max(lat_list), 2),
        })

    by_service.sort(key=lambda x: x["count"], reverse=True)

    recent_metric_objs = (
        session.query(db.SystemMetricLog)
        .filter(
            db.SystemMetricLog.category.in_(["latency", "ocr", "translation"]),
            db.SystemMetricLog.created_at >= cutoff,
            db.SystemMetricLog.latency_ms.isnot(None),
        )
        .order_by(desc(db.SystemMetricLog.created_at))
        .limit(30)
        .all()
    )
    recent_latencies = [m.to_dict() for m in recent_metric_objs]

    return {
        "avg_web_latency": _avg(web_latencies),
        "avg_ocr_latency": _avg(ocr_latencies),
        "avg_translation_latency": _avg(trans_latencies),
        "p95_web_latency": _p95(web_latencies + ocr_latencies + trans_latencies),
        "by_service": by_service[:15],
        "recent_latencies": recent_latencies,
    }


def get_error_logs_paginated(
    page: int = 1,
    per_page: int = 20,
    level: Optional[str] = None,
    group_id: Optional[int] = None,
    user_id: Optional[int] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve error log records with filtering and pagination."""
    session = q.get_session()
    query = session.query(db.SystemMetricLog).filter(db.SystemMetricLog.category == "error")

    if level and level != "ALL":
        query = query.filter(db.SystemMetricLog.error_level == level)
    if group_id:
        query = query.filter(db.SystemMetricLog.group_id == group_id)
    if user_id:
        query = query.filter(db.SystemMetricLog.user_id == user_id)
    if search:
        search_fmt = f"%{search}%"
        query = query.filter(
            (db.SystemMetricLog.name.ilike(search_fmt))
            | (db.SystemMetricLog.error_message.ilike(search_fmt))
            | (db.SystemMetricLog.traceback.ilike(search_fmt))
        )

    total = query.count()
    num_pages = (total + per_page - 1) // per_page if total > 0 else 1

    records = (
        query.order_by(desc(db.SystemMetricLog.created_at))
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "records": [r.to_dict() for r in records],
        "total": total,
        "page": page,
        "per_page": per_page,
        "num_pages": num_pages,
    }


def init_metrics_middleware(app: Flask) -> None:
    """Attach Flask request timing and unhandled exception hooks."""

    @app.before_request
    def _start_request_timer():
        g._start_time = time.time()

    @app.after_request
    def _log_request_latency(response):
        if hasattr(g, "_start_time"):
            duration_ms = round((time.time() - g._start_time) * 1000, 2)
            # Log endpoints that take longer than 30ms or return error statuses
            path = request.path
            # Ignore static assets to reduce log noise
            if not path.startswith("/static"):
                u_id = current_user.id if current_user and hasattr(current_user, "id") and current_user.is_authenticated else None
                g_id = current_user.organization_id if current_user and hasattr(current_user, "organization_id") and current_user.is_authenticated else None

                status_code = response.status_code
                if status_code >= 400 or duration_ms > 200:
                    record_metric(
                        category="latency",
                        name=f"{request.method} {path}",
                        user_id=u_id,
                        group_id=g_id,
                        status=str(status_code),
                        latency_ms=duration_ms,
                        details={"ip": request.remote_addr, "status": status_code},
                    )

                if status_code >= 500:
                    record_metric(
                        category="error",
                        name=f"HTTP {status_code}: {request.method} {path}",
                        user_id=u_id,
                        group_id=g_id,
                        status="FAILED",
                        error_level="ERROR",
                        error_message=f"HTTP Server Error {status_code} at {path}",
                        details={"ip": request.remote_addr, "path": path},
                    )
        return response

    @app.teardown_request
    def _handle_exception(exception=None):
        if exception:
            u_id = current_user.id if current_user and hasattr(current_user, "id") and current_user.is_authenticated else None
            g_id = current_user.organization_id if current_user and hasattr(current_user, "organization_id") and current_user.is_authenticated else None
            record_metric(
                category="error",
                name=f"Exception: {type(exception).__name__}",
                user_id=u_id,
                group_id=g_id,
                status="FAILED",
                error_level="CRITICAL",
                error_message=str(exception),
                traceback_str=traceback.format_exc(),
                details={"path": request.path, "method": request.method},
            )
