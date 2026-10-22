"""Background tasks for project-wide structure extraction.

Triggered from the Download page when the user ticks "Format document
structure with AI" and clicks Download.  The task iterates over every page
in the project, calls the LLM extraction utility on each page's OCR text,
and saves the results as a versioned ``chat-structure:user:<id>`` revision
track — the same track that ``download_as_csv`` reads.
"""

import logging

from kalanjiyam.tasks import app

logger = logging.getLogger(__name__)


@app.task(bind=True)
def extract_project_structure(self, *, app_env: str, project_slug: str, user_id: int):
    """Extract document structure from all pages in *project_slug*.

    Progress is reported via Celery's ``update_state`` so that the frontend
    can poll ``/extract-structure-status/<task_id>`` and show a live
    progress bar.

    Returns a dict with keys:
        status          – "done"
        pages_processed – number of pages iterated (including skipped)
        entries_total   – total structured entries saved across all pages
        errors          – list of {page, error} dicts for failed pages
    """
    from config import create_config_only_app

    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        from kalanjiyam import database as db
        from kalanjiyam import queries as q
        from kalanjiyam.utils import structure_extract as se
        from kalanjiyam.utils.revisions import EditError, add_revision

        # ── Configuration ────────────────────────────────────────────────
        cfg = flask_app.config
        # Support both legacy key names and the canonical ones
        api_url = (
            cfg.get("LLM_GEMMA_TRANSLATION_API_URL")
            or cfg.get("AI_SERVICE_URL", "")
        )
        api_key = (
            cfg.get("LLM_GEMMA_TRANSLATION_API_KEY")
            or cfg.get("AI_SERVICE_API_KEY", "")
        )
        model = cfg.get("STRUCTURE_EXTRACT_MODEL", "gemma-4")
        timeout = float(cfg.get("AI_SERVICE_TIMEOUT", 300))

        # ── Load project ─────────────────────────────────────────────────
        session = q.get_session()
        project_ = q.project(project_slug)
        if project_ is None:
            return {"status": "error", "error": "Project not found"}

        version_key = f"chat-structure:user:{user_id}"
        pages = project_.pages
        total = len(pages)
        processed = 0
        entries_total = 0
        errors: list[dict] = []

        for page_ in pages:
            # Report progress to Celery so the frontend can poll it
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": processed,
                    "total": total,
                    "page": page_.slug,
                    "entries_total": entries_total,
                },
            )

            # ── Resolve OCR text ─────────────────────────────────────────
            ocr_text = _page_plain_text(page_, session)
            if not ocr_text.strip():
                logger.debug("Skipping page %s — no OCR text", page_.slug)
                processed += 1
                continue

            # ── Call the extraction model ────────────────────────────────
            try:
                entries = se.extract(
                    ocr_text,
                    api_url=api_url,
                    api_key=api_key,
                    model=model,
                    timeout=timeout,
                )
            except se.ExtractionError as exc:
                logger.warning(
                    "Extraction failed for page %s: %s", page_.slug, exc
                )
                errors.append({"page": page_.slug, "error": str(exc)})
                processed += 1
                continue

            # ── Persist as a versioned revision ──────────────────────────
            # Build a minimal plain-text representation for the revision body
            lines = [
                f"{e.get('heading', '')}  {e.get('page_label_raw', '')}".strip()
                for e in entries
            ]
            plain_content = "\n".join(lines)

            document_payload = {
                "type": "chat-structure",
                "format": "structure-entries-v1",
                "entries": entries,
            }

            # Determine the current version number for optimistic-lock safety
            pv = (
                session.query(db.PageVersion)
                .filter_by(page_id=page_.id, version_key=version_key)
                .first()
            )
            current_version = pv.version if pv else 0

            # Resolve a safe status string (never pass an enum value directly)
            if page_.status and hasattr(page_.status, "name"):
                page_status = page_.status.name
            elif page_.status:
                page_status = str(page_.status)
            else:
                page_status = "reviewed-0"

            try:
                add_revision(
                    page_,
                    summary="Structure extraction (AI batch)",
                    content=plain_content,
                    status=page_status,
                    version=current_version,
                    author_id=user_id,
                    document=document_payload,
                    content_format="plain",
                    version_key=version_key,
                )
                entries_total += len(entries)
            except EditError as exc:
                logger.warning(
                    "Failed to save revision for page %s: %s", page_.slug, exc
                )
                errors.append({"page": page_.slug, "error": str(exc)})

            processed += 1

        logger.info(
            "Structure extraction complete for project %s: "
            "%d pages processed, %d entries, %d errors",
            project_slug,
            processed,
            entries_total,
            len(errors),
        )

        return {
            "status": "done",
            "pages_processed": processed,
            "entries_total": entries_total,
            "errors": errors,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _page_plain_text(page_, session) -> str:
    """Return the plain OCR text for *page_*, or an empty string."""
    try:
        from kalanjiyam import database as db

        # Look for the default (non-versioned) page version
        pv = (
            session.query(db.PageVersion)
            .filter_by(page_id=page_.id, version_key="")
            .first()
        )
        if pv is None or not pv.revisions:
            return ""

        latest_rev = pv.revisions[-1]
        doc = getattr(latest_rev, "document", None)
        if doc and isinstance(doc, dict):
            # Rich document format — extract text blocks
            return _doc_to_plain(doc)

        content = getattr(latest_rev, "content", None)
        return content or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read OCR text for page %s: %s", page_.slug, exc)
        return ""


def _doc_to_plain(doc: dict) -> str:
    """Flatten a rich document dict to plain text."""
    try:
        from kalanjiyam.utils.document_utils import document_to_plain_text

        return document_to_plain_text(doc)
    except Exception:  # noqa: BLE001
        pass

    # Fallback: walk known block types
    parts: list[str] = []
    for block in doc.get("blocks", []) or doc.get("content", []) or []:
        if isinstance(block, dict):
            text = block.get("text") or block.get("content") or ""
            if text:
                parts.append(str(text))
    return "\n".join(parts)
