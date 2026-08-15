"""Background run for the archival-taxonomy smoke test.

SMOKE TEST ONLY. Companion to `kalanjiyam.utils.archival_taxonomy` and the
"Metadata (test)" tab. Nothing here touches the schema: the run log and the
extracted document both live in the existing `Project.extracted_metadata` JSON
column, under two keys this experiment owns.

Deliberately a separate module from `tasks.metadata` so the whole experiment can
be deleted in one go without picking it back out of production code. It mirrors
that module's Redis lock / progress conventions on purpose -- the tab polls for
progress exactly the way the real Metadata tab does.

Unlike the real pipeline, this makes a *single* persona call and carries the
whole instruction in the user message, because the taxonomy has no persona
registered server-side.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from datetime import datetime

import redis
from flask import has_app_context
from sqlalchemy.orm.attributes import flag_modified

from config import create_config_only_app
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.tasks import app
from kalanjiyam.utils import archival_taxonomy as at
from kalanjiyam.utils import llm_client
from kalanjiyam.utils import project_metadata as pm

LOG = logging.getLogger(__name__)

#: Keys this experiment owns inside `extracted_metadata`.
ARCHIVAL_KEY = "archival"
ARCHIVAL_RUN_KEY = "archival_run"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_RUNNING = "running"

_LOCK_TTL = 60 * 15
_PROGRESS_TTL = 60 * 60

#: Ceiling on page text sent in one run. Generous because this runs on a worker
#: rather than in a web request -- there is no gunicorn timeout to duck under.
#: Still bounded: the backing model's context is 32k tokens, and Indic scripts
#: tokenize far worse than Latin.
MAX_CHARS = 60_000
DEFAULT_CHARS = 12_000

_redis = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def lock_key(project_id: int) -> str:
    return f"archival_test:lock:{project_id}"


def progress_key(project_id: int) -> str:
    return f"archival_test:progress:{project_id}"


def get_progress(project_id: int) -> dict | None:
    """Read the current run's progress. Best-effort; never raises.

    The tab reads this on every page load, so an unreachable Redis must not
    take the page down with it.
    """
    try:
        blob = _redis.get(progress_key(project_id))
    except redis.RedisError:
        LOG.warning("could not read archival test progress for %s", project_id)
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
        LOG.warning("could not record archival test progress for %s", project_id)


def _clear_progress(project_id: int) -> None:
    try:
        _redis.delete(progress_key(project_id))
    except redis.RedisError:
        pass


def _get_app_context():
    """Run under a Flask app context whether called from Celery, CLI, or web.

    `llm_client.chat` reads OCR_SERVICE_URL off `current_app.config`, so the
    worker needs one of these.
    """
    if has_app_context():
        return contextlib.nullcontext()
    app_env = os.environ.get(
        "KALANJIYAM_DEPLOYMENT_ENV", os.environ.get("FLASK_ENV", "development")
    )
    return create_config_only_app(app_env).app_context()


def clamp_chars(value) -> int:
    """Coerce a user-supplied character cap into the allowed range."""
    try:
        return max(500, min(int(value or DEFAULT_CHARS), MAX_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_CHARS


@app.task(bind=True)
def run_archival_test(
    self,
    project_id: int,
    persona: str = "",
    instructions: str = "",
    max_chars: int = DEFAULT_CHARS,
) -> dict:
    """SMOKE TEST: sample a project's pages and run one persona over them."""
    with _get_app_context():
        lock = lock_key(project_id)
        locked = False
        try:
            locked = bool(
                _redis.set(lock, self.request.id or "1", nx=True, ex=_LOCK_TTL)
            )
            if not locked:
                LOG.info("archival test already running for project %s", project_id)
                return {"status": "locked"}
        except redis.RedisError:
            # Without Redis we cannot guarantee a single in-flight run. A
            # duplicate run wastes GPU time but is not destructive.
            LOG.warning("could not acquire archival test lock for %s", project_id)

        try:
            return _run(
                project_id,
                persona=persona or llm_client.PERSONA_FRONT_MATTER,
                instructions=instructions or at.build_prompt(),
                max_chars=clamp_chars(max_chars),
            )
        except Exception as e:  # noqa: BLE001 - a smoke test must show failures
            LOG.exception("archival test failed for project %s", project_id)
            _set_progress(
                project_id, status=STATUS_ERROR, stage="failed", error=str(e)
            )
            _save_run(
                project_id,
                {
                    "at": datetime.utcnow().isoformat(),
                    "persona": persona,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            return {"status": STATUS_ERROR, "error": str(e)}
        finally:
            if locked:
                try:
                    _redis.delete(lock)
                except redis.RedisError:
                    LOG.warning("could not release archival lock for %s", project_id)


def _run(project_id: int, *, persona: str, instructions: str, max_chars: int) -> dict:
    session = q.get_session()
    project = session.query(db.Project).filter(db.Project.id == project_id).first()
    if project is None:
        raise ValueError(f"project {project_id} not found")

    run: dict = {
        "at": datetime.utcnow().isoformat(),
        "persona": persona,
        "max_chars": max_chars,
        "ok": False,
    }

    _set_progress(
        project_id, status=STATUS_RUNNING, stage="resolving pages",
        done=0, total=4, error=None,
    )
    tracks = pm.resolve_extraction_tracks(session, project_id)
    if not tracks:
        raise RuntimeError("this project has no page text to sample")

    _set_progress(project_id, stage="profiling scripts", done=1)
    profile = pm.script_profile(session, list(tracks.values()))

    _set_progress(project_id, stage="sampling pages", done=2)
    sample = pm.sample_front_matter(session, tracks, profile.get("scripts") or {})
    page_text = (sample.text or "")[:max_chars]
    if not page_text.strip():
        raise RuntimeError("the sampled pages are empty")

    prompt = f"{instructions}\n\n{page_text}"
    run.update(
        pages_sampled=sample.page_slugs,
        ocr_fallback_pages=sample.ocr_fallback_count,
        chars_sent=len(prompt),
        scripts=profile.get("scripts") or {},
        prompt_preview=prompt[:2000],
    )

    _set_progress(project_id, stage=f"asking {persona}", done=3)
    LOG.info(
        "archival test: project=%s persona=%s pages=%s chars=%s",
        project_id, persona, len(sample.page_slugs), len(prompt),
    )

    started = datetime.utcnow()
    try:
        result = llm_client.chat(persona, prompt)
    except llm_client.LlmError as e:
        run["elapsed_s"] = (datetime.utcnow() - started).total_seconds()
        run["error"] = str(e)
        run["status"] = e.status
        LOG.warning(
            "archival test service error: project=%s persona=%s %s",
            project_id, persona, e,
        )
        _save_run(project_id, run)
        _set_progress(project_id, status=STATUS_ERROR, stage="failed", error=str(e))
        return {"status": STATUS_ERROR, "error": str(e)}

    run["elapsed_s"] = round((datetime.utcnow() - started).total_seconds(), 2)
    run.update(
        ok=result.ok,
        model=result.model,
        usage=result.usage,
        cached=result.cached,
        truncated=result.truncated,
        raw=result.raw,
        error=result.error,
    )

    document = None
    if result.ok:
        # Keys the model invented are quarantined rather than written into the
        # document, so the tab shows the taxonomy and not the model's guesses.
        # Write-locked tags are dropped here too: the prompt never asks for them,
        # so anything arriving under those keys was fabricated.
        document = {
            k: v
            for k, v in result.data.items()
            if k in at.BY_CODE and k not in at.WRITE_LOCKED
        }
        run["parsed_keys"] = sorted(document)
        run["unknown_keys"] = sorted(set(result.data) - set(at.BY_CODE))
        run["locked_keys_returned"] = sorted(set(result.data) & at.WRITE_LOCKED)
        run["taxonomy_version"] = at.TAXONOMY_VERSION
        LOG.info(
            "archival test ok: project=%s tags=%s/%s model=%s",
            project_id, len(document), len(at.TAGS), result.model,
        )
    else:
        LOG.warning(
            "archival test unusable output: project=%s persona=%s %s",
            project_id, persona, result.error,
        )

    _save_run(project_id, run, document=document)
    _set_progress(
        project_id,
        status=STATUS_OK if result.ok else STATUS_ERROR,
        stage="done",
        done=4,
        error=None if result.ok else result.error,
    )
    return {
        "status": STATUS_OK if result.ok else STATUS_ERROR,
        "tags": len(document or {}),
    }


def _save_run(project_id: int, run: dict, document: dict | None = None) -> None:
    """Persist the run log, and the document when there is one.

    Merges into a copy of the column so the unrelated sibling keys the real
    pipeline owns (`languages`, `content`, `derived`, `provenance`,
    `source_file`) are never dropped.
    """
    session = q.get_session()
    project = session.query(db.Project).filter(db.Project.id == project_id).first()
    if project is None:
        return

    merged = dict(project.extracted_metadata or {})
    merged[ARCHIVAL_RUN_KEY] = run
    if document is not None:
        merged[ARCHIVAL_KEY] = document
    project.extracted_metadata = merged
    # SQLAlchemy does not track in-place mutation of a JSON column.
    flag_modified(project, "extracted_metadata")
    session.commit()
