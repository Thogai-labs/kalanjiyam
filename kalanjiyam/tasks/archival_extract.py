"""Full-text archival description extraction.

Reads every page of a project in token-budgeted windows, asks the extraction
service to describe each one, verifies the evidence it gets back, and reduces the
windows into a single description.

Structured after `kalanjiyam.tasks.metadata`, whose scaffolding is proven: a Redis
lock so two runs cannot overlap, progress the tab can poll, isolated failures so
one bad window does not lose the rest, and -- most importantly -- **a regenerate
must not destroy curation**. Curated field values live in their own column and are
never written by a run.

What is new here is that the whole document is read rather than sampled. Fifteen
of the twenty-two tags in the client's schema are recall problems (who is named
anywhere in this file?), and sampling answers a different question. That makes a
run expensive, so two things keep it affordable:

* **Windows are hashed.** A re-run only re-asks for windows whose text changed,
  which matters because proofreading corrections arrive continuously.
* **It has its own Celery queue.** A full pass is many minutes of GPU on the same
  service that answers live OCR.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import datetime

import redis
from flask import has_app_context

from config import create_config_only_app
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.tasks import app
from kalanjiyam.utils import archival_description as ad
from kalanjiyam.utils import archival_reduce as ar
from kalanjiyam.utils import archival_taxonomy as at
from kalanjiyam.utils import metadata_client as mc
from kalanjiyam.utils import project_metadata as pm

LOG = logging.getLogger(__name__)

STATUS_PENDING = "PENDING"
STATUS_RUNNING = "IN_PROGRESS"
STATUS_OK = "COMPLETED"
STATUS_PARTIAL = "PARTIAL"
STATUS_FAILED = "FAILED"
STATUS_SKIPPED = "SKIPPED"

#: A full pass over a large document is genuinely long, so the lock outlives the
#: 30 minutes `tasks.metadata` allows itself.
_LOCK_TTL = 60 * 90
_PROGRESS_TTL = 60 * 60 * 2

_redis = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def lock_key(project_id: int) -> str:
    return f"archival_extract:lock:{project_id}"


def progress_key(project_id: int) -> str:
    return f"archival_extract:progress:{project_id}"


def get_progress(project_id: int) -> dict | None:
    """Read the current run's progress. Best-effort; never raises.

    The tab reads this on every page load, so an unreachable Redis must not take
    the page down with it.
    """
    try:
        blob = _redis.get(progress_key(project_id))
    except redis.RedisError:
        LOG.warning("could not read extraction progress for project %s", project_id)
        return None
    if not blob:
        return None
    try:
        return json.loads(blob)
    except (ValueError, TypeError):
        return None


def _set_progress(project_id: int, **fields) -> None:
    payload = get_progress(project_id) or {}
    payload.update(fields)
    payload["updated_at"] = datetime.utcnow().isoformat()
    try:
        _redis.setex(progress_key(project_id), _PROGRESS_TTL, json.dumps(payload))
    except redis.RedisError:
        # Progress reporting is cosmetic; never fail a run over it.
        LOG.warning("could not record extraction progress for %s", project_id)


def _get_app_context():
    """Run under a Flask app context whether called from Celery, CLI, or web."""
    if has_app_context():
        return contextlib.nullcontext()
    app_env = os.environ.get(
        "KALANJIYAM_DEPLOYMENT_ENV", os.environ.get("FLASK_ENV", "development")
    )
    return create_config_only_app(app_env).app_context()


@app.task(bind=True)
def extract_archival_metadata(
    self, project_id: int, force: bool = False, enqueued_at: str | None = None
) -> dict:
    """Describe one project. Returns a summary of the run."""
    with _get_app_context():
        lock = lock_key(project_id)
        locked = False
        try:
            locked = bool(
                _redis.set(lock, self.request.id or "1", nx=True, ex=_LOCK_TTL)
            )
            if not locked:
                LOG.info("archival extraction already running for %s", project_id)
                return {"status": "locked"}
        except redis.RedisError:
            # Without Redis we cannot guarantee a single in-flight run. A
            # duplicate run wastes GPU time but is not destructive, so prefer
            # running to refusing.
            LOG.warning("could not acquire extraction lock for %s", project_id)

        try:
            return _run(project_id, force=force, enqueued_at=enqueued_at)
        except Exception as e:  # noqa: BLE001 - surfaced to the UI
            LOG.exception("archival extraction failed for project %s", project_id)
            _set_progress(
                project_id, status=STATUS_FAILED, stage="failed", error=str(e)
            )
            return {"status": STATUS_FAILED, "error": str(e)}
        finally:
            if locked:
                try:
                    _redis.delete(lock)
                except redis.RedisError:
                    LOG.warning("could not release extraction lock for %s", project_id)


def _previous_hashes(session, project_id: int) -> dict:
    """window_index -> text_hash from the last completed run.

    Used to skip windows whose text has not changed since. A re-extraction after
    a one-page proofreading fix should cost one call, not thirty.
    """
    last = (
        session.query(db.MetadataExtractionRun)
        .filter(
            db.MetadataExtractionRun.project_id == project_id,
            db.MetadataExtractionRun.status.in_((STATUS_OK, STATUS_PARTIAL)),
        )
        .order_by(db.MetadataExtractionRun.id.desc())
        .first()
    )
    if last is None:
        return {}
    return {
        w.window_index: (w.text_hash, w.raw_response)
        for w in last.windows
        if w.status == STATUS_OK and w.text_hash
    }


def _run(
    project_id: int, *, force: bool, enqueued_at: str | None = None
) -> dict:
    session = q.get_session()
    project = session.query(db.Project).filter(db.Project.id == project_id).first()
    if project is None:
        raise ValueError(f"project {project_id} not found")

    start_time = datetime.utcnow()
    if enqueued_at:
        try:
            start_time = datetime.fromisoformat(enqueued_at)
        except (ValueError, TypeError):
            start_time = datetime.utcnow()

    _set_progress(
        project_id, status=STATUS_RUNNING, stage="resolving pages", done=0, error=None
    )
    tracks = pm.resolve_extraction_tracks(session, project_id)
    if not tracks:
        _set_progress(
            project_id,
            status=STATUS_FAILED,
            stage="no text",
            error="This project has no OCR'd or edited pages yet.",
        )
        return {"status": STATUS_FAILED, "error": "no text"}

    _set_progress(project_id, stage="profiling scripts")
    profile = pm.script_profile(session, list(tracks.values()))
    scripts = profile.get("scripts") or {}
    confidences = pm.page_ocr_confidence(session, project_id)

    run = db.MetadataExtractionRun(
        project_id=project_id,
        status=STATUS_RUNNING,
        taxonomy_version=at.TAXONOMY_VERSION,
        contract_version=mc.CONTRACT_VERSION,
        pages_total=len(tracks),
        created_at=start_time,
    )
    session.add(run)
    session.commit()

    previous = {} if force else _previous_hashes(session, project_id)
    tags = [tag.code for tag in at.extractable_tags()]
    language_hint = _language_hint(project)

    window_fields: list[dict] = []
    window_stats: list[dict] = []
    pages_read: set[str] = set()
    completed = failed = 0

    for window in pm.iter_windows(session, tracks, scripts, confidences):
        _set_progress(
            project_id,
            stage=f"reading window {window.index} of {window.total}",
            done=window.index,
            total=window.total,
        )

        record = db.MetadataWindow(
            run_id=run.id,
            window_index=window.index,
            page_slugs=window.page_slugs,
            text_hash=window.text_hash,
            chars_in=window.chars,
            fields_attempted=len(tags),
            status=STATUS_RUNNING,
        )
        session.add(record)
        session.commit()

        cached = previous.get(window.index)
        if cached and cached[0] == window.text_hash and cached[1]:
            # Unchanged since the last completed run: reuse that response rather
            # than paying for the same window twice.
            result = mc.parse_response(cached[1], tags)
            record.status = STATUS_SKIPPED
            reused = True
        else:
            reused = False
            started = datetime.utcnow()
            try:
                request_body = mc.build_request(
                    unit_id=f"kalanjiyam:project/{project.slug}",
                    window_index=window.index,
                    window_total=window.total,
                    pages=window.pages,
                    language_hint=language_hint,
                    tags=tags,
                )
                result = mc.extract_window(request_body)
            except mc.MetadataServiceError as e:
                LOG.warning("window %s failed for %s: %s", window.index, project_id, e)
                record.status = STATUS_FAILED
                record.error_message = str(e)
                record.attempt_count = (record.attempt_count or 0) + 1
                session.commit()
                failed += 1
                continue
            record.attempt_count = (record.attempt_count or 0) + 1
            record.extraction_latency_ms = (
                datetime.utcnow() - started
            ).total_seconds() * 1000

        if not result.ok:
            record.status = STATUS_FAILED
            record.error_message = result.error
            session.commit()
            failed += 1
            continue

        evidence = ar.verify_evidence(result.fields, window.pages)
        stats = ar.window_metrics(result.fields, window.pages, evidence)
        stats["prompt_tokens"] = result.usage.get("prompt_tokens")
        stats["completion_tokens"] = result.usage.get("completion_tokens")
        stats["engine_latency_ms"] = result.engine_latency_ms

        _apply_window_stats(record, result, stats, tags)
        if not reused:
            record.status = STATUS_OK
            record.raw_response = result.raw
        record.completed_at = datetime.utcnow()
        session.commit()

        window_fields.append(result.fields)
        window_stats.append(stats)
        pages_read.update(window.page_slugs)
        completed += 1

        if result.engine and not run.engine:
            run.engine = result.engine
            run.model_name = result.model_name
            run.model_version = result.model_version

    if not completed:
        now = datetime.utcnow()
        run.status = STATUS_FAILED
        run.error_message = "no window produced a usable description"
        run.completed_at = now
        run.total_extraction_latency_ms = max(
            0.0, (now - run.created_at).total_seconds() * 1000.0
        )
        session.commit()
        _set_progress(
            project_id, status=STATUS_FAILED, stage="failed", error=run.error_message
        )
        return {"status": STATUS_FAILED, "windows_failed": failed}

    _set_progress(project_id, stage="reducing")
    fields = ar.reduce_windows(window_fields)
    metrics = ar.run_metrics(window_stats, fields, len(tracks))

    _save_fields(session, run, project_id, fields)
    _apply_run_metrics(run, metrics, completed, failed, len(pages_read))
    run.status = STATUS_OK if not failed else STATUS_PARTIAL
    now = datetime.utcnow()
    run.completed_at = now
    run.total_extraction_latency_ms = max(
        0.0, (now - run.created_at).total_seconds() * 1000.0
    )
    session.commit()

    _write_down(session, project, fields)

    _set_progress(
        project_id,
        status=run.status,
        stage="done",
        done=completed,
        total=completed + failed,
        error=None,
    )
    return {
        "status": run.status,
        "run_id": run.id,
        "windows_completed": completed,
        "windows_failed": failed,
        "fields_filled": metrics["fields_filled"],
        "evidence_verified_rate": metrics["evidence_verified_rate"],
    }


def _write_down(session, project, fields: dict) -> None:
    """Seed the search-facing bibliographic columns from the description.

    This is the whole reason a project needs only one extraction pass. The
    columns `search/indexer.py` reads used to be filled by a separate sampling
    run over the front matter; they are now filled from the full-text pass, which
    read every page and can cite where each value came from.

    Best-effort on purpose: a description that saved correctly must not be marked
    failed because the search index could not be notified.
    """
    try:
        report = ad.write_down(session, project, fields)
        session.commit()
    except Exception:  # noqa: BLE001 - the description itself is already saved
        LOG.exception("could not write down bibliographic fields for %s", project.id)
        session.rollback()
        return

    if not (report.get("applied") or report.get("metadata_applied")):
        return

    try:
        from kalanjiyam.tasks.search_index import enqueue_project

        enqueue_project(project.id)
    except Exception:  # noqa: BLE001
        LOG.warning("could not reindex project %s after write-down", project.id)


def _language_hint(project) -> list[str]:
    """Language codes from an earlier metadata run, if one exists."""
    data = project.extracted_metadata or {}
    return [
        lang.get("code")
        for lang in (data.get("languages") or [])
        if isinstance(lang, dict) and lang.get("code")
    ]


def _apply_window_stats(record, result, stats: dict, tags: list[str]) -> None:
    record.fields_returned = stats["fields_returned"]
    record.fields_declined = len(tags) - stats["fields_returned"]
    record.avg_field_confidence = stats["avg_field_confidence"]
    record.min_field_confidence = stats["min_field_confidence"]
    record.low_conf_field_count = stats["low_conf_field_count"]
    record.evidence_spans = stats["evidence_spans"]
    record.evidence_verified = stats["evidence_verified"]
    record.source_ocr_confidence = stats["source_ocr_confidence"]
    record.pages_without_confidence = stats["pages_without_confidence"]
    record.prompt_tokens = stats.get("prompt_tokens")
    record.completion_tokens = stats.get("completion_tokens")
    record.engine_latency_ms = stats.get("engine_latency_ms")


def _apply_run_metrics(
    run, metrics: dict, completed: int, failed: int, pages_read: int
) -> None:
    run.windows_total = completed + failed
    run.windows_completed = completed
    run.windows_failed = failed
    run.pages_read = pages_read
    for key in (
        "fields_filled",
        "fields_total",
        "avg_field_confidence",
        "min_field_confidence",
        "low_conf_field_count",
        "evidence_spans",
        "evidence_verified",
        "evidence_verified_rate",
        "avg_source_ocr_confidence",
        "pages_without_confidence",
        "total_prompt_tokens",
        "total_completion_tokens",
        "total_engine_latency_ms",
        "total_extraction_latency_ms",
    ):
        setattr(run, key, metrics.get(key))


def _save_fields(session, run, project_id: int, fields: dict) -> None:
    """Write the reduced description and its evidence.

    Write-locked tags are never written here. They are archivist-entered, and the
    extractor has no business producing a custodial history or an access
    condition -- the whole point of excluding them from the request.
    """
    size = 0
    for code, blob in fields.items():
        if code in at.WRITE_LOCKED:
            continue

        field = db.MetadataField(
            run_id=run.id,
            project_id=project_id,
            tag_code=code,
            value=blob.get("value"),
            confidence=blob.get("confidence"),
            source=blob.get("source"),
        )
        session.add(field)
        session.flush()  # need field.id for the evidence rows
        size += len(json.dumps(blob.get("value"), ensure_ascii=False, default=str))

        for value_index, spans in _spans_of(code, blob):
            for span in spans:
                session.add(
                    db.MetadataEvidence(
                        field_id=field.id,
                        value_index=value_index,
                        page_slug=span.get("page_slug") or None,
                        block_id=span.get("block_id") or None,
                        quote=span.get("quote") or None,
                        verified=span.get("verified"),
                    )
                )

    run.metadata_data_size_bytes = size
    session.commit()


def _spans_of(code: str, blob: dict):
    """Yield (value_index, spans) for a field.

    List-valued tags cite per entity -- a citation for a list of forty names is
    no citation at all -- so `value_index` records which one a span belongs to.
    """
    tag = at.BY_CODE.get(code)
    value = blob.get("value")

    if tag is not None and tag.kind in (at.KIND_ENTITIES, at.KIND_RELATIONS):
        for index, item in enumerate(value or []):
            if isinstance(item, dict) and item.get("evidence"):
                yield index, item["evidence"]
        return

    if blob.get("evidence"):
        yield None, blob["evidence"]
