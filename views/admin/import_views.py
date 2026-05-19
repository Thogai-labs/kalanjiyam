"""Admin views for importing book data."""

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from flask import Blueprint, current_app, request, flash, redirect, url_for, render_template
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.utils.assets import get_page_image_filepath

bp = Blueprint("admin_import", __name__)


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


def get_or_create_user(session, username: str) -> Optional[db.User]:
    """Get existing user or create a placeholder user."""
    if not username:
        return None
    
    user = session.query(db.User).filter_by(username=username).first()
    if user:
        return user
    
    # Create placeholder user if doesn't exist
    user = db.User(
        username=username,
        email=f"{username}@imported.local",
        description="Imported user"
    )
    user.set_password("imported_user_password_change_me")
    session.add(user)
    session.flush()  # Get the ID
    return user


def get_or_create_genre(session, genre_id: int) -> Optional[db.Genre]:
    """Get existing genre or return None."""
    if not genre_id:
        return None
    
    return session.query(db.Genre).filter_by(id=genre_id).first()


def get_or_create_page_status(session, status_name: str) -> db.PageStatus:
    """Get existing page status or create it."""
    status = session.query(db.PageStatus).filter_by(name=status_name).first()
    if status:
        return status
    
    status = db.PageStatus(name=status_name)
    session.add(status)
    session.flush()
    return status


def import_project_data(session, project_data: Dict[str, Any], user_mapping: Dict[str, int] = None) -> db.Project:
    """Import a single project from exported data."""
    if user_mapping is None:
        user_mapping = {}
    
    metadata = project_data['metadata']
    
    # Check if project already exists
    existing_project = session.query(db.Project).filter_by(slug=metadata['slug']).first()
    if existing_project:
        raise ValueError(f"Project with slug '{metadata['slug']}' already exists")
    
    # Get or create creator user
    creator = None
    if metadata.get('creator_username'):
        creator = get_or_create_user(session, metadata['creator_username'])
    
    # Get genre
    genre = None
    if metadata.get('genre_id'):
        genre = get_or_create_genre(session, metadata['genre_id'])
    
    # Create project
    project = db.Project(
        slug=metadata['slug'],
        display_title=metadata['display_title'],
        print_title=metadata['print_title'],
        author=metadata['author'],
        editor=metadata['editor'],
        publisher=metadata['publisher'],
        publication_year=metadata['publication_year'],
        worldcat_link=metadata['worldcat_link'],
        description=metadata['description'],
        notes=metadata['notes'],
        page_numbers=metadata['page_numbers'],
        created_at=datetime.fromisoformat(metadata['created_at']),
        updated_at=datetime.fromisoformat(metadata['updated_at']),
        creator_id=creator.id if creator else None,
        genre_id=genre.id if genre else None
    )
    
    session.add(project)
    session.flush()  # Get the project ID
    
    # Create discussion board
    if project_data['discussion']['board']:
        board = db.Board(title=project_data['discussion']['board']['title'])
        session.add(board)
        session.flush()
        project.board_id = board.id
        
        # Import threads and posts
        for thread_data in project_data['discussion']['threads']:
            thread_author = get_or_create_user(session, thread_data['author_username'])
            
            thread = db.Thread(
                title=thread_data['title'],
                board_id=board.id,
                author_id=thread_author.id if thread_author else None,
                created_at=datetime.fromisoformat(thread_data['created_at']),
                updated_at=datetime.fromisoformat(thread_data['updated_at'])
            )
            session.add(thread)
            session.flush()
            
            for post_data in thread_data['posts']:
                post_author = get_or_create_user(session, post_data['author_username'])
                
                post = db.Post(
                    board_id=board.id,
                    thread_id=thread.id,
                    author_id=post_author.id if post_author else None,
                    created_at=datetime.fromisoformat(post_data['created_at']),
                    updated_at=datetime.fromisoformat(post_data['updated_at']),
                    content=post_data['content']
                )
                session.add(post)
    
    # Create pages
    page_mapping = {}  # Map page slugs to page objects
    for page_data in project_data['pages']:
        status = get_or_create_page_status(session, page_data['status_name'])
        
        page = db.Page(
            project_id=project.id,
            slug=page_data['slug'],
            order=page_data['order'],
            version=page_data['version'],
            ocr_bounding_boxes=page_data['ocr_bounding_boxes'],
            status_id=status.id
        )
        session.add(page)
        session.flush()
        page_mapping[page_data['slug']] = page
    
    # Create revisions
    revision_mapping = {}  # Map revision IDs to revision objects
    for revision_data in project_data['revisions']:
        page = page_mapping.get(revision_data['page_slug'])
        if not page:
            continue
        
        author = get_or_create_user(session, revision_data['author_username'])
        status = get_or_create_page_status(session, revision_data['status_name'])
        
        revision = db.Revision(
            project_id=project.id,
            page_id=page.id,
            author_id=author.id if author else None,
            status_id=status.id,
            created=datetime.fromisoformat(revision_data['created']),
            summary=revision_data['summary'],
            content=revision_data['content']
        )
        session.add(revision)
        session.flush()
        revision_mapping[revision_data.get('revision_id')] = revision
    
    # Create translations
    for translation_data in project_data['translations']:
        author = get_or_create_user(session, translation_data['author_username'])
        
        translation = db.Translation(
            page_id=page_mapping[translation_data['page_slug']].id if 'page_slug' in translation_data else None,
            revision_id=revision_mapping.get(translation_data['revision_id']).id if translation_data.get('revision_id') in revision_mapping else None,
            author_id=author.id if author else None,
            content=translation_data['content'],
            source_language=translation_data['source_language'],
            target_language=translation_data['target_language'],
            translation_engine=translation_data['translation_engine'],
            status=translation_data['status'],
            created_at=datetime.fromisoformat(translation_data['created_at']),
            updated_at=datetime.fromisoformat(translation_data['updated_at'])
        )
        session.add(translation)
    
    return project


