"""Background extraction of book metadata.

**The extraction here is retired.** `extract_project_metadata` has no caller: the
description tab reads every page and cites its evidence, and seeds these same
columns through `utils.archival_description.write_down`. This module read the
front matter and a sample of the body, which cannot answer the recall questions
an archival description is mostly made of, and running both would give two
answers to "what is this called".

What is still live and imported elsewhere:

* `apply_bibliographic` -- the fill-only-empty-columns rule, reused by the
  write-down so there is one place that decides whether a generated value may
  touch a column a human has typed in.
* `accept_staged` -- so a run staged before the changeover is not stranded.
* `SCHEMA_VERSION` -- still stamped on `extracted_metadata`.

The sampling code below is kept rather than deleted because the samplers in
`utils.project_metadata` are shared with the windowing that replaced it.

Orchestrates three persona calls against the chat service, with the
deterministic work (script profiling, page counts, OCR engine list) done
locally in `kalanjiyam.utils.project_metadata` so the model is never asked to
compute something we can just look up.

Two invariants shape the design:

* **A regenerate must not destroy curation.** Once a moderator has edited the
  metadata, a new run lands in a ``staged`` slot and the tab offers to load
  it, rather than overwriting in place.
* **Partial success beats no success.** Each persona call is isolated: if
  content analysis fails, the front-matter result is still saved and the run
  is marked ``partial``.
"""

from __future__ import annotations

import contextlib
import hashlib
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
from kalanjiyam.utils import project_metadata as pm
from kalanjiyam.utils.llm_client import (
    PERSONA_CONTENT_ANALYSIS,
    PERSONA_FRONT_MATTER,
    PERSONA_LANGUAGE_ID,
    LlmError,
    chat_with_backoff,
)

LOG = logging.getLogger(__name__)

#: Shape version of `Project.extracted_metadata`.
SCHEMA_VERSION = 1

#: Bump when persona prompts change server-side, so cached runs are invalidated.
PROMPT_VERSION = "1"

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"
STATUS_RUNNING = "running"

_LOCK_TTL = 60 * 30
_PROGRESS_TTL = 60 * 60

#: Windows used by deep (map-reduce) analysis. CONSERVATIVE: 20 windows is
#: already ~21 calls, which is minutes of GPU time on a large book.
DEEP_MAX_WINDOWS = 20

_redis = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def lock_key(project_id: int) -> str:
    return f"metadata_extract:lock:{project_id}"


def progress_key(project_id: int) -> str:
    return f"metadata_extract:progress:{project_id}"


