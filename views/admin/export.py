"""Admin views for exporting book data."""

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from flask import Blueprint, current_app, send_file, abort, flash, redirect, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.utils.assets import get_page_image_filepath

bp = Blueprint("admin_export", __name__)


def admin_required(func):
    """Decorator to require admin access."""
    from functools import wraps
    
    @wraps(func)
    def decorated_view(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access required.")
            return redirect(url_for("proofing.index"))
        return func(*args, **kwargs)
    
    return decorated_view


def export_project_data(project: db.Project) -> Dict[str, Any]:
    """Export all data for a single project."""
    session = q.get_session()
    
    # Export project metadata
    project_data = {
        'metadata': {
            'slug': project.slug,
            'display_title': project.display_title,
            'print_title': project.print_title,
            'author': project.author,
            'editor': project.editor,
            'publisher': project.publisher,
            'publication_year': project.publication_year,
            'worldcat_link': project.worldcat_link,
            'description': project.description,
            'notes': project.notes,
            'page_numbers': project.page_numbers,
            'created_at': project.created_at.isoformat(),
            'updated_at': project.updated_at.isoformat(),
            'genre_id': project.genre_id,
            'creator_username': project.creator.username if project.creator else None
        },
        'pages': [],
        'revisions': [],
        'translations': [],
        'discussion': {
            'board': None,
            'threads': [],
            'posts': []
        }
    }
    
    # Export pages
    for page in project.pages:
        page_data = {
            'slug': page.slug,
            'order': page.order,
            'version': page.version,
            'ocr_bounding_boxes': page.ocr_bounding_boxes,
            'status_name': page.status.name if page.status else None
        }
        project_data['pages'].append(page_data)
        
        # Export revisions for this page
        for revision in page.revisions:
            revision_data = {
                'page_slug': page.slug,
                'author_username': revision.author.username if revision.author else None,
                'status_name': revision.status.name if revision.status else None,
                'created': revision.created.isoformat(),
                'summary': revision.summary,
                'content': revision.content
            }
            project_data['revisions'].append(revision_data)
            
            # Export translations for this revision
            for translation in revision.translations:
                translation_data = {
                    'revision_id': revision.id,
                    'author_username': translation.author.username if translation.author else None,
                    'content': translation.content,
                    'source_language': translation.source_language,
                    'target_language': translation.target_language,
                    'translation_engine': translation.translation_engine,
                    'status': translation.status,
                    'created_at': translation.created_at.isoformat(),
                    'updated_at': translation.updated_at.isoformat()
                }
                project_data['translations'].append(translation_data)
    
    # Export discussion data
    if project.board:
        project_data['discussion']['board'] = {
            'title': project.board.title
        }
        
        for thread in project.board.threads:
            thread_data = {
                'title': thread.title,
                'author_username': thread.author.username if thread.author else None,
                'created_at': thread.created_at.isoformat(),
                'updated_at': thread.updated_at.isoformat(),
                'posts': []
            }
            
            for post in thread.posts:
                post_data = {
                    'author_username': post.author.username if post.author else None,
                    'created_at': post.created_at.isoformat(),
                    'updated_at': post.updated_at.isoformat(),
                    'content': post.content
                }
                thread_data['posts'].append(post_data)
            
            project_data['discussion']['threads'].append(thread_data)
    
    return project_data


@bp.route("/export/project/<project_slug>")
@login_required
@admin_required
def export_project(project_slug: str):
    """Export a single project as a ZIP file."""
    project = q.project(project_slug)
    if not project:
        abort(404)
    
    # Create temporary directory for export
    export_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "exports" / f"{project_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    export_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Export project data
        project_data = export_project_data(project)
        
        # Save JSON data
        json_file = export_dir / "project_data.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(project_data, f, indent=2, ensure_ascii=False)
        
        # Copy project files
        project_files_dir = export_dir / "files"
        project_files_dir.mkdir(exist_ok=True)
        
        # Copy PDF
        pdf_source = Path(current_app.config["UPLOAD_FOLDER"]) / "projects" / project_slug / "pdf" / "source.pdf"
        if pdf_source.exists():
            pdf_dest = project_files_dir / "source.pdf"
            pdf_dest.write_bytes(pdf_source.read_bytes())
        
        # Copy page images
        pages_dir = project_files_dir / "pages"
        pages_dir.mkdir(exist_ok=True)
        
        for page in project.pages:
            image_path = get_page_image_filepath(project_slug, page.slug)
            if image_path.exists():
                image_dest = pages_dir / f"{page.slug}.jpg"
                image_dest.write_bytes(image_path.read_bytes())
        
        # Create ZIP file
        zip_path = export_dir.parent / f"{project_slug}_export.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add JSON data
            zipf.write(json_file, "project_data.json")
            
            # Add files
            for file_path in project_files_dir.rglob("*"):
                if file_path.is_file():
                    zipf.write(file_path, f"files/{file_path.relative_to(project_files_dir)}")
        
        # Clean up temporary directory
        import shutil
        shutil.rmtree(export_dir)
        
        return send_file(
            zip_path,
            as_attachment=True,
            download_name=f"{project_slug}_export.zip",
            mimetype="application/zip"
        )
        
    except Exception as e:
        # Clean up on error
        import shutil
        if export_dir.exists():
            shutil.rmtree(export_dir)
        flash(f"Export failed: {str(e)}")
        return redirect(url_for("admin.index"))


@bp.route("/export/all-projects")
@login_required
@admin_required
def export_all_projects():
    """Export all projects as a single ZIP file."""
    projects = q.projects()
    
    # Create temporary directory for export
    export_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "exports" / f"all_projects_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    export_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        all_projects_data = {
            'export_info': {
                'exported_at': datetime.now().isoformat(),
                'total_projects': len(projects),
                'version': '1.0'
            },
            'projects': []
        }
        
        for project in projects:
            project_data = export_project_data(project)
            all_projects_data['projects'].append(project_data)
        
        # Save JSON data
        json_file = export_dir / "all_projects_data.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(all_projects_data, f, indent=2, ensure_ascii=False)
        
        # Create ZIP file
        zip_path = export_dir.parent / "all_projects_export.zip"
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(json_file, "all_projects_data.json")
        
        # Clean up temporary directory
        import shutil
        shutil.rmtree(export_dir)
        
        return send_file(
            zip_path,
            as_attachment=True,
            download_name="all_projects_export.zip",
            mimetype="application/zip"
        )
        
    except Exception as e:
        # Clean up on error
        import shutil
        if export_dir.exists():
            shutil.rmtree(export_dir)
        flash(f"Export failed: {str(e)}")
        return redirect(url_for("admin.index"))