def extract_and_import_project(zip_file: Path, session) -> Dict[str, Any]:
    """Extract ZIP file and import project data."""
    import tempfile
    import shutil
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Extract ZIP file
        with zipfile.ZipFile(zip_file, 'r') as zipf:
            zipf.extractall(temp_path)
        
        # Read project data
        json_file = temp_path / "project_data.json"
        if not json_file.exists():
            raise ValueError("No project_data.json found in ZIP file")
        
        with open(json_file, 'r', encoding='utf-8') as f:
            project_data = json.load(f)
        
        # Import project
        project = import_project_data(session, project_data)
        
        # Copy files if they exist
        files_dir = temp_path / "files"
        if files_dir.exists():
            project_files_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "projects" / project.slug
            project_files_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy PDF
            pdf_source = files_dir / "source.pdf"
            if pdf_source.exists():
                pdf_dest = project_files_dir / "pdf" / "source.pdf"
                pdf_dest.parent.mkdir(parents=True, exist_ok=True)
                pdf_dest.write_bytes(pdf_source.read_bytes())
            
            # Copy page images
            pages_source = files_dir / "pages"
            if pages_source.exists():
                pages_dest = project_files_dir / "pages"
                pages_dest.mkdir(parents=True, exist_ok=True)
                
                for image_file in pages_source.glob("*.jpg"):
                    image_dest = pages_dest / image_file.name
                    image_dest.write_bytes(image_file.read_bytes())
        
        return {
            'project': project,
            'metadata': project_data['metadata']
        }


@bp.route("/import", methods=["GET", "POST"])
@login_required
@admin_required
def import_project():
    """Import a project from a ZIP file."""
    if request.method == "POST":
        if 'project_file' not in request.files:
            flash("No file selected")
            return redirect(request.url)
        
        file = request.files['project_file']
        if file.filename == '':
            flash("No file selected")
            return redirect(request.url)
        
        if not file.filename.endswith('.zip'):
            flash("Please upload a ZIP file")
            return redirect(request.url)
        
        try:
            # Save uploaded file temporarily
            filename = secure_filename(file.filename)
            temp_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "imports"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            temp_file = temp_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
            file.save(temp_file)
            
            # Import project
            session = q.get_session()
            result = extract_and_import_project(temp_file, session)
            session.commit()
            
            # Clean up
            temp_file.unlink()
            
            flash(f"Successfully imported project: {result['metadata']['display_title']}")
            return redirect(url_for("proofing.project.detail", slug=result['project'].slug))
            
        except Exception as e:
            session.rollback()
            flash(f"Import failed: {str(e)}")
            return redirect(request.url)
    
    return render_template("admin/import.html")


@bp.route("/import/all-projects", methods=["GET", "POST"])
@login_required
@admin_required
def import_all_projects():
    """Import all projects from a ZIP file."""
    if request.method == "POST":
        if 'projects_file' not in request.files:
            flash("No file selected")
            return redirect(request.url)
        
        file = request.files['projects_file']
        if file.filename == '':
            flash("No file selected")
            return redirect(request.url)
        
        if not file.filename.endswith('.zip'):
            flash("Please upload a ZIP file")
            return redirect(request.url)
        
        try:
            # Save uploaded file temporarily
            filename = secure_filename(file.filename)
            temp_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "imports"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            temp_file = temp_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
            file.save(temp_file)
            
            # Extract and read all projects data
            import tempfile
            import shutil
            
            with tempfile.TemporaryDirectory() as extract_dir:
                extract_path = Path(extract_dir)
                
                with zipfile.ZipFile(temp_file, 'r') as zipf:
                    zipf.extractall(extract_path)
                
                json_file = extract_path / "all_projects_data.json"
                if not json_file.exists():
                    raise ValueError("No all_projects_data.json found in ZIP file")
                
                with open(json_file, 'r', encoding='utf-8') as f:
                    all_projects_data = json.load(f)
                
                # Import all projects
                session = q.get_session()
                imported_projects = []
                
                for project_data in all_projects_data['projects']:
                    try:
                        project = import_project_data(session, project_data)
                        imported_projects.append(project.display_title)
                    except Exception as e:
                        flash(f"Failed to import project {project_data['metadata']['display_title']}: {str(e)}")
                        continue
                
                session.commit()
                
                # Clean up
                temp_file.unlink()
                
                flash(f"Successfully imported {len(imported_projects)} projects")
                return redirect(url_for("proofing.index"))
                
        except Exception as e:
            session.rollback()
            flash(f"Import failed: {str(e)}")
            return redirect(request.url)
    
    return render_template("admin/import_all.html")
