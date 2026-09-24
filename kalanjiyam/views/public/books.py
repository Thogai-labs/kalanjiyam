"""Public views for viewing books (project-based)."""

import json
import os

import redis
from flask import Blueprint, abort, current_app, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import orm

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.utils.org_access import is_multi_tenant_enabled

bp = Blueprint("books", __name__)


@bp.before_request
def check_books_enabled():
    """Abort with 404 if public books / digital library catalog is disabled."""
    if not current_app.config.get("ENABLE_BOOKS", True):
        abort(404)


def _get_redis_client():
    try:
        client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0")
        )
        client.ping()
        return client
    except Exception:
        return None


def get_public_projects():
    """Get all projects marked as publicly viewable."""
    session = q.get_session()

    projects = (
        session.query(db.Project)
        .filter(
            db.Project.is_publicly_viewable.is_(True),
            db.Project.pages.any(),
        )
        .options(
            orm.selectinload(db.Project.pages),
            orm.selectinload(db.Project.groups),
        )
        .all()
    )
    if (
        current_app.config.get("ENFORCE_GROUP_ACCESS_FOR_PROJECTS")
        or is_multi_tenant_enabled()
    ):
        projects = [p for p in projects if q.user_can_view_project(current_user, p)]
    return projects


def get_project_stats(project, r_client=None):
    """Get statistics for a project with Redis caching (total pages, OCR'd pages, translated pages, language pairs)."""
    updated_ts = (
        int(project.updated_at.timestamp())
        if getattr(project, "updated_at", None)
        else 0
    )
    cache_key = f"books:stats:{project.id}:{updated_ts}"

    if r_client is not None:
        try:
            cached_raw = r_client.get(cache_key)
            if cached_raw:
                return json.loads(cached_raw.decode("utf-8"))
        except Exception:
            pass

    session = q.get_session()
    total_pages = (
        len(project.pages) if hasattr(project, "pages") and project.pages else 0
    )

    if total_pages == 0:
        stats = {
            "total_pages": 0,
            "ocr_pages": 0,
            "translated_pages": 0,
            "ocr_percentage": 0,
            "translation_percentage": 0,
            "lang_pairs": {},
        }
        if r_client is not None:
            try:
                r_client.setex(cache_key, 3600, json.dumps(stats))
            except Exception:
                pass
        return stats

    # Count pages with revisions (OCR'd)
    ocr_pages = (
        session.query(db.Page.id)
        .filter(db.Page.project_id == project.id)
        .join(db.Revision)
        .distinct()
        .count()
    )

    # Get language pair breakdown and translated pages in one single query
    translations = (
        session.query(
            db.Translation.source_language,
            db.Translation.target_language,
            db.Translation.page_id,
        )
        .join(db.Page, db.Translation.page_id == db.Page.id)
        .filter(db.Page.project_id == project.id)
        .distinct()
        .all()
    )

    translated_page_ids = {t[2] for t in translations}
    translated_pages = len(translated_page_ids)

    pair_counts = {}
    for src, tgt, _page_id in translations:
        pair_key = f"{src or 'auto'} → {tgt}"
        pair_counts[pair_key] = pair_counts.get(pair_key, 0) + 1

    lang_pairs = {}
    for pair_key, count in pair_counts.items():
        lang_pairs[pair_key] = {
            "count": count,
            "percentage": (count / total_pages * 100),
        }

    stats = {
        "total_pages": total_pages,
        "ocr_pages": ocr_pages,
        "translated_pages": translated_pages,
        "ocr_percentage": (ocr_pages / total_pages * 100) if total_pages > 0 else 0,
        "translation_percentage": (
            (translated_pages / total_pages * 100) if total_pages > 0 else 0
        ),
        "lang_pairs": lang_pairs,
    }

    if r_client is not None:
        try:
            r_client.setex(cache_key, 3600, json.dumps(stats))
        except Exception:
            pass

    return stats


@bp.route("/")
def index():
    """Show all available books."""
    projects = get_public_projects()
    r_client = _get_redis_client()

    # Get stats for each project
    projects_with_stats = []
    for project in projects:
        stats = get_project_stats(project, r_client=r_client)
        projects_with_stats.append(
            {
                "project": project,
                "stats": stats,
            }
        )

    # Sort by title
    projects_with_stats.sort(key=lambda x: x["project"].display_title)

    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 9, type=int)
    if page < 1:
        page = 1
    if per_page not in (9, 12, 18, 27, 36):
        per_page = 9

    serialized_projects = [
        {
            "id": p["project"].id,
            "slug": p["project"].slug,
            "title": p["project"].display_title,
            "author": p["project"].author or "",
            "description": p["project"].description or "",
            "url": url_for("public.books.book", project_slug=p["project"].slug),
            "stats": {
                "total_pages": p["stats"]["total_pages"],
                "ocr_pages": p["stats"]["ocr_pages"],
                "ocr_percentage": round(p["stats"]["ocr_percentage"], 1),
                "translated_pages": p["stats"]["translated_pages"],
                "translation_percentage": round(
                    p["stats"]["translation_percentage"], 1
                ),
                "lang_pairs": [
                    {
                        "pair": pair,
                        "count": info["count"],
                        "percentage": round(info["percentage"], 1),
                    }
                    for pair, info in p["stats"].get("lang_pairs", {}).items()
                ],
            },
        }
        for p in projects_with_stats
    ]

    return render_template(
        "public/books/index.html",
        projects=projects_with_stats,
        serialized_projects=serialized_projects,
        query=query,
        page=page,
        per_page=per_page,
    )


