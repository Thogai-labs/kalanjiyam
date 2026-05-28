"""Public views for viewing books (project-based)."""

from flask import Blueprint, abort, current_app, render_template, request
from flask_login import current_user
from sqlalchemy import and_, or_

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.utils.org_access import is_multi_tenant_enabled

bp = Blueprint("books", __name__)


def get_public_projects():
    """Get all projects that have OCR'd content available for public viewing."""
    session = q.get_session()
    
    # Get projects that have at least one page with a revision (OCR'd content)
    projects_with_content = (
        session.query(db.Project)
        .join(db.Page)
        .join(db.Revision)
        .distinct()
        .all()
    )
    if current_app.config.get("ENFORCE_GROUP_ACCESS_FOR_PROJECTS") or is_multi_tenant_enabled():
        projects_with_content = [
            p for p in projects_with_content if q.user_can_view_project(current_user, p)
        ]
    return projects_with_content


def get_project_stats(project):
    """Get statistics for a project (total pages, OCR'd pages, translated pages)."""
    session = q.get_session()
    
    total_pages = len(project.pages)
    
    # Count pages with revisions (OCR'd)
    ocr_pages = (
        session.query(db.Page)
        .filter(db.Page.project_id == project.id)
        .join(db.Revision)
        .distinct()
        .count()
    )
    
    # Count pages with translations
    translated_pages = (
        session.query(db.Page)
        .filter(db.Page.project_id == project.id)
        .join(db.Translation)
        .distinct()
        .count()
    )
    
    return {
        'total_pages': total_pages,
        'ocr_pages': ocr_pages,
        'translated_pages': translated_pages,
        'ocr_percentage': (ocr_pages / total_pages * 100) if total_pages > 0 else 0,
        'translation_percentage': (translated_pages / total_pages * 100) if total_pages > 0 else 0
    }


@bp.route("/")
def index():
    """Show all available books."""
    projects = get_public_projects()
    
    # Get stats for each project
    projects_with_stats = []
    for project in projects:
        stats = get_project_stats(project)
        projects_with_stats.append({
            'project': project,
            'stats': stats
        })
    
    # Sort by title
    projects_with_stats.sort(key=lambda x: x['project'].display_title)
    
    return render_template(
        "public/books/index.html",
        projects=projects_with_stats
    )


@bp.route("/<project_slug>/")
def book(project_slug):
    """Show book details and page list."""
    project = q.project(project_slug)
    if project is None:
        abort(404)
    if (current_app.config.get("ENFORCE_GROUP_ACCESS_FOR_PROJECTS") or is_multi_tenant_enabled()) and not q.user_can_view_project(
        current_user, project
    ):
        abort(403)
    
    # Check if project has any OCR'd content
    session = q.get_session()
    has_content = (
        session.query(db.Page)
        .filter(db.Page.project_id == project.id)
        .join(db.Revision)
        .first()
    ) is not None
    
    if not has_content:
        abort(404)
    
    stats = get_project_stats(project)
    
    # Get pages with their latest revision and translation info
    pages_with_info = []
    for page in project.pages:
        latest_revision = page.revisions[-1] if page.revisions else None
        
        # Check if page has translations
        has_translation = (
            session.query(db.Translation)
            .filter(db.Translation.page_id == page.id)
            .first()
        ) is not None
        
        pages_with_info.append({
            'page': page,
            'latest_revision': latest_revision,
            'has_translation': has_translation
        })
    
    return render_template(
        "public/books/book.html",
        project=project,
        stats=stats,
        pages=pages_with_info
    )


@bp.route("/<project_slug>/<page_slug>/")
def page(project_slug, page_slug):
    """Show a specific page with OCR text and optional translation."""
    project = q.project(project_slug)
    if project is None:
        abort(404)
    if (current_app.config.get("ENFORCE_GROUP_ACCESS_FOR_PROJECTS") or is_multi_tenant_enabled()) and not q.user_can_view_project(
        current_user, project
    ):
        abort(403)
    
    page_obj = q.page(project.id, page_slug)
    if page_obj is None:
        abort(404)
    
    # Check if page has any revisions (OCR'd content)
    if not page_obj.revisions:
        abort(404)
    
    # Get latest revision
    latest_revision = page_obj.revisions[-1]
    
    # Get available translations
    session = q.get_session()
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
    
    prev_page = pages[current_index - 1] if current_index > 0 else None
    next_page = pages[current_index + 1] if current_index < len(pages) - 1 else None
    
    # Get requested translation language
    translation_lang = request.args.get('translation', 'en')
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
        total_pages=len(pages)
    )
