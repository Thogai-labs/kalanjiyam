"""Views and API endpoints for Multi Batch Processes."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta

import redis
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    render_template,
    request,
    url_for,
)
from flask_babel import lazy_gettext as _l
from flask_login import current_user
from sqlalchemy import func

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.models.batch import BatchItem, BatchJob
from kalanjiyam.utils.translation_engine import (
    build_translation_choices,
    get_supported_languages_list,
)
from kalanjiyam.utils.user_tasks import add_user_task, get_user_identifier
from kalanjiyam.views.proofing.decorators import p2_required

LOG = logging.getLogger(__name__)

bp = Blueprint("multi_batch", __name__)


def _get_redis_client():
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url)


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@p2_required
def index():
    """Two-window interface for Multi Batch Processes.

    Window 1: List of documents sorted/filtered by uploaded date range.
    Window 2: Operations configuration (OCR adhering to v2 contract, Translation, or both)
              and live status monitor.
    """
    session = q.get_session()
    device_fp = request.cookies.get("device_fingerprint")

    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )

    # Sort strictly by uploaded date (newest first)
    projects_query = base_query.order_by(db.Project.created_at.desc())
    all_projects = projects_query.all()

    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    yesterday_start = today_start - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)
    one_week_ago = now - timedelta(days=7)
    one_month_ago = now - timedelta(days=30)

    project_ids = [p.id for p in all_projects]

    page_counts = {}
    ocred_counts = {}
    trans_counts = {}

    if project_ids:
        # Batch aggregate page counts
        page_counts = dict(
            session.query(db.Page.project_id, func.count(db.Page.id))
            .filter(db.Page.project_id.in_(project_ids))
            .group_by(db.Page.project_id)
            .all()
        )

        # Batch aggregate OCRed page counts
        ocred_counts = dict(
            session.query(db.Page.project_id, func.count(db.Page.id))
            .filter(
                db.Page.project_id.in_(project_ids),
                db.Page.ocr_bounding_boxes.isnot(None),
                db.Page.ocr_bounding_boxes != "",
            )
            .group_by(db.Page.project_id)
            .all()
        )

        # Batch aggregate Translated page counts
        trans_counts = dict(
            session.query(db.Page.project_id, func.count(func.distinct(db.Translation.page_id)))
            .join(db.Translation, db.Translation.page_id == db.Page.id)
            .filter(
                db.Page.project_id.in_(project_ids),
                db.Translation.status == "completed",
            )
            .group_by(db.Page.project_id)
            .all()
        )

    # Construct serialized document list with date range classifications and progress stats
    documents_data = []
    bucket_counts = {
        "all": len(all_projects),
        "today": 0,
        "yesterday": 0,
        "2_days": 0,
        "1_week": 0,
        "1_month": 0,
    }

    for p in all_projects:
        created = p.created_at or now

        # Date bucket classification
        if created >= today_start:
            bucket = "today"
            date_label = f"Today, {created.strftime('%H:%M')}"
            bucket_counts["today"] += 1
            bucket_counts["2_days"] += 1
            bucket_counts["1_week"] += 1
            bucket_counts["1_month"] += 1
        elif created >= yesterday_start:
            bucket = "yesterday"
            date_label = f"Yesterday, {created.strftime('%H:%M')}"
            bucket_counts["yesterday"] += 1
            bucket_counts["2_days"] += 1
            bucket_counts["1_week"] += 1
            bucket_counts["1_month"] += 1
        elif created >= two_days_ago:
            bucket = "2_days"
            days_ago = (now - created).days or 2
            date_label = f"{days_ago} days ago"
            bucket_counts["2_days"] += 1
            bucket_counts["1_week"] += 1
            bucket_counts["1_month"] += 1
        elif created >= one_week_ago:
            bucket = "1_week"
            days_ago = (now - created).days
            date_label = f"{days_ago} days ago"
            bucket_counts["1_week"] += 1
            bucket_counts["1_month"] += 1
        elif created >= one_month_ago:
            bucket = "1_month"
            date_label = created.strftime("%b %d, %Y")
            bucket_counts["1_month"] += 1
        else:
            bucket = "older"
            date_label = created.strftime("%b %d, %Y")

        tot = page_counts.get(p.id, 0)
        ocr = ocred_counts.get(p.id, 0)
        trans = trans_counts.get(p.id, 0)

        is_fully_ocred = (tot > 0 and ocr >= tot)
        is_partially_ocred = (0 < ocr < tot)
        is_fully_translated = (tot > 0 and trans >= tot)
        is_partially_translated = (0 < trans < tot)

        documents_data.append({
            "id": p.id,
            "slug": p.slug,
            "display_title": p.display_title or p.slug,
            "created_at_iso": created.isoformat(),
            "date_bucket": bucket,
            "date_label": date_label,
            "total_pages": tot,
            "ocred_pages": ocr,
            "trans_pages": trans,
            "is_fully_ocred": is_fully_ocred,
            "is_partially_ocred": is_partially_ocred,
            "is_not_ocred": (ocr == 0),
            "is_fully_translated": is_fully_translated,
            "is_partially_translated": is_partially_translated,
            "is_not_translated": (trans == 0),
        })

    # Prepare OCR Engine Choices (Adheres to v2 contract)
    from kalanjiyam.utils.ocr_client import get_available_engines
    from kalanjiyam.utils.ocr_types import (
        build_engine_choices,
        normalize_engine,
        REVERSE_ENGINE_MAP,
        SUPPORTED_ENGINES,
    )

    system_settings = q.get_system_settings()
    default_ocr_engine = getattr(system_settings, "default_ocr_engine", "gemma_ocr") or "gemma_ocr"
    recommended_ocr_engine = getattr(system_settings, "recommended_ocr_engine", None) or default_ocr_engine
    default_engine_value = REVERSE_ENGINE_MAP.get(default_ocr_engine, "1")

    ocr_status = get_available_engines()
    available_ocr_engines = ocr_status.get("engines", []) if isinstance(ocr_status, dict) else (ocr_status or [])
    if not available_ocr_engines:
        available_ocr_engines = list(SUPPORTED_ENGINES)

    ocr_engine_choices = build_engine_choices(
        available_ocr_engines,
        is_super_admin=getattr(current_user, "is_super_admin", False),
        recommended_engine=recommended_ocr_engine,
    )

    # Prepare Translation Choices
    from kalanjiyam.utils.translation_engine import (
        build_translation_choices,
        normalize_translation_engine,
        REVERSE_TRANSLATION_ENGINE_MAP,
    )
    default_trans_engine = getattr(system_settings, "default_translation_engine", "indictrans3") or "indictrans3"
    rec_trans_engine = getattr(system_settings, "recommended_translation_engine", None)
    default_trans_engine_value = REVERSE_TRANSLATION_ENGINE_MAP.get(default_trans_engine, "1")
    trans_engine_choices = build_translation_choices(
        is_super_admin=getattr(current_user, "is_super_admin", False),
        recommended_engine=rec_trans_engine,
        default_engine=default_trans_engine,
    )
    supported_languages = get_supported_languages_list()

    return render_template(
        "proofing/multi-batch.html",
        documents=documents_data,
        bucket_counts=bucket_counts,
        ocr_engines=ocr_engine_choices,
        default_ocr_engine=default_ocr_engine,
        default_engine_value=default_engine_value,
        trans_engines=trans_engine_choices,
        default_trans_engine=default_trans_engine,
        default_trans_engine_value=default_trans_engine_value,
        languages=supported_languages,
    )


@bp.route("/run", methods=["POST"])
@p2_required
def run():
    """Trigger background multi-batch operation on s3_batch queue."""
    session = q.get_session()
    device_fp = request.cookies.get("device_fingerprint")

    # Read payload from JSON or Form
    if request.is_json:
        payload = request.get_json() or {}
    else:
        payload = request.form.to_dict()
        if "project_ids[]" in request.form:
            payload["project_ids"] = request.form.getlist("project_ids[]")
        elif "project_ids" in request.form:
            payload["project_ids"] = request.form.getlist("project_ids")

    raw_project_ids = payload.get("project_ids", [])
    if isinstance(raw_project_ids, str):
        try:
            raw_project_ids = json.loads(raw_project_ids)
        except Exception:
            raw_project_ids = [int(x.strip()) for x in raw_project_ids.split(",") if x.strip()]

    try:
        project_ids = [int(x) for x in raw_project_ids]
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": _l("Invalid project IDs provided.")}), 400

    if not project_ids:
        return jsonify({"success": False, "error": _l("Please select at least one document.")}), 400

    do_ocr = payload.get("do_ocr") in (True, "true", "1", 1, "on")
    do_translate = payload.get("do_translate") in (True, "true", "1", 1, "on")

    if not do_ocr and not do_translate:
        return jsonify({"success": False, "error": _l("Please select at least one operation (OCR or Translation).")}), 400

    from kalanjiyam.utils.ocr_types import normalize_engine
    from kalanjiyam.utils.translation_engine import normalize_translation_engine

    ocr_engine = normalize_engine(payload.get("ocr_engine") or "gemma_ocr")
    ocr_language = payload.get("ocr_language") or "auto"
    trans_engine = normalize_translation_engine(payload.get("trans_engine") or "indictrans3")
    source_lang = payload.get("source_lang") or "sa"
    target_lang = payload.get("target_lang") or "en"

    # Verify authorization for all requested projects
    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )
    authorized_projects = base_query.filter(db.Project.id.in_(project_ids)).all()
    authorized_ids = [p.id for p in authorized_projects]

    if not authorized_ids:
        return jsonify({"success": False, "error": _l("None of the selected documents are accessible.")}), 403

    # Create BatchJob in database
    batch_job = BatchJob(
        target_uri="ui://multi-batch",
        status="IN_PROGRESS",
        job_type="MULTI_BATCH",
    )
    session.add(batch_job)
    session.flush()

    # Create BatchItems for each authorized project
    for proj in authorized_projects:
        batch_item = BatchItem(
            job_id=batch_job.id,
            file_path=f"{proj.display_title or proj.slug} ({proj.slug})",
            project_id=proj.id,
            status="PENDING",
            total_pages=len(proj.pages),
            source_lang=source_lang if do_translate else None,
            target_lang=target_lang if do_translate else None,
            engine=ocr_engine if do_ocr else trans_engine,
        )
        session.add(batch_item)

    session.commit()

    # Dispatch Celery task strictly on s3_batch queue
    from kalanjiyam.tasks.s3_batch import run_multi_batch_operation

    task = run_multi_batch_operation.apply_async(
        args=[
            batch_job.id,
            authorized_ids,
            do_ocr,
            ocr_engine,
            ocr_language,
            do_translate,
            trans_engine,
            source_lang,
            target_lang,
        ],
        queue="s3_batch",
    )

    # Cache task-to-job mapping in Redis for quick URL and task resolution
    r_client = _get_redis_client()
    try:
        r_client.setex(f"multi_batch_task:{task.id}", 86400 * 7, str(batch_job.id))
        r_client.setex(f"multi_batch_job_task:{batch_job.id}", 86400 * 7, str(task.id))
    except Exception as e:
        LOG.warning(f"Error caching multi_batch task mapping in Redis: {e}")

    # Register in user tasks system for navbar badge and active task polling
    user_id = get_user_identifier(current_user, request)
    if user_id:
        add_user_task(
            user_identifier=user_id,
            task_id=task.id,
            task_type="multi_batch",
            project_slug="multi-batch",
            project_title=f"Multi Batch ({len(authorized_ids)} documents)",
            extra_info={
                "job_id": batch_job.id,
                "do_ocr": do_ocr,
                "do_translate": do_translate,
                "ocr_engine": ocr_engine,
                "trans_engine": trans_engine,
                "queue": "s3_batch",
            },
        )

    return jsonify({
        "success": True,
        "job_id": batch_job.id,
        "task_id": task.id,
        "message": _l("Multi-batch process started on s3_batch queue."),
    })


def _safe_decode(val):
    if isinstance(val, bytes):
        return val.decode("utf-8")
    return val


def _resolve_job_id(task_or_job_id: str | int) -> int | None:
    """Resolve a Celery task ID or BatchJob ID to its integer BatchJob ID."""
    if not task_or_job_id:
        return None

    str_id = str(task_or_job_id).strip()
    r_client = _get_redis_client()

    # 1. Try Redis direct mapping for task_id -> job_id
    try:
        mapped_job_id = r_client.get(f"multi_batch_task:{str_id}")
        if mapped_job_id:
            val = _safe_decode(mapped_job_id)
            if val and val.isdigit():
                return int(val)
    except Exception as e:
        LOG.warning(f"Error querying Redis multi_batch_task for {str_id}: {e}")

    # 2. If it's a numeric ID, check if it's a valid BatchJob
    if str_id.isdigit():
        jid = int(str_id)
        session = q.get_session()
        job = session.query(BatchJob).get(jid)
        if job and job.job_type == "MULTI_BATCH":
            return jid
        try:
            if r_client.exists(f"multi_batch_job:{jid}"):
                return jid
        except Exception:
            pass
        if job:
            return jid

    # 3. Check user tasks in Redis
    try:
        user_id = get_user_identifier(current_user, request)
        if user_id:
            key = f"user_tasks:{user_id}"
            task_data = r_client.hget(key, str_id)
            if task_data:
                info = json.loads(_safe_decode(task_data))
                extra = info.get("extra_info") or {}
                if extra.get("job_id"):
                    return int(extra["job_id"])
    except Exception as e:
        LOG.warning(f"Error checking user tasks for {str_id}: {e}")

    # 4. Search active redis keys multi_batch_job:*
    try:
        keys = r_client.keys("multi_batch_job:*")
        for k in keys:
            raw = r_client.get(k)
            if raw:
                data = json.loads(_safe_decode(raw))
                if data.get("task_id") == str_id and data.get("job_id"):
                    return int(data["job_id"])
    except Exception as e:
        LOG.warning(f"Error scanning multi_batch_job keys for {str_id}: {e}")

    return None


def _get_job_status_data(job_id: int, task_id: str | None = None) -> dict | None:
    """Retrieve full batch job status data from Redis or database fallback."""
    r_client = _get_redis_client()
    redis_key = f"multi_batch_job:{job_id}"

    state_data = None
    try:
        raw_state = r_client.get(redis_key)
        if raw_state:
            state_data = json.loads(_safe_decode(raw_state))
    except Exception as e:
        LOG.warning(f"Error reading Redis status for multi_batch_job {job_id}: {e}")

    session = q.get_session()
    job = session.query(BatchJob).get(job_id)

    if not state_data and not job:
        return None

    # Resolve task_id if not present
    if not task_id:
        try:
            mapped_task = r_client.get(f"multi_batch_job_task:{job_id}")
            if mapped_task:
                task_id = _safe_decode(mapped_task)
        except Exception:
            pass

    if state_data:
        if task_id and not state_data.get("task_id"):
            state_data["task_id"] = task_id
        if not state_data.get("job_id"):
            state_data["job_id"] = job_id
        return state_data

    # Fallback to database
    items = session.query(BatchItem).filter_by(job_id=job.id).all()
    completed_items = sum(1 for it in items if it.status in ("COMPLETED", "SKIPPED"))
    failed_items = sum(1 for it in items if it.status == "FAILED")
    total_pages = sum(it.total_pages or 0 for it in items)

    project_ids = [it.project_id for it in items if it.project_id]
    projects_map = {}
    if project_ids:
        projs = session.query(db.Project).filter(db.Project.id.in_(project_ids)).all()
        projects_map = {p.id: p for p in projs}

    return {
        "job_id": job.id,
        "task_id": task_id,
        "status": job.status,
        "total_projects": len(items),
        "completed_projects": completed_items,
        "total_pages": total_pages,
        "processed_pages": sum(it.total_pages or 0 for it in items if it.status in ("COMPLETED", "SKIPPED")),
        "skipped_ocr_pages": 0,
        "skipped_trans_pages": 0,
        "skipped_ocr_books": sum(1 for it in items if it.status == "SKIPPED"),
        "skipped_trans_books": 0,
        "error_count": failed_items,
        "percent": int((completed_items / max(1, len(items))) * 100) if items else 0,
        "started_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "projects": [
            {
                "id": it.project_id,
                "slug": projects_map.get(it.project_id).slug if it.project_id in projects_map else (it.file_path or str(it.project_id)),
                "title": projects_map.get(it.project_id).display_title if it.project_id in projects_map else it.file_path,
                "total_pages": it.total_pages or 0,
                "status": it.status,
                "ocr_status": "COMPLETED" if it.status == "COMPLETED" else it.status,
                "ocr_message": it.error_message if it.status == "FAILED" else "",
                "trans_status": "COMPLETED" if it.status == "COMPLETED" else it.status,
                "trans_message": it.error_message if it.status == "FAILED" else "",
                "message": it.error_message or it.status,
            }
            for it in items
        ],
        "logs": [f"Batch job #{job.id} status is {job.status}."],
    }


@bp.route("/status/<int:job_id>", methods=["GET"])
@bp.route("/status/<task_id>", methods=["GET"])
@p2_required
def status(task_id: str | None = None, job_id: int | None = None):
    """Query live progress of a multi-batch operation by task ID or job ID."""
    target_id = task_id or (str(job_id) if job_id is not None else None)
    if not target_id:
        return jsonify({"success": False, "error": _l("Batch job not found.")}), 404

    resolved_job_id = _resolve_job_id(target_id)
    if not resolved_job_id:
        return jsonify({"success": False, "error": _l("Batch job not found.")}), 404

    data = _get_job_status_data(resolved_job_id, task_id=str(task_id or job_id))
    if not data:
        return jsonify({"success": False, "error": _l("Batch job not found.")}), 404

    return jsonify({"success": True, "data": data})


@bp.route("/<task_id>", methods=["GET"])
@p2_required
def task_status_page(task_id: str):
    """Dedicated status page for a multi-batch task/process."""
    if task_id in ("run", "status"):
        abort(404)

    resolved_job_id = _resolve_job_id(task_id)
    if not resolved_job_id:
        abort(404, description=_l("Multi-batch task not found."))

    session = q.get_session()
    device_fp = request.cookies.get("device_fingerprint")

    # Verify authorization: user must have access to at least one project in the job
    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )
    items = session.query(BatchItem).filter_by(job_id=resolved_job_id).all()
    project_ids = [it.project_id for it in items if it.project_id]

    if project_ids:
        authorized_count = base_query.filter(db.Project.id.in_(project_ids)).count()
        if authorized_count == 0 and not getattr(current_user, "is_super_admin", False):
            abort(403, description=_l("You are not authorized to view this batch task."))

    state_data = _get_job_status_data(resolved_job_id, task_id=task_id)
    if not state_data:
        abort(404, description=_l("Multi-batch task details not found."))

    return render_template(
        "proofing/multi-batch-status.html",
        task_id=task_id,
        job_id=resolved_job_id,
        status_data=state_data,
    )