def get_progress(project_id: int) -> dict | None:
    """Read the current run's progress, if any.

    Best-effort: the Metadata tab reads this on every page load, and an
    unreachable Redis must not take the page down with it.
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
        LOG.warning("could not record extraction progress for project %s", project_id)


def _get_app_context():
    """Run under a Flask app context whether called from Celery, CLI, or web."""
    if has_app_context():
        return contextlib.nullcontext()
    app_env = os.environ.get(
        "KALANJIYAM_DEPLOYMENT_ENV", os.environ.get("FLASK_ENV", "development")
    )
    return create_config_only_app(app_env).app_context()


# Bibliographic mapping
# ---------------------

#: LLM field -> Project column. `catalog_ids`, `confidence`, and `notes` have
#: no column and live in `extracted_metadata` only.
BIBLIOGRAPHIC_FIELDS = {
    "title": "print_title",
    "subtitle": "subtitle",
    "author": "author",
    "editor_translator": "editor",
    "publisher": "publisher",
    "place_of_publication": "place_of_publication",
    "year": "publication_year",
    "edition": "edition",
    "series": "series",
    "subject": "subject",
}


def _clean(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def apply_bibliographic(session, project, data: dict) -> dict:
    """Fill *empty* bibliographic columns from an extraction.

    Never overwrites a value a human has already set -- curated data outranks
    a fresh generation. Returns a per-field report for the UI.
    """
    applied, skipped = {}, {}
    for source, column in BIBLIOGRAPHIC_FIELDS.items():
        value = _clean(data.get(source))
        if not value:
            continue
        if _clean(getattr(project, column, "")):
            skipped[column] = value
            continue
        setattr(project, column, value)
        applied[column] = value

    genre = _clean(data.get("genre"))
    if genre and project.genre_id is None:
        match = _match_genre(genre)
        if match is not None:
            project.genre_id = match.id
            applied["genre"] = match.name
        else:
            # Unmatched genres are kept as text rather than silently creating
            # new Genre rows, which would pollute a shared lookup table.
            skipped["genre"] = genre

    return {"applied": applied, "skipped": skipped}


def _match_genre(name: str):
    """Case-insensitive match of free text against the Genre table."""
    target = name.strip().lower()
    for genre in q.genres():
        if genre.name.strip().lower() == target:
            return genre
    return None


# Extraction
# ----------


def _sample_hash(*texts: str) -> str:
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode())
    for text in texts:
        digest.update(b"\x00")
        digest.update(text.encode("utf-8", "replace"))
    return digest.hexdigest()


def _call(persona: str, text: str, errors: list[str]):
    """Run one persona, converting failures into a recorded error."""
    if not text.strip():
        errors.append(f"{persona}: no text to analyze")
        return None
    try:
        result = chat_with_backoff(persona, text)
    except LlmError as e:
        LOG.warning("persona %s failed: %s", persona, e)
        errors.append(f"{persona}: {e}")
        return None
    if not result.ok:
        errors.append(f"{persona}: {result.error}")
    return result


def _usage_of(*results) -> dict:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    for result in results:
        if result is None:
            continue
        total["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = result.usage.get(key)
            if isinstance(value, int):
                total[key] += value
    return total


def _deep_content_analysis(session, project_id, tracks, scripts, errors: list[str]):
    """Map-reduce content analysis for very large books.

    Flat sampling reads a fraction of a percent of a 2,000-page book. This
    trades many cheap calls for coverage: summarize windows, then summarize
    the summaries.
    """
    rows = pm._usable(list(tracks.values()))
    if not rows:
        return None, []

    window_count = min(DEEP_MAX_WINDOWS, max(1, len(rows) // 25))
    size = max(1, len(rows) // window_count)
    windows = [rows[i : i + size] for i in range(0, len(rows), size)][:window_count]

    budget = pm._budget_chars("content_analysis", scripts)
    partials, results = [], []
    for index, window in enumerate(windows, start=1):
        sample = pm._assemble(session, window, budget)
        if not sample.text.strip():
            continue
        _set_progress(project_id, stage=f"analyzing section {index} of {len(windows)}")
        result = _call(PERSONA_CONTENT_ANALYSIS, sample.text, errors)
        results.append(result)
        if result is not None and result.ok:
            partials.append(result.data)

    if not partials:
        return None, results

    # Reduce: hand the window summaries back through the same persona.
    reduction = "\n\n".join(
        f"--- section {i} ---\n{p.get('summary') or ''}"
        for i, p in enumerate(partials, start=1)
        if p.get("summary")
    )
    reduced = _call(PERSONA_CONTENT_ANALYSIS, reduction, errors) if reduction else None
    results.append(reduced)

    merged = dict(partials[0])
    if reduced is not None and reduced.ok and reduced.data.get("summary"):
        merged["summary"] = reduced.data["summary"]

    # Union the list-valued fields across windows.
    keywords, toc = [], []
    entities: dict[str, list] = {}
    for part in partials:
        for word in part.get("keywords") or []:
            if word not in keywords:
                keywords.append(word)
        for item in part.get("toc") or []:
            if item not in toc:
                toc.append(item)
        for key, values in (part.get("entities") or {}).items():
            bucket = entities.setdefault(key, [])
            for value in values or []:
                if value not in bucket:
                    bucket.append(value)
    merged["keywords"] = keywords
    merged["toc"] = toc
    merged["entities"] = entities
    return merged, results


@app.task(bind=True)
def extract_project_metadata(self, project_id: int, deep: bool = False) -> dict:
    """Extract metadata for one project. Returns a summary of the run."""
    with _get_app_context():
        lock = lock_key(project_id)
        locked = False
        try:
            locked = bool(
                _redis.set(lock, self.request.id or "1", nx=True, ex=_LOCK_TTL)
            )
            if not locked:
                LOG.info("extraction already running for project %s", project_id)
                return {"status": "locked"}
        except redis.RedisError:
            # Without Redis we cannot guarantee a single in-flight run. A
            # duplicate run wastes GPU time but is not destructive, so prefer
            # running to refusing.
            LOG.warning("could not acquire extraction lock for %s", project_id)

        try:
            return _run(project_id, deep=deep)
        except Exception as e:  # noqa: BLE001 - surfaced to the UI
            LOG.exception("metadata extraction failed for project %s", project_id)
            _set_progress(project_id, status=STATUS_ERROR, stage="failed", error=str(e))
            return {"status": STATUS_ERROR, "error": str(e)}
        finally:
            if locked:
                try:
                    _redis.delete(lock)
                except redis.RedisError:
                    LOG.warning("could not release extraction lock for %s", project_id)


def _run(project_id: int, *, deep: bool) -> dict:
    session = q.get_session()
    project = session.query(db.Project).filter(db.Project.id == project_id).first()
    if project is None:
        raise ValueError(f"project {project_id} not found")

    total_stages = 5 if not deep else 6
    _set_progress(
        project_id,
        status=STATUS_RUNNING,
        stage="resolving pages",
        done=0,
        total=total_stages,
        error=None,
    )

    tracks = pm.resolve_extraction_tracks(session, project_id)
    if not tracks:
        _set_progress(
            project_id,
            status=STATUS_ERROR,
            stage="no text",
            error="This project has no OCR'd or edited pages yet.",
        )
        return {"status": STATUS_ERROR, "error": "no text"}

    _set_progress(project_id, stage="profiling scripts", done=1)
    derived = pm.derived_stats(session, project_id, tracks)
    scripts = derived["scripts"]

    _set_progress(project_id, stage="sampling pages", done=2)
    front = pm.sample_front_matter(session, tracks, scripts)
    body = pm.sample_body(session, tracks, scripts)
    languages = pm.sample_for_language_id(session, tracks, scripts)

    existing = project.extracted_metadata or {}
    sample_hash = _sample_hash(front.text, body.text, languages.text)
    previous = (existing.get("provenance") or {}).get("sample_hash")
    if (
        previous == sample_hash
        and (existing.get("provenance") or {}).get("status") == STATUS_OK
        and not deep
    ):
        _set_progress(
            project_id,
            status=STATUS_OK,
            stage="unchanged",
            done=total_stages,
            total=total_stages,
        )
        return {"status": STATUS_OK, "unchanged": True}

    errors: list[str] = []

    _set_progress(project_id, stage="reading front matter", done=3)
    front_result = _call(PERSONA_FRONT_MATTER, front.text, errors)

    _set_progress(project_id, stage="analyzing content", done=4)
    if deep:
        content_data, content_results = _deep_content_analysis(
            session, project_id, tracks, scripts, errors
        )
    else:
        content_result = _call(PERSONA_CONTENT_ANALYSIS, body.text, errors)
        content_data = (
            content_result.data if content_result and content_result.ok else None
        )
        content_results = [content_result]

    _set_progress(project_id, stage="identifying languages", done=total_stages - 1)
    language_input = _language_prompt(languages.text, derived)
    language_result = _call(PERSONA_LANGUAGE_ID, language_input, errors)

    front_data = front_result.data if front_result and front_result.ok else None
    language_data = (
        language_result.data if language_result and language_result.ok else None
    )

    status = (
        STATUS_OK
        if not errors
        else (
            STATUS_PARTIAL
            if (front_data or content_data or language_data)
            else STATUS_ERROR
        )
    )

    provenance = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": (front_result.model if front_result else "") or "",
        "status": status,
        "errors": errors,
        "deep": deep,
        "sample_hash": sample_hash,
        "pages_sampled": sorted(
            {*front.page_slugs, *body.page_slugs, *languages.page_slugs}
        ),
        "pages_sampled_count": len(
            {*front.page_slugs, *body.page_slugs, *languages.page_slugs}
        ),
        "pages_total": derived["pages_total"],
        "ocr_fallback_pages": front.ocr_fallback_count + body.ocr_fallback_count,
        "sample_truncated": front.truncated or body.truncated,
        "usage": _usage_of(front_result, language_result, *(content_results or [])),
        "run_at": datetime.utcnow().isoformat(),
    }

    payload = {
        "schema_version": SCHEMA_VERSION,
        "languages": (language_data or {}).get("languages") or [],
        "content": content_data or {},
        "bibliographic": front_data or {},
        "derived": derived,
        "provenance": provenance,
    }

    _save(session, project, payload, existing)
    _set_progress(
        project_id,
        status=status,
        stage="done",
        done=total_stages,
        total=total_stages,
        error="; ".join(errors) if errors else None,
    )
    return {"status": status, "errors": errors}


def _language_prompt(sample_text: str, derived: dict) -> str:
    """Give the language persona the deterministic script mix as context."""
    if not sample_text.strip():
        return ""
    scripts = ", ".join(
        f"{derived['script_labels'].get(code, code)} {share:.0%}"
        for code, share in (derived.get("scripts") or {}).items()
    )
    header = f"Detected scripts: {scripts}\n\n" if scripts else ""
    return f"{header}{sample_text}"


def _save(session, project, payload: dict, existing: dict) -> None:
    """Persist a run, staging it when curated values already exist."""
    curated = bool((existing.get("content") or {}) or (existing.get("languages") or []))

    if curated:
        # Something is already there; park the new run for review rather than
        # overwriting edits a moderator has made.
        merged = dict(existing)
        merged["derived"] = payload["derived"]
        merged["staged"] = {
            "languages": payload["languages"],
            "content": payload["content"],
            "bibliographic": payload["bibliographic"],
            "provenance": payload["provenance"],
        }
        project.extracted_metadata = merged
    else:
        project.extracted_metadata = payload
        if payload["bibliographic"]:
            payload["provenance"]["bibliographic_applied"] = apply_bibliographic(
                session, project, payload["bibliographic"]
            )

    # SQLAlchemy does not track in-place mutation of a JSON column.
    flag_modified(project, "extracted_metadata")
    session.commit()

    try:
        from kalanjiyam.tasks.search_index import enqueue_project

        enqueue_project(project.id)
    except Exception:  # noqa: BLE001 - indexing is best-effort
        LOG.warning("could not enqueue reindex for project %s", project.id)


def accept_staged(session, project) -> bool:
    """Promote a staged run to the canonical fields. Returns True if applied."""
    data = project.extracted_metadata or {}
    staged = data.get("staged")
    if not staged:
        return False

    merged = dict(data)
    merged["languages"] = staged.get("languages") or []
    merged["content"] = staged.get("content") or {}
    merged["bibliographic"] = staged.get("bibliographic") or {}
    merged["provenance"] = staged.get("provenance") or {}
    merged.pop("staged", None)
    project.extracted_metadata = merged

    if merged["bibliographic"]:
        apply_bibliographic(session, project, merged["bibliographic"])

    flag_modified(project, "extracted_metadata")
    session.commit()
    return True