@bp.route("/<project_slug>/")
def book(project_slug):
    """Show book details and page list."""
    session = q.get_session()
    project = (
        session.query(db.Project)
        .filter_by(slug=project_slug)
        .options(
            orm.selectinload(db.Project.pages),
            orm.selectinload(db.Project.groups),
        )
        .first()
    )
    if project is None:
        abort(404)
    if (
        current_app.config.get("ENFORCE_GROUP_ACCESS_FOR_PROJECTS")
        or is_multi_tenant_enabled()
    ) and not q.user_can_view_project(current_user, project):
        abort(403)

    if not project.is_publicly_viewable:
        abort(404)

    r_client = _get_redis_client()
    stats = get_project_stats(project, r_client=r_client)

    pages = project.pages
    page_ids = [p.id for p in pages]

    # 1. Batch fetch all translations for the project's pages in ONE single query
    translations_by_page = {}
    if page_ids:
        all_translations = (
            session.query(
                db.Translation.page_id,
                db.Translation.source_language,
                db.Translation.target_language,
            )
            .filter(db.Translation.page_id.in_(page_ids))
            .all()
        )
        for page_id, src, tgt in all_translations:
            translations_by_page.setdefault(page_id, []).append(
                f"{src or 'auto'}→{tgt}"
            )

    # 2. Batch fetch page IDs with revisions in ONE single query
    revision_page_ids = set()
    if page_ids:
        rev_rows = (
            session.query(db.Revision.page_id)
            .filter(db.Revision.page_id.in_(page_ids))
            .distinct()
            .all()
        )
        revision_page_ids = {r[0] for r in rev_rows}

    # 3. Assemble pages with their latest revision and translation info
    pages_with_info = []
    for page_item in pages:
        has_rev = page_item.id in revision_page_ids
        t_langs = translations_by_page.get(page_item.id, [])
        pages_with_info.append(
            {
                "page": page_item,
                "latest_revision": True if has_rev else None,
                "has_translation": len(t_langs) > 0,
                "translation_langs": t_langs,
            }
        )

    return render_template(
        "public/books/book.html",
        project=project,
        stats=stats,
        pages=pages_with_info,
    )


@bp.route("/<project_slug>/<page_slug>/")
def page(project_slug, page_slug):
    """Show a specific page with OCR text and optional translation."""
    session = q.get_session()
    project = (
        session.query(db.Project)
        .filter_by(slug=project_slug)
        .options(
            orm.selectinload(db.Project.pages),
            orm.selectinload(db.Project.groups),
        )
        .first()
    )
    if project is None:
        abort(404)
    if not project.is_publicly_viewable:
        abort(404)
    if (
        current_app.config.get("ENFORCE_GROUP_ACCESS_FOR_PROJECTS")
        or is_multi_tenant_enabled()
    ) and not q.user_can_view_project(current_user, project):
        abort(403)

    page_obj = q.page(project.id, page_slug)
    if page_obj is None:
        abort(404)

    latest_revision = page_obj.revisions[-1] if page_obj.revisions else None

    # Get available translations (only if there is a revision)
    translations = []
    if latest_revision:
        translations = (
            session.query(db.Translation)
            .filter(db.Translation.page_id == page_obj.id)
            .filter(db.Translation.revision_id == latest_revision.id)
            .all()
        )

    # Get navigation context
    pages = project.pages
    current_index = None
    for i, p in enumerate(pages):
        if p.slug == page_slug:
            current_index = i
            break

    prev_page = (
        pages[current_index - 1]
        if current_index is not None and current_index > 0
        else None
    )
    next_page = (
        pages[current_index + 1]
        if current_index is not None and current_index < len(pages) - 1
        else None
    )

    # Get requested translation language
    translation_lang = request.args.get("translation", "en")
    selected_translation = None

    for translation in translations:
        if translation.target_language == translation_lang:
            selected_translation = translation
            break

    # If requested language not found, use first available translation
    if not selected_translation and translations:
        selected_translation = translations[0]

    return render_template(
        "public/books/page.html",
        project=project,
        page=page_obj,
        revision=latest_revision,
        translations=translations,
        selected_translation=selected_translation,
        prev_page=prev_page,
        next_page=next_page,
        current_index=current_index,
        total_pages=len(pages),
    )
