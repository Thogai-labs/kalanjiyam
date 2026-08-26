"""Manages an internal admin view for site data."""

import logging

from flask import (
    abort,
    after_this_request,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    send_file,
    current_app,
    jsonify,
)
from flask_admin import Admin, AdminIndexView, expose, BaseView as AdminBaseView
from flask_admin.babel import gettext
from flask_admin.form import SecureForm
from flask_admin.helpers import flash_errors, get_redirect_target
from flask_wtf.csrf import generate_csrf
from flask_admin.contrib import sqla
from flask_login import current_user, login_required
from wtforms import PasswordField, SelectField, SelectMultipleField, validators
from werkzeug.utils import secure_filename
from slugify import slugify
import json
import zipfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Dict, Any, Optional

import kalanjiyam.database as db
import kalanjiyam.queries as q

log = logging.getLogger(__name__)
from kalanjiyam.admin_user import (
    WEB_ASSIGNABLE_ROLES,
    assignable_role_choices,
    organization_choices,
    organization_multi_choices,
    soft_delete_user,
    sync_user_org_and_roles,
    validate_user_deletable,
)
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils.admin_access import (
    is_platform_super_admin,
    platform_admin_inaccessible,
    require_org_admin,
    require_platform_super_admin,
)
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.utils.storage import get_storage, page_image_key, pdf_key





def _promote_org_admin(session, org: db.Group, admin_user_id: int | None) -> None:
    """Grant org_admin role and organization membership for the designated admin."""
    if not admin_user_id:
        return
    user = session.query(db.User).filter_by(id=admin_user_id).first()
    if user is None:
        return
    org_admin_role = session.query(db.Role).filter_by(name=db.SiteRole.ORG_ADMIN.value).first()
    if org_admin_role and org_admin_role not in user.roles:
        user.roles.append(org_admin_role)
    user.organization_id = org.id
    session.query(db.UserGroups).filter_by(user_id=user.id).delete()
    session.add(db.UserGroups(user_id=user.id, group_id=org.id))
    session.add(user)


def _schedule_zip_cleanup(zip_path: Path) -> None:
    """Delete export ZIP after the response is sent."""

def _export_revision_payloads(project: db.Project, files_dir: Path) -> None:
    """Save model-named .json revision payloads into files/revisions/{page_slug}/."""
    from kalanjiyam.utils.document_storage import derive_revision_tag, get_page_revision_index, load_revision_document

    revisions_dir = files_dir / "revisions"
    for page in project.pages:
        page_rev_dir = revisions_dir / page.slug
        for revision in page.revisions:
            doc = load_revision_document(revision)
            if doc is not None:
                page_rev_dir.mkdir(parents=True, exist_ok=True)
                tag = derive_revision_tag(revision)
                filename = f"{tag}.json" if tag else f"v{get_page_revision_index(revision)}.json"
                payload_path = page_rev_dir / filename
                if isinstance(doc, dict) and "timestamp" not in doc:
                    created_dt = getattr(revision, "created", None)
                    doc["timestamp"] = created_dt.isoformat() if hasattr(created_dt, "isoformat") else str(created_dt or "")
                raw = (
                    doc.encode("utf-8")
                    if isinstance(doc, str)
                    else json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
                )
                payload_path.write_bytes(raw)


class KalanjiyamIndexView(AdminIndexView):
    def is_accessible(self):
        return current_user.is_authenticated and (current_user.is_moderator or current_user.is_org_admin)
    
    def inaccessible_callback(self, name, **kwargs):
        abort(404)
    
    @expose("/")
    def index(self):
        if is_platform_super_admin():
            return redirect(url_for("platform_view.index"))
        if current_user.is_org_admin:
            return redirect(url_for("org_admin_view.index"))
        
        # For moderators, show the default admin interface
        return super().index()
    
    def _projects_for_current_admin(self):
        projects = q.projects()
        if is_platform_super_admin():
            return projects
        org_id = getattr(current_user, "organization_id", None)
        if org_id is None:
            return []
        return [p for p in projects if any(g.id == org_id for g in p.groups)]

    @expose('/export/project/<project_slug>')
    @login_required
    def export_project(self, project_slug):
        """Export a single project as a ZIP file."""
        if is_platform_super_admin():
            abort(403, description="Superadmins are not allowed to access or export project data.")
        if not current_user.is_org_admin:
            abort(404)
        
        project = q.project(project_slug)
        if not project:
            abort(404)
        if project not in self._projects_for_current_admin():
            abort(403)
        
        # Create temporary directory for export
        export_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "exports" / f"{project_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        export_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # Export project data
            project_data = self._export_project_data(project)
            
            # Save JSON data
            json_file = export_dir / "project_data.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(project_data, f, indent=2, ensure_ascii=False)
            
            # Copy project files
            project_files_dir = export_dir / "files"
            project_files_dir.mkdir(exist_ok=True)
            
            # Copy PDF
            storage = get_storage()
            source_pdf_key = pdf_key(project_slug)
            if storage.exists(source_pdf_key):
                pdf_dest = project_files_dir / "source.pdf"
                pdf_dest.write_bytes(storage.read_bytes(source_pdf_key))
            
            # Copy page images
            pages_dir = project_files_dir / "pages"
            pages_dir.mkdir(exist_ok=True)
            
            for page in project.pages:
                image_path = get_page_image_filepath(project_slug, page.slug)
                if image_path.exists():
                    image_dest = pages_dir / f"{page.slug}.jpg"
                    image_dest.write_bytes(image_path.read_bytes())

            # Export revision payloads as model-named .json files
            _export_revision_payloads(project, project_files_dir)
            
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
            
            _schedule_zip_cleanup(zip_path)
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
            flash(f"Export failed: {str(e)}", "error")
            return redirect(url_for('admin.index'))
    
    @expose('/export/all-projects')
    @login_required
    def export_all_projects(self):
        """Export all projects as a single ZIP file."""
        if is_platform_super_admin():
            abort(403, description="Superadmins are not allowed to access or export project data.")
        if not current_user.is_org_admin:
            abort(404)
        
        projects = self._projects_for_current_admin()
        
        # Create temporary directory for export
        export_dir = Path(current_app.config["UPLOAD_FOLDER"]) / "exports" / f"all_projects_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        export_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            all_projects_data = {
                'export_info': {
                    'exported_at': datetime.now().isoformat(),
                    'total_projects': len(projects),
                    'version': '2.0'
                },
                'projects': []
            }
            
            for project in projects:
                project_data = self._export_project_data(project)
                all_projects_data['projects'].append(project_data)
            
            # Create project folders with JSON and files.
            for project in projects:
                project_dir = export_dir / "projects" / project.slug
                project_dir.mkdir(parents=True, exist_ok=True)
                with open(project_dir / "project_data.json", "w", encoding="utf-8") as f:
                    json.dump(self._export_project_data(project), f, indent=2, ensure_ascii=False)
                files_dir = project_dir / "files"
                files_dir.mkdir(exist_ok=True)
                storage = get_storage()
                source_pdf_key = pdf_key(project.slug)
                if storage.exists(source_pdf_key):
                    (files_dir / "source.pdf").write_bytes(storage.read_bytes(source_pdf_key))
                pages_dir = files_dir / "pages"
                pages_dir.mkdir(exist_ok=True)
                for page in project.pages:
                    image_path = get_page_image_filepath(project.slug, page.slug)
                    if image_path.exists():
                        (pages_dir / f"{page.slug}.jpg").write_bytes(image_path.read_bytes())

                # Export revision payloads as model-named .json files
                _export_revision_payloads(project, files_dir)

            json_file = export_dir / "all_projects_data.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(all_projects_data, f, indent=2, ensure_ascii=False)

            zip_path = export_dir.parent / "all_projects_export.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in export_dir.rglob("*"):
                    if file_path.is_file():
                        zipf.write(file_path, str(file_path.relative_to(export_dir)))
            
            # Clean up temporary directory
            import shutil
            shutil.rmtree(export_dir)
            
            _schedule_zip_cleanup(zip_path)
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
            flash(f"Export failed: {str(e)}", "error")
            return redirect(url_for('admin.index'))
    
    @expose('/export-import')
    @login_required
    def export_import_dashboard(self):
        """Export/import dashboard."""
        if is_platform_super_admin():
            abort(403, description="Superadmins are not allowed to access or export project data.")
        if not current_user.is_org_admin:
            abort(404)
        projects = self._projects_for_current_admin()
        return render_template("admin/export_import.html", projects=projects)

    @expose('/import', methods=['GET', 'POST'])
    @login_required
    def import_project(self):
        """Import a project from a ZIP file."""
        if is_platform_super_admin():
            abort(403, description="Superadmins are not allowed to access or import project data.")
        if not current_user.is_org_admin:
            abort(404)
        
        if request.method == "POST":
            if 'project_file' not in request.files:
                flash("No file selected", "error")
                return redirect(request.url)
            
            file = request.files['project_file']
            if file.filename == '':
                flash("No file selected", "error")
                return redirect(request.url)
            
            if not file.filename.endswith('.zip'):
                flash("Please upload a ZIP file", "error")
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
                result = self._extract_and_import_project(temp_file, session)
                session.commit()

                # Index the imported book. Enqueued after the commit so the
                # worker cannot race ahead of the data.
                from kalanjiyam.tasks.search_index import enqueue_project

                enqueue_project(result['project'].id)

                # Clean up
                temp_file.unlink()
                
                flash(f"Successfully imported project: {result['metadata']['display_title']}", "success")
                return redirect(url_for("proofing.project.detail", slug=result['project'].slug))
                
            except Exception as e:
                session.rollback()
                flash(f"Import failed: {str(e)}", "error")
                return redirect(request.url)
        
        return render_template("admin/import.html")
    
    @expose('/import/all-projects', methods=['GET', 'POST'])
    @login_required
    def import_all_projects(self):
        """Import all projects from a ZIP file."""
        if is_platform_super_admin():
            abort(403, description="Superadmins are not allowed to access or import project data.")
        if not current_user.is_org_admin:
            abort(404)
        
        if request.method == "POST":
            if 'projects_file' not in request.files:
                flash("No file selected", "error")
                return redirect(request.url)
            
            file = request.files['projects_file']
            if file.filename == '':
                flash("No file selected", "error")
                return redirect(request.url)
            
            if not file.filename.endswith('.zip'):
                flash("Please upload a ZIP file", "error")
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
                    imported_project_ids = []
                    
                    for project_data in all_projects_data['projects']:
                        try:
                            slug = project_data["metadata"]["slug"]
                            project_dir = extract_path / "projects" / slug
                            if project_dir.exists():
                                project_json = project_dir / "project_data.json"
                                if not project_json.exists():
                                    raise ValueError(f"Missing project_data.json for {slug}")
                                files_dir = project_dir / "files"
                                temp_single_zip = extract_path / f"{slug}_single.zip"
                                with zipfile.ZipFile(temp_single_zip, "w", zipfile.ZIP_DEFLATED) as zipf2:
                                    zipf2.write(project_json, "project_data.json")
                                    if files_dir.exists():
                                        for file_path in files_dir.rglob("*"):
                                            if file_path.is_file():
                                                zipf2.write(file_path, f"files/{file_path.relative_to(files_dir)}")
                                result = self._extract_and_import_project(temp_single_zip, session)
                                project = result["project"]
                                temp_single_zip.unlink(missing_ok=True)
                            else:
                                project = self._import_project_data(session, project_data)
                            imported_projects.append(project.display_title)
                            imported_project_ids.append(project.id)
                        except Exception as e:
                            flash(f"Failed to import project {project_data['metadata']['display_title']}: {str(e)}", "error")
                            continue

                    session.commit()

                    # Index the imported books, after the commit so the
                    # workers cannot race ahead of the data.
                    from kalanjiyam.tasks.search_index import enqueue_project

                    for imported_id in imported_project_ids:
                        enqueue_project(imported_id)

                    # Clean up
                    temp_file.unlink()
                    
                    flash(f"Successfully imported {len(imported_projects)} projects", "success")
                    return redirect(url_for("proofing.index"))
                    
            except Exception as e:
                session.rollback()
                flash(f"Import failed: {str(e)}", "error")
                return redirect(request.url)
        
        return render_template("admin/import_all.html")

    def _export_project_data(self, project: db.Project) -> Dict[str, Any]:
        """Export all data for a single project."""
        session = q.get_session()
        
        # Export project metadata
        project_data = {
            'format_version': '3.0',
            'organization_slug': project.groups[0].slug if project.groups else None,
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
            from kalanjiyam.utils.document_storage import derive_revision_tag, get_page_revision_index, load_page_ocr, load_revision_document

            page_data = {
                'slug': page.slug,
                'order': page.order,
                'version': page.version,
                'ocr_bounding_boxes': load_page_ocr(page),
                'page_width': page.page_width,
                'page_height': page.page_height,
                'status_name': page.status.name if page.status else None
            }
            project_data['pages'].append(page_data)
            
            # Export revisions for this page
            for revision in page.revisions:
                doc_payload = load_revision_document(revision)
                tag = derive_revision_tag(revision)
                v_num = get_page_revision_index(revision)
                page_version = getattr(revision, "page_version", None)
                version_key = getattr(page_version, "version_key", "") if page_version else ""

                if isinstance(doc_payload, dict) and "timestamp" not in doc_payload:
                    created_dt = getattr(revision, "created", None)
                    doc_payload["timestamp"] = created_dt.isoformat() if hasattr(created_dt, "isoformat") else str(created_dt or "")

                ocr_model = None
                trans_model = None
                src_lang = None
                tgt_lang = None

                if version_key.startswith("ocr:"):
                    ocr_model = version_key.split("ocr:", 1)[1]
                elif tag.startswith("ocr-"):
                    ocr_model = tag.split("ocr-", 1)[1]

                if version_key.startswith("translation:"):
                    parts = version_key.split(":", 2)
                    if len(parts) >= 2:
                        trans_model = parts[1]
                    if len(parts) >= 3 and "->" in parts[2]:
                        src_lang, tgt_lang = parts[2].split("->", 1)
                elif revision.translations:
                    t = revision.translations[0]
                    trans_model = t.translation_engine
                    src_lang = t.source_language
                    tgt_lang = t.target_language
                elif tag.startswith("translation-"):
                    trans_model = tag.split("translation-", 1)[1]

                payload_filename = f"{tag}_v{v_num}.json"

                revision_data = {
                    'revision_key': revision.id,
                    'page_slug': page.slug,
                    'version_key': version_key,
                    'tag': tag,
                    'payload_filename': payload_filename,
                    'ocr_model': ocr_model,
                    'translation_model': trans_model,
                    'source_language': src_lang,
                    'target_language': tgt_lang,
                    'author_username': revision.author.username if revision.author else None,
                    'status_name': revision.status.name if revision.status else None,
                    'created': revision.created.isoformat() if hasattr(revision.created, "isoformat") else str(revision.created),
                    'summary': revision.summary,
                    'content': revision.content,
                    'content_format': getattr(revision, 'content_format', 'plain'),
                    'document': doc_payload,
                }
                project_data['revisions'].append(revision_data)
                
                # Export translations for this revision
                for translation in revision.translations:
                    translation_data = {
                        'revision_key': revision.id,
                        'page_slug': page.slug,
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
    
    def _get_or_create_user(self, session, username: str) -> Optional[db.User]:
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
    
    def _get_or_create_genre(self, session, genre_id: int) -> Optional[db.Genre]:
        """Get existing genre or return None."""
        if not genre_id:
            return None
        
        return session.query(db.Genre).filter_by(id=genre_id).first()
    
    def _get_or_create_page_status(self, session, status_name: str) -> db.PageStatus:
        """Get existing page status or create it."""
        status = session.query(db.PageStatus).filter_by(name=status_name).first()
        if status:
            return status
        
        status = db.PageStatus(name=status_name)
        session.add(status)
        session.flush()
        return status
    
    def _import_project_data(self, session, project_data: Dict[str, Any], user_mapping: Dict[str, int] = None) -> db.Project:
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
            creator = self._get_or_create_user(session, metadata['creator_username'])
        
        # Get genre
        genre = None
        if metadata.get('genre_id'):
            genre = self._get_or_create_genre(session, metadata['genre_id'])
        
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
                thread_author = self._get_or_create_user(session, thread_data['author_username'])
                
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
                    post_author = self._get_or_create_user(session, post_data['author_username'])
                    
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
            status = self._get_or_create_page_status(session, page_data['status_name'])
            
            page = db.Page(
                project_id=project.id,
                slug=page_data['slug'],
                order=page_data['order'],
                version=page_data['version'],
                ocr_bounding_boxes=page_data.get('ocr_bounding_boxes'),
                page_width=page_data.get('page_width'),
                page_height=page_data.get('page_height'),
                status_id=status.id
            )
            session.add(page)
            session.flush()
            # Also persist OCR data to S3/VersityGW
            if page_data.get('ocr_bounding_boxes'):
                from kalanjiyam.utils.document_storage import save_page_ocr

                try:
                    save_page_ocr(page, page_data['ocr_bounding_boxes'])
                except Exception:
                    pass
            page_mapping[page_data['slug']] = page
        
        # Create revisions
        revision_mapping = {}  # Map revision keys to revision objects
        for revision_data in project_data['revisions']:
            page = page_mapping.get(revision_data['page_slug'])
            if not page:
                continue
            
            author = self._get_or_create_user(session, revision_data['author_username'])
            status = self._get_or_create_page_status(session, revision_data['status_name'])
            
            revision = db.Revision(
                project_id=project.id,
                page_id=page.id,
                author_id=author.id if author else None,
                status_id=status.id,
                created=datetime.fromisoformat(revision_data['created']),
                summary=revision_data['summary'],
                content=revision_data['content'],
                content_format=revision_data.get('content_format', 'plain'),
                document=revision_data.get('document'),
            )
            session.add(revision)
            session.flush()
            # Also persist document to S3/VersityGW
            if revision_data.get('document'):
                from kalanjiyam.utils.document_storage import save_revision_document

                try:
                    save_revision_document(revision, revision_data['document'])
                except Exception:
                    pass
            revision_mapping[revision_data.get('revision_key')] = revision
        
        # Create translations
        for translation_data in project_data['translations']:
            author = self._get_or_create_user(session, translation_data['author_username'])
            
            translation = db.Translation(
                page_id=page_mapping[translation_data['page_slug']].id if 'page_slug' in translation_data else None,
                revision_id=revision_mapping.get(translation_data['revision_key']).id if translation_data.get('revision_key') in revision_mapping else None,
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

        # Enforce Org Admin's organization assignment
        if not is_platform_super_admin() and current_user.is_org_admin:
            org_id = current_user.organization_id
            if org_id:
                session.add(db.ProjectGroups(group_id=org_id, project_id=project.id))
        else:
            org_slug = project_data.get("organization_slug")
            if org_slug:
                org = q.organization_by_slug(org_slug)
                if org:
                    session.add(db.ProjectGroups(group_id=org.id, project_id=project.id))
        
        return project
    
    def _extract_and_import_project(self, zip_file: Path, session) -> Dict[str, Any]:
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
            project = self._import_project_data(session, project_data)
            
            # Copy files if they exist
            files_dir = temp_path / "files"
            if files_dir.exists():
                storage = get_storage()

                # Copy PDF
                pdf_source = files_dir / "source.pdf"
                if pdf_source.exists():
                    storage.save(pdf_key(project.slug), pdf_source)

                # Copy page images
                pages_source = files_dir / "pages"
                if pages_source.exists():
                    for image_file in pages_source.glob("*.jpg"):
                        storage.save(
                            page_image_key(project.slug, image_file.stem), image_file
                        )
            
            return {
                'project': project,
                'metadata': project_data['metadata']
            }


def _sync_job_and_item_metrics(session, job):
    """Auto-heal metrics: sync item/page statuses and re-aggregate totals
    for jobs whose pages have finished processing but weren't marked COMPLETED."""
    if not job or job.status == 'COMPLETED':
        return
    from kalanjiyam.models.batch import BatchItem, BatchOcrPage
    items = session.query(BatchItem).filter_by(job_id=job.id).all()
    if not items:
        return
    all_completed = True
    item_ids = [item.id for item in items]
    all_pages = (
        session.query(BatchOcrPage)
        .filter(BatchOcrPage.batch_item_id.in_(item_ids))
        .all()
    ) if item_ids else []
    pages_by_item = {item.id: [] for item in items}
    for p in all_pages:
        pages_by_item[p.batch_item_id].append(p)

    for item in items:
        page_records = pages_by_item.get(item.id, [])
        
        # Retroactively backfill source_size_bytes from Project metadata or Storage
        if item.source_size_bytes is None or item.source_size_bytes == 0:
            try:
                from kalanjiyam.utils.storage import get_storage, pdf_key, project_docx_key
                db_proj = session.query(db.Project).filter_by(id=item.project_id).first()
                if db_proj:
                    # 1. Check extracted_metadata stored during PDF upload
                    meta = db_proj.extracted_metadata or {}
                    meta_src_sz = (meta.get("source_file") or {}).get("size_bytes")
                    if meta_src_sz:
                        item.source_size_bytes = meta_src_sz
                    
                    # 2. Check active PDF/DOCX storage keys if not deleted
                    if not item.source_size_bytes and db_proj.slug:
                        storage = get_storage()
                        for k, size in storage.list_keys(pdf_key(db_proj.slug)):
                            if k == pdf_key(db_proj.slug):
                                item.source_size_bytes = size
                                break
                                
                        if not item.source_size_bytes:
                            for k, size in storage.list_keys(project_docx_key(db_proj.slug)):
                                if k == project_docx_key(db_proj.slug):
                                    item.source_size_bytes = size
                                    break

                    # 3. Fallback to sum of page extracted images if original uploaded file size missing
                    if not item.source_size_bytes:
                        sum_ext = sum(p.extracted_image_size_bytes or 0 for p in page_records)
                        if sum_ext > 0:
                            item.source_size_bytes = sum_ext
            except Exception:
                pass
        # Auto-complete pages that have recorded metrics or existing DB translations but are still PENDING
        for p in page_records:
            if p.status == 'PENDING':
                if p.ocr_latency_ms or p.translation_latency_ms or p.ocr_data_size_bytes or p.translation_data_size_bytes:
                    p.status = 'COMPLETED'
                    p.completed_at = p.completed_at or datetime.utcnow()
                else:
                    db_p = session.query(db.Page).filter_by(project_id=item.project_id, order=p.page_number).first()
                    if db_p and (db_p.translations or db_p.revisions):
                        p.status = 'COMPLETED'
                        p.completed_at = p.completed_at or datetime.utcnow()
                        if db_p.translations:
                            latest_trans = db_p.translations[-1]
                            trans_content = getattr(latest_trans, 'content', '') or ''
                            p.translation_data_size_bytes = p.translation_data_size_bytes or len(trans_content.encode('utf-8'))

        completed_pages = [p for p in page_records if p.status == 'COMPLETED']
        if completed_pages:
            # Re-aggregate item-level totals from completed pages
            item.total_ocr_latency_ms = sum(p.ocr_latency_ms or 0 for p in completed_pages)
            item.total_translation_latency_ms = sum(p.translation_latency_ms or 0 for p in completed_pages)
            item.extracted_images_size_bytes = sum(p.extracted_image_size_bytes or 0 for p in completed_pages)
            item.cropped_images_size_bytes = sum(p.cropped_image_size_bytes or 0 for p in completed_pages)
            item.ocr_data_size_bytes = sum(p.ocr_data_size_bytes or 0 for p in completed_pages)
            item.translation_data_size_bytes = sum(p.translation_data_size_bytes or 0 for p in completed_pages)
            
            engines = [p.engine for p in completed_pages if p.engine]
            item.engine = engines[0] if engines else item.engine
            conf_list = [p.confidence for p in completed_pages if p.confidence is not None]
            item.avg_confidence = (sum(conf_list) / len(conf_list)) if conf_list else item.avg_confidence
            item.min_confidence = min(conf_list) if conf_list else item.min_confidence
            p05_list = [p.p05 for p in completed_pages if p.p05 is not None]
            item.avg_p05 = (sum(p05_list) / len(p05_list)) if p05_list else item.avg_p05
            item.low_conf_page_count = sum(1 for p in completed_pages if (p.confidence is not None and p.confidence < 0.70) or (p.p05 is not None and p.p05 < 0.70))
            item.total_blocks = sum(p.blocks or 0 for p in completed_pages)
            item.total_chars = sum(p.chars or 0 for p in completed_pages)
            item.total_engine_latency_ms = sum(p.engine_latency_ms or 0 for p in completed_pages)

            if (item.total_pages is None or item.total_pages == 0) and page_records:
                item.total_pages = len(page_records)

            target_pages = item.total_pages or len(page_records) or 1
            if len(completed_pages) >= target_pages or len(completed_pages) >= len(page_records) or job.job_type.startswith('SINGLE_PAGE_PROOFING'):
                item.status = 'COMPLETED'
                item.completed_at = item.completed_at or datetime.utcnow()

        if item.status != 'COMPLETED':
            all_completed = False

    if all_completed and items:
        job.status = 'COMPLETED'
        job.completed_at = job.completed_at or datetime.utcnow()
    try:
        session.commit()
    except Exception:
        session.rollback()


def _format_source_size_bytes(size_bytes):
    """Format bytes to human-readable string (B, KB, MB, GB)."""
    if not size_bytes or size_bytes <= 0:
        return "0 MB"
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def _format_ocr_time_mins(total_time_mins):
    """Format total OCR time in minutes."""
    if not total_time_mins or total_time_mins <= 0:
        return "0.0 mins"
    if total_time_mins < 0.1:
        return f"{total_time_mins:.2f} mins"
    return f"{total_time_mins:,.1f} mins"


def _batch_ocr_summary_dict(total_ocr_ms, total_pages, total_source_size_bytes, total_extraction_ms=0.0):
    """Build summary dictionary for batch OCR KPI cards."""
    total_ocr_mins = (total_ocr_ms / 1000.0) / 60.0 if total_ocr_ms else 0.0
    total_extract_ocr_ms = (total_ocr_ms or 0.0) + (total_extraction_ms or 0.0)
    total_extract_ocr_mins = (total_extract_ocr_ms / 1000.0) / 60.0 if total_extract_ocr_ms else 0.0
    return {
        "total_ocr_time_mins": round(total_ocr_mins, 2),
        "total_ocr_time_formatted": _format_ocr_time_mins(total_ocr_mins),
        "total_extraction_time_mins": round((total_extraction_ms / 60000.0) if total_extraction_ms else 0.0, 2),
        "total_extraction_time_formatted": _format_ocr_time_mins((total_extraction_ms / 60000.0) if total_extraction_ms else 0.0),
        "total_extract_ocr_time_mins": round(total_extract_ocr_mins, 2),
        "total_extract_ocr_time_formatted": _format_ocr_time_mins(total_extract_ocr_mins),
        "total_pages": total_pages,
        "total_pages_formatted": f"{total_pages:,}",
        "total_source_size_bytes": total_source_size_bytes or 0,
        "total_source_size_formatted": _format_source_size_bytes(total_source_size_bytes),
    }


class PlatformView(AdminBaseView):
    """Super-admin platform overview."""

    def is_accessible(self):
        return is_platform_super_admin()

    def inaccessible_callback(self, name, **kwargs):
        return platform_admin_inaccessible()

    @expose("/")
    def index(self):
        require_platform_super_admin()
        orgs = q.groups()
        total_storage_used = sum(g.storage_used_bytes or 0 for g in orgs)
        total_ocr_used = sum(g.ocr_credits_used or 0 for g in orgs)
        total_translation_used = sum(g.translation_credits_used or 0 for g in orgs)
        return render_template(
            "admin/platform_dashboard.html",
            orgs=orgs,
            org_count=len(orgs),
            total_storage_used=total_storage_used,
            total_ocr_used=total_ocr_used,
            total_translation_used=total_translation_used,
        )

    @expose("/user_analytics")
    def user_analytics(self):
        require_platform_super_admin()
        orgs = q.groups()
        session = q.get_session()
        
        # Calculate overall stats for each organization
        org_stats = []
        for org in orgs:
            users_count = len(q.users_in_group(org.id))
            projects_count, _ = q.projects_in_group(org.id, page=1, per_page=1000)
            projects_count = len(projects_count)
            
            # Count revisions made by users of this org
            user_ids = [u.id for u in q.users_in_group(org.id)]
            revisions_count = 0
            if user_ids:
                revisions_count = session.query(db.Revision).filter(db.Revision.author_id.in_(user_ids)).count()
                
            org_stats.append({
                "org": org,
                "users_count": users_count,
                "projects_count": projects_count,
                "revisions_count": revisions_count,
                "ocr_count": org.ocr_credits_used or 0,
                "translation_count": org.translation_credits_used or 0,
                "storage_used": org.storage_used_bytes or 0,
            })
            
        return render_template(
            "admin/org_analytics.html",
            org_stats=org_stats,
            is_platform=True
        )

    @expose("/user_analytics/<int:org_id>")
    def org_user_analytics(self, org_id):
        require_platform_super_admin()
        org = q.group(org_id)
        if not org:
            abort(404)
            
        session = q.get_session()
        users = q.users_in_group(org.id)
        
        user_stats = []
        for user in users:
            projects_count = session.query(db.Project).filter_by(creator_id=user.id).count()
            revisions_count = session.query(db.Revision).filter_by(author_id=user.id).count()
            ocr_count = session.query(db.UsageLog).filter_by(user_id=user.id, action="run_ocr").count()
            translation_count = user.translation_credits_used or 0
            user_stats.append({
                "user": user,
                "projects_count": projects_count,
                "revisions_count": revisions_count,
                "ocr_count": ocr_count,
                "translation_count": translation_count,
            })
            
        return render_template(
            "admin/org_user_analytics.html",
            org=org,
            user_stats=user_stats,
            is_platform=True
        )

    @expose("/cli_batch_ocr")
    @expose("/cli_batch_ocr/<int:job_id>")
    def cli_batch_ocr(self, job_id=None):
        require_platform_super_admin()
        session = q.get_session()
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
        from sqlalchemy import func, or_
        from flask import request

        # Page 1: Dedicated Job List View
        if job_id is None:
            category = request.args.get('category', 'all')  # all, proofer, ui_batch, cli_batch
            task_type = request.args.get('task_type', 'all')  # all, ocr, translation
            search_query = request.args.get('q', '').strip()
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            if page < 1:
                page = 1
            if per_page < 1 or per_page > 100:
                per_page = 20

            query = session.query(BatchJob)

            # 1. Filter by Category
            if category == 'proofer':
                query = query.filter(BatchJob.job_type.in_(['SINGLE_PAGE_PROOFING_OCR', 'SINGLE_PAGE_PROOFING_TRANSLATION']))
            elif category == 'ui_batch':
                query = query.filter(BatchJob.job_type.in_(['UI_BATCH_OCR', 'UI_BATCH_TRANSLATION']))
            elif category == 'cli_batch':
                query = query.filter(BatchJob.job_type.in_(['BATCH_OCR', 'BATCH_OCR_JSONL', 'JSONL_IMPORT']))

            # 2. Filter by Task Type (OCR vs Translation)
            if task_type == 'ocr':
                query = query.filter(or_(
                    BatchJob.job_type.like('%OCR%'),
                    BatchJob.job_type == 'JSONL_IMPORT'
                ))
            elif task_type == 'translation':
                query = query.filter(BatchJob.job_type.like('%TRANSLATION%'))

            # 3. Filter by Search Query
            if search_query:
                query = query.filter(or_(
                    BatchJob.target_uri.ilike(f"%{search_query}%"),
                    BatchJob.job_type.ilike(f"%{search_query}%")
                ))

            # Database-level summary aggregation
            summary_query = (
                session.query(
                    func.coalesce(func.sum(BatchItem.total_ocr_latency_ms), 0.0),
                    func.coalesce(func.sum(BatchItem.extraction_latency_ms), 0.0),
                    func.coalesce(func.sum(BatchItem.total_pages), 0),
                    func.coalesce(func.sum(BatchItem.source_size_bytes), 0),
                )
                .join(BatchJob, BatchItem.job_id == BatchJob.id)
            )

            if category == 'proofer':
                summary_query = summary_query.filter(BatchJob.job_type.in_(['SINGLE_PAGE_PROOFING_OCR', 'SINGLE_PAGE_PROOFING_TRANSLATION']))
            elif category == 'ui_batch':
                summary_query = summary_query.filter(BatchJob.job_type.in_(['UI_BATCH_OCR', 'UI_BATCH_TRANSLATION']))
            elif category == 'cli_batch':
                summary_query = summary_query.filter(BatchJob.job_type.in_(['BATCH_OCR', 'BATCH_OCR_JSONL', 'JSONL_IMPORT']))

            if task_type == 'ocr':
                summary_query = summary_query.filter(or_(
                    BatchJob.job_type.like('%OCR%'),
                    BatchJob.job_type == 'JSONL_IMPORT'
                ))
            elif task_type == 'translation':
                summary_query = summary_query.filter(BatchJob.job_type.like('%TRANSLATION%'))

            if search_query:
                summary_query = summary_query.filter(or_(
                    BatchJob.target_uri.ilike(f"%{search_query}%"),
                    BatchJob.job_type.ilike(f"%{search_query}%")
                ))

            tot_ocr_ms, tot_extract_ms, tot_pages, tot_source_bytes = (
                summary_query.first() or (0.0, 0.0, 0, 0)
            )

            summary = _batch_ocr_summary_dict(
                total_ocr_ms=float(tot_ocr_ms),
                total_pages=int(tot_pages),
                total_source_size_bytes=int(tot_source_bytes),
                total_extraction_ms=float(tot_extract_ms),
            )

            # Paginated Jobs list
            total_jobs = query.count()
            num_pages = max(1, (total_jobs + per_page - 1) // per_page)
            if page > num_pages:
                page = num_pages

            jobs_on_page = (
                query.order_by(BatchJob.id.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            )

            for j in jobs_on_page:
                if j.status != 'COMPLETED':
                    _sync_job_and_item_metrics(session, j)

            job_ids = [j.id for j in jobs_on_page]
            stats_map = {}
            if job_ids:
                job_item_stats = (
                    session.query(
                        BatchItem.job_id,
                        func.count(BatchItem.id).label("item_count"),
                        func.coalesce(func.sum(BatchItem.total_pages), 0).label("total_pages"),
                    )
                    .filter(BatchItem.job_id.in_(job_ids))
                    .group_by(BatchItem.job_id)
                    .all()
                )
                stats_map = {row.job_id: (row.item_count, row.total_pages) for row in job_item_stats}

            jobs_list = []
            for j in jobs_on_page:
                item_count, job_pages = stats_map.get(j.id, (0, 0))
                jobs_list.append({
                    "id": j.id,
                    "target_uri": j.target_uri,
                    "job_type": j.job_type,
                    "status": j.status,
                    "created_at": j.created_at,
                    "completed_at": j.completed_at,
                    "item_count": item_count,
                    "total_pages": job_pages,
                })

            return render_template(
                "admin/cli_batch_ocr.html",
                is_job_list=True,
                jobs_list=jobs_list,
                summary=summary,
                current_category=category,
                current_task_type=task_type,
                search_query=search_query,
                page=page,
                per_page=per_page,
                total=total_jobs,
                num_pages=num_pages,
            )

        # Page 2: Dedicated Job Details & Metrics View
        selected_job = session.query(BatchJob).get(job_id)
        if not selected_job:
            abort(404)

        if selected_job.status != 'COMPLETED':
            _sync_job_and_item_metrics(session, selected_job)

        job_summary_row = (
            session.query(
                func.coalesce(func.sum(BatchItem.total_ocr_latency_ms), 0.0),
                func.coalesce(func.sum(BatchItem.extraction_latency_ms), 0.0),
                func.coalesce(func.sum(BatchItem.total_pages), 0),
                func.coalesce(func.sum(BatchItem.source_size_bytes), 0),
            )
            .filter(BatchItem.job_id == selected_job.id)
            .first()
        )
        job_total_ocr_ms, job_total_extraction_ms, job_total_pages, job_total_source_size = (
            job_summary_row or (0.0, 0.0, 0, 0)
        )

        summary = _batch_ocr_summary_dict(
            total_ocr_ms=float(job_total_ocr_ms),
            total_pages=int(job_total_pages),
            total_source_size_bytes=int(job_total_source_size),
            total_extraction_ms=float(job_total_extraction_ms),
        )

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        if page < 1:
            page = 1
        if per_page < 1 or per_page > 100:
            per_page = 25

        items_query = session.query(BatchItem).filter(BatchItem.job_id == selected_job.id)
        total_items = items_query.count()
        num_pages = max(1, (total_items + per_page - 1) // per_page)
        if page > num_pages:
            page = num_pages

        items = (
            items_query.order_by(BatchItem.id.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        item_ids = [item.id for item in items]
        pages_by_item = {item.id: [] for item in items}
        if item_ids:
            page_records = (
                session.query(BatchOcrPage)
                .filter(BatchOcrPage.batch_item_id.in_(item_ids))
                .order_by(BatchOcrPage.batch_item_id.asc(), BatchOcrPage.page_number.asc())
                .all()
            )
            for p_rec in page_records:
                pages_by_item[p_rec.batch_item_id].append(p_rec)

        item_metrics = []
        for item in items:
            p_recs = pages_by_item.get(item.id, [])
            pages = item.total_pages or len(p_recs)
            time_sec = (item.total_ocr_latency_ms / 1000.0) if item.total_ocr_latency_ms else None
            trans_time_sec = (item.total_translation_latency_ms / 1000.0) if item.total_translation_latency_ms else None
            avg_per_page_sec = (time_sec / pages) if (time_sec is not None and pages > 0) else None
            avg_trans_per_page_sec = (trans_time_sec / pages) if (trans_time_sec is not None and pages > 0) else None

            page_metrics_list = []
            for p_rec in p_recs:
                p_time_sec = (p_rec.ocr_latency_ms / 1000.0) if p_rec.ocr_latency_ms else None
                p_trans_time_sec = (p_rec.translation_latency_ms / 1000.0) if p_rec.translation_latency_ms else None
                p_eng_lat_sec = (p_rec.engine_latency_ms / 1000.0) if p_rec.engine_latency_ms else None
                page_metrics_list.append({
                    "id": p_rec.id,
                    "page_number": p_rec.page_number,
                    "status": p_rec.status,
                    "time_took_sec": round(p_time_sec, 2) if p_time_sec is not None else None,
                    "translation_time_took_sec": round(p_trans_time_sec, 2) if p_trans_time_sec is not None else None,
                    "extracted_image_size_bytes": p_rec.extracted_image_size_bytes,
                    "cropped_image_size_bytes": p_rec.cropped_image_size_bytes,
                    "ocr_data_size_bytes": p_rec.ocr_data_size_bytes,
                    "translation_data_size_bytes": p_rec.translation_data_size_bytes,
                    "source_lang": p_rec.source_lang,
                    "target_lang": p_rec.target_lang,
                    "engine": p_rec.engine,
                    "confidence": round(p_rec.confidence * 100, 1) if p_rec.confidence is not None else None,
                    "p05": round(p_rec.p05 * 100, 1) if p_rec.p05 is not None else None,
                    "blocks": p_rec.blocks,
                    "chars": p_rec.chars,
                    "engine_latency_sec": round(p_eng_lat_sec, 2) if p_eng_lat_sec is not None else None,
                    "attempt_count": p_rec.attempt_count,
                    "error_message": p_rec.error_message,
                })

            total_eng_lat_sec = (item.total_engine_latency_ms / 1000.0) if item.total_engine_latency_ms else None
            item_metrics.append({
                "id": item.id,
                "name": item.file_path,
                "size_bytes": item.source_size_bytes,
                "extracted_images_size_bytes": item.extracted_images_size_bytes,
                "cropped_images_size_bytes": item.cropped_images_size_bytes,
                "ocr_data_size_bytes": item.ocr_data_size_bytes,
                "translation_data_size_bytes": item.translation_data_size_bytes,
                "source_lang": item.source_lang,
                "target_lang": item.target_lang,
                "engine": item.engine,
                "avg_confidence": round(item.avg_confidence * 100, 1) if item.avg_confidence is not None else None,
                "min_confidence": round(item.min_confidence * 100, 1) if item.min_confidence is not None else None,
                "avg_p05": round(item.avg_p05 * 100, 1) if item.avg_p05 is not None else None,
                "low_conf_page_count": item.low_conf_page_count,
                "total_blocks": item.total_blocks,
                "total_chars": item.total_chars,
                "total_engine_latency_sec": round(total_eng_lat_sec, 2) if total_eng_lat_sec is not None else None,
                "avg_engine_latency_sec": round((total_eng_lat_sec / pages), 2) if (total_eng_lat_sec is not None and pages > 0) else None,
                "pages": pages,
                "time_took_sec": round(time_sec, 2) if time_sec is not None else None,
                "translation_time_took_sec": round(trans_time_sec, 2) if trans_time_sec is not None else None,
                "avg_per_page_sec": round(avg_per_page_sec, 2) if avg_per_page_sec is not None else None,
                "avg_trans_per_page_sec": round(avg_trans_per_page_sec, 2) if avg_trans_per_page_sec is not None else None,
                "status": item.status,
                "error_message": item.error_message,
                "extraction_latency_ms": item.extraction_latency_ms,
                "project_id": item.project_id,
                "page_metrics": page_metrics_list,
            })

        return render_template(
            "admin/cli_batch_ocr.html",
            is_job_list=False,
            selected_job=selected_job,
            item_metrics=item_metrics,
            summary=summary,
            page=page,
            per_page=per_page,
            total=total_items,
            num_pages=num_pages,
        )

    @expose("/cli_batch_ocr/<int:job_id>/export_summary_csv")
    def cli_batch_ocr_export_summary_csv(self, job_id):
        require_platform_super_admin()
        session = q.get_session()
        import csv
        import io
        from flask import Response
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
        from sqlalchemy import func

        job = session.query(BatchJob).get(job_id)
        if not job:
            abort(404)

        items = session.query(BatchItem).filter_by(job_id=job.id).all()
        item_ids = [i.id for i in items]
        page_counts_map = {}
        if item_ids:
            page_counts = (
                session.query(BatchOcrPage.batch_item_id, func.count(BatchOcrPage.id))
                .filter(BatchOcrPage.batch_item_id.in_(item_ids))
                .group_by(BatchOcrPage.batch_item_id)
                .all()
            )
            page_counts_map = {b_id: count for b_id, count in page_counts}

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Item ID",
            "Name / File Path",
            "Engine",
            "Pages",
            "Avg Confidence (%)",
            "Min Confidence (%)",
            "Pages <0.7",
            "Avg p05 (%)",
            "Total Blocks",
            "Total Chars",
            "Source Lang",
            "Target Lang",
            "Source Size (Bytes)",
            "Extracted Images Size (Bytes)",
            "Cropped Images Size (Bytes)",
            "OCR Data Size (Bytes)",
            "Translation Data Size (Bytes)",
            "OCR Time Took (Sec)",
            "Total Engine Latency (Sec)",
            "Avg Engine Latency (Sec)",
            "Translation Time Took (Sec)",
            "Avg Per Page OCR Time (Sec)",
            "Avg Per Page Translation Time (Sec)",
            "Status",
            "Extraction Latency (ms)",
            "Error Message",
        ])

        for item in items:
            pages = item.total_pages or page_counts_map.get(item.id, 0)
            time_sec = (item.total_ocr_latency_ms / 1000.0) if item.total_ocr_latency_ms else None
            eng_lat_sec = (item.total_engine_latency_ms / 1000.0) if item.total_engine_latency_ms else None
            avg_eng_lat_sec = (eng_lat_sec / pages) if (eng_lat_sec is not None and pages > 0) else None
            trans_time_sec = (item.total_translation_latency_ms / 1000.0) if item.total_translation_latency_ms else None
            avg_per_page_sec = (time_sec / pages) if (time_sec is not None and pages > 0) else None

            writer.writerow([
                item.id,
                item.file_path,
                item.engine or "",
                pages,
                round(item.avg_confidence * 100, 1) if item.avg_confidence is not None else "",
                round(item.min_confidence * 100, 1) if item.min_confidence is not None else "",
                item.low_conf_page_count if item.low_conf_page_count is not None else 0,
                round(item.avg_p05 * 100, 1) if item.avg_p05 is not None else "",
                item.total_blocks if item.total_blocks is not None else "",
                item.total_chars if item.total_chars is not None else "",
                item.source_lang or "",
                item.target_lang or "",
                item.source_size_bytes or 0,
                item.extracted_images_size_bytes or 0,
                item.cropped_images_size_bytes or 0,
                item.ocr_data_size_bytes or 0,
                item.translation_data_size_bytes or 0,
                round(time_sec, 2) if time_sec is not None else "",
                round(eng_lat_sec, 2) if eng_lat_sec is not None else "",
                round(avg_eng_lat_sec, 2) if avg_eng_lat_sec is not None else "",
                round(trans_time_sec, 2) if trans_time_sec is not None else "",
                round(avg_per_page_sec, 2) if avg_per_page_sec is not None else "",
                round(trans_time_sec / pages, 2) if (trans_time_sec is not None and pages > 0) else "",
                item.status,
                round(item.extraction_latency_ms, 2) if item.extraction_latency_ms else "",
                item.error_message or "",
            ])

        filename = f"batch_job_{job.id}_document_summary.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @expose("/metadata_metrics")
    def metadata_metrics(self):
        """Extraction runs across every document, the OCR dashboard's sibling."""
        require_platform_super_admin()
        session = q.get_session()
        return render_template(
            "admin/metadata_metrics.html",
            **_metadata_metrics_payload(
                session,
                status=request.args.get("status", "all"),
                search=request.args.get("q", "").strip(),
                start_date=request.args.get("start_date", "").strip(),
                end_date=request.args.get("end_date", "").strip(),
            ),
        )

    @expose("/metadata_metrics/export_csv")
    def metadata_metrics_export_csv(self):
        require_platform_super_admin()
        return _metadata_metrics_csv_response(
            q.get_session(),
            status=request.args.get("status", "all"),
            search=request.args.get("q", "").strip(),
            start_date=request.args.get("start_date", "").strip(),
            end_date=request.args.get("end_date", "").strip(),
        )


    @expose("/cli_batch_ocr/<int:job_id>/export_pages_csv")
    def cli_batch_ocr_export_pages_csv(self, job_id):
        require_platform_super_admin()
        session = q.get_session()
        import csv
        import io
        from flask import Response
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage

        job = session.query(BatchJob).get(job_id)
        if not job:
            abort(404)

        items = session.query(BatchItem).filter_by(job_id=job.id).all()
        item_ids = [i.id for i in items]

        p_records = (
            session.query(BatchOcrPage)
            .filter(BatchOcrPage.batch_item_id.in_(item_ids))
            .order_by(BatchOcrPage.batch_item_id.asc(), BatchOcrPage.page_number.asc())
            .all()
        ) if item_ids else []

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Item ID",
            "Name / File Path",
            "Page Number",
            "Engine",
            "Confidence (%)",
            "p05 (%)",
            "Blocks",
            "Chars",
            "Source Lang",
            "Target Lang",
            "Extracted Image Size (Bytes)",
            "Cropped Images Size (Bytes)",
            "OCR Data Size (Bytes)",
            "Translation Data Size (Bytes)",
            "OCR Time Took (Sec)",
            "Engine Latency (Sec)",
            "Translation Time Took (Sec)",
            "Status",
            "Attempt Count",
            "Error Message",
        ])

        item_path_map = {i.id: i.file_path for i in items}

        for p in p_records:
            p_time = (p.ocr_latency_ms / 1000.0) if p.ocr_latency_ms else None
            p_eng_lat = (p.engine_latency_ms / 1000.0) if p.engine_latency_ms else None
            p_trans_time = (p.translation_latency_ms / 1000.0) if p.translation_latency_ms else None
            writer.writerow([
                p.batch_item_id,
                item_path_map.get(p.batch_item_id, ""),
                p.page_number,
                p.engine or "",
                round(p.confidence * 100, 1) if p.confidence is not None else "",
                round(p.p05 * 100, 1) if p.p05 is not None else "",
                p.blocks if p.blocks is not None else "",
                p.chars if p.chars is not None else "",
                p.source_lang or "",
                p.target_lang or "",
                p.extracted_image_size_bytes or 0,
                p.cropped_image_size_bytes or 0,
                p.ocr_data_size_bytes or 0,
                p.translation_data_size_bytes or 0,
                round(p_time, 2) if p_time is not None else "",
                round(p_eng_lat, 2) if p_eng_lat is not None else "",
                round(p_trans_time, 2) if p_trans_time is not None else "",
                p.status,
                p.attempt_count,
                p.error_message or "",
            ])

        filename = f"batch_job_{job.id}_per_page_metrics.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @expose("/settings", methods=["GET", "POST"])
    def settings(self):
        require_platform_super_admin()
        
        session = q.get_session()
        system_settings = q.get_system_settings()
        
        from flask_wtf import FlaskForm
        from wtforms import IntegerField, SelectField
        from wtforms.validators import Optional as wtform_Optional, NumberRange
        from kalanjiyam.utils.ocr_types import SUPPORTED_ENGINES, ENGINE_LABELS
        
        class PlatformSettingsForm(FlaskForm):
            unregistered_user_ocr_limit = IntegerField(
                "Daily OCR Credit Limit (Unregistered Users)",
                validators=[wtform_Optional(), NumberRange(min=0)],
                description="Daily limit per guest user (IP/fingerprint)."
            )
            unregistered_user_project_limit = IntegerField(
                "Daily Project Limit (Unregistered Users)",
                validators=[wtform_Optional(), NumberRange(min=0)],
                description="Daily project creation limit per guest user (IP/fingerprint)."
            )
            unregistered_user_upload_limit = IntegerField(
                "Guest Upload Limit (MB)",
                validators=[wtform_Optional(), NumberRange(min=1)],
                description="Max PDF upload size in MB for guest users."
            )
            default_ocr_engine = SelectField(
                "Default OCR Engine",
                choices=[],
                default="tesseract",
                description="The OCR engine that registered (non-super-admin) and unregistered users will use."
            )
            recommended_ocr_engine = SelectField(
                "Recommended OCR Engine",
                choices=[],
                default="",
                description="The recommended OCR engine displayed with a star icon for users."
            )
            auto_cleanup_days = SelectField(
                "Source File Retention Period",
                coerce=int,
                choices=[
                    (1, "1 Day"),
                    (3, "3 Days"),
                    (7, "7 Days (Default)"),
                    (14, "14 Days"),
                    (30, "30 Days"),
                    (60, "60 Days"),
                    (90, "90 Days"),
                ],
                default=7,
                description="Number of days before uploaded source PDF/DOC files are automatically cleaned up.",
            )
            
        form = PlatformSettingsForm()
        
        from kalanjiyam.utils.ocr_client import get_available_engines
        ocr_ping = get_available_engines()
        active_engines = ocr_ping.get("engines", [])
        
        choices_set = {eng for eng in active_engines if eng in SUPPORTED_ENGINES and eng != "google"}
        choices_set.add("tesseract")
        if system_settings.default_ocr_engine and system_settings.default_ocr_engine != "google":
            choices_set.add(system_settings.default_ocr_engine)
            
        sorted_engines = [eng for eng in SUPPORTED_ENGINES if eng in choices_set]
        form.default_ocr_engine.choices = [(eng, ENGINE_LABELS.get(eng, eng.capitalize())) for eng in sorted_engines]
        
        # Populate recommended engine choices
        rec_choices_set = set(choices_set)
        if system_settings.recommended_ocr_engine and system_settings.recommended_ocr_engine != "google":
            rec_choices_set.add(system_settings.recommended_ocr_engine)
        sorted_rec_engines = [eng for eng in SUPPORTED_ENGINES if eng in rec_choices_set]
        rec_choices = [("", "None (No recommended engine)")] + [(eng, ENGINE_LABELS.get(eng, eng.capitalize())) for eng in sorted_rec_engines]
        form.recommended_ocr_engine.choices = rec_choices
        
        cleanup_enabled = current_app.config.get("AUTO_UPLOADED_FILES_CLEANUP", False)

        if form.validate_on_submit():
            system_settings.unregistered_user_ocr_limit = form.unregistered_user_ocr_limit.data if form.unregistered_user_ocr_limit.data is not None else 10
            system_settings.unregistered_user_project_limit = form.unregistered_user_project_limit.data if form.unregistered_user_project_limit.data is not None else 5
            system_settings.unregistered_user_upload_limit = form.unregistered_user_upload_limit.data if form.unregistered_user_upload_limit.data is not None else 10
            system_settings.default_ocr_engine = form.default_ocr_engine.data if form.default_ocr_engine.data else "tesseract"
            system_settings.recommended_ocr_engine = form.recommended_ocr_engine.data if form.recommended_ocr_engine.data else None
            system_settings.auto_cleanup_days = form.auto_cleanup_days.data if form.auto_cleanup_days.data is not None else 7
            
            session.add(system_settings)
            session.commit()
            flash("Platform settings saved successfully.", "success")
            return redirect(url_for(".settings"))
            
        if request.method == "GET":
            form.unregistered_user_ocr_limit.data = system_settings.unregistered_user_ocr_limit
            form.unregistered_user_project_limit.data = system_settings.unregistered_user_project_limit
            form.unregistered_user_upload_limit.data = getattr(system_settings, "unregistered_user_upload_limit", 10)
            form.default_ocr_engine.data = system_settings.default_ocr_engine
            form.recommended_ocr_engine.data = system_settings.recommended_ocr_engine or ""
            form.auto_cleanup_days.data = getattr(system_settings, "auto_cleanup_days", 7) or 7
            
        return render_template("admin/platform_settings.html", form=form, cleanup_enabled=cleanup_enabled)

    @expose("/metrics")
    def metrics(self):
        require_platform_super_admin()

        from kalanjiyam.utils.metrics import (
            get_active_celery_queues,
            get_latency_metrics_summary,
            get_error_logs_paginated,
        )

        queues_data = get_active_celery_queues()
        latencies_data = get_latency_metrics_summary(days=7)
        error_logs_data = get_error_logs_paginated(page=1, per_page=20)
        groups = q.groups()
        session = q.get_session()
        all_users = session.query(db.User).all()

        return render_template(
            "admin/platform_metrics.html",
            queues_data=queues_data,
            latencies_data=latencies_data,
            error_logs_data=error_logs_data,
            groups=groups,
            all_users=all_users,
            csrf_token=generate_csrf(),
        )

    @expose("/metrics/api")
    def metrics_api(self):
        require_platform_super_admin()

        from kalanjiyam.utils.metrics import (
            get_active_celery_queues,
            get_latency_metrics_summary,
            get_error_logs_paginated,
        )

        tab = request.args.get("tab", "queues")
        if tab == "queues":
            data = get_active_celery_queues()
        elif tab == "latencies":
            days = request.args.get("days", 7, type=int)
            data = get_latency_metrics_summary(days=days)
        elif tab == "errors":
            page = request.args.get("page", 1, type=int)
            per_page = request.args.get("per_page", 20, type=int)
            level = request.args.get("level")
            group_id = request.args.get("group_id", type=int)
            user_id = request.args.get("user_id", type=int)
            search = request.args.get("search")
            data = get_error_logs_paginated(
                page=page,
                per_page=per_page,
                level=level,
                group_id=group_id,
                user_id=user_id,
                search=search,
            )
        else:
            data = {"error": "Invalid tab parameter"}

        return jsonify(data)

    @expose("/metrics/clear", methods=["POST"])
    def metrics_clear(self):
        require_platform_super_admin()
        session = q.get_session()
        category = request.form.get("category", "ALL")

        query = session.query(db.SystemMetricLog)
        if category != "ALL":
            query = query.filter(db.SystemMetricLog.category == category)

        deleted_count = query.delete(synchronize_session=False)
        session.commit()

        flash(f"Cleared {deleted_count} metric logs.", "success")
        return redirect(url_for(".metrics"))

    @expose("/reported-issues")
    def reported_issues(self):
        require_platform_super_admin()
        session = q.get_session()
        issues = session.query(db.ReportedIssue).order_by(db.ReportedIssue.created_at.desc()).all()
        return render_template(
            "admin/reported_issues.html",
            issues=issues,
            csrf_token=generate_csrf(),
        )

    @expose("/reported-issues/update-status", methods=["POST"])
    def update_issue_status(self):
        require_platform_super_admin()
        issue_id = request.form.get("issue_id", type=int)
        status = request.form.get("status", "").strip()

        valid_statuses = ["pending", "resolved", "not_applicable"]
        if issue_id and status in valid_statuses:
            session = q.get_session()
            issue = session.query(db.ReportedIssue).filter_by(id=issue_id).first()
            if issue:
                issue.status = status
                session.commit()
                flash("Issue status updated successfully.", "success")
            else:
                flash("Issue not found.", "error")
        else:
            flash("Invalid status or issue ID.", "error")

        return redirect(url_for(".reported_issues"))

    @expose("/search_index", methods=["GET", "POST"])
    def search_index(self):
        """Platform-wide search index management."""
        require_platform_super_admin()
        session = q.get_session()
        from kalanjiyam.search import admin_ops

        org_ids = admin_ops.indexable_org_ids(session)
        if request.method == "POST":
            _handle_search_index_action(session, org_ids=org_ids, allow_all=True)
            return redirect(url_for(".search_index"))

        return _render_search_index(
            session, org_ids=org_ids, is_org_admin=False, org_id=None
        )

    @expose("/search_index/status")
    def search_index_status(self):
        """Job progress and index stats, polled by the dashboard."""
        require_platform_super_admin()
        session = q.get_session()
        from kalanjiyam.search import admin_ops

        org_ids = admin_ops.indexable_org_ids(session)
        return jsonify(_search_index_status_payload(session, org_ids, org_id=None))


def _search_index_status_payload(session, org_ids, *, org_id):
    from kalanjiyam.search import admin_ops

    state = admin_ops.dashboard_state(session, org_ids)
    jobs = admin_ops.recent_jobs(session, org_id=org_id, limit=20)
    return {
        "health": state["health"],
        "orgs": state["orgs"],
        "total_pages": state["total_pages"],
        "total_projects": state["total_projects"],
        "total_size_bytes": state["total_size_bytes"],
        "ungrouped_projects": state["ungrouped_projects"],
        "jobs": [admin_ops.job_summary(j) for j in jobs],
    }


def _render_search_index(session, *, org_ids, is_org_admin, org_id):
    from kalanjiyam.search import admin_ops

    state = admin_ops.dashboard_state(session, org_ids)
    return render_template(
        "admin/search_index.html",
        state=state,
        jobs=admin_ops.recent_jobs(session, org_id=org_id, limit=20),
        projects=admin_ops.projects_for_picker(session, org_ids),
        is_org_admin=is_org_admin,
        csrf_token=generate_csrf(),
    )


def _handle_search_index_action(session, *, org_ids, allow_all, forced_org_id=None):
    """Run a dashboard action.

    ``org_ids`` is the caller's authorized scope, derived from their identity.
    Any organization or project named in the form is checked against it, so a
    forged form value cannot reach another tenant's index.
    """
    from kalanjiyam.models.search import JOB_DROP, JOB_REBUILD, JOB_SYNC
    from kalanjiyam.search import admin_ops

    action = request.form.get("action", "")
    requested_by_id = current_user.id if current_user.is_authenticated else None

    def resolve_org():
        if forced_org_id is not None:
            return forced_org_id
        raw = request.form.get("org_id", type=int)
        if raw is None:
            return None
        if raw not in org_ids:
            raise admin_ops.ActionError("You cannot manage that organization's index.")
        return raw

    try:
        if action == "create_indices":
            count = admin_ops.ensure_indices(session, org_ids)
            flash(f"Search indices are ready for {count} organization(s).", "success")

        elif action == "rebuild":
            org_id = resolve_org()
            if org_id is None and not allow_all:
                raise admin_ops.ActionError("Choose an organization to rebuild.")
            job = admin_ops.start_job(
                session,
                job_type=JOB_REBUILD,
                org_id=org_id,
                requested_by_id=requested_by_id,
            )
            flash(f"Started rebuild as job #{job.id}.", "success")

        elif action == "reindex_project":
            project_id = request.form.get("project_id", type=int)
            if not project_id:
                raise admin_ops.ActionError("Choose a book to reindex.")
            if not admin_ops.project_is_in_orgs(session, project_id, org_ids):
                raise admin_ops.ActionError("You cannot reindex that book.")
            job = admin_ops.start_job(
                session,
                job_type=JOB_REBUILD,
                project_id=project_id,
                requested_by_id=requested_by_id,
            )
            flash(f"Started reindex as job #{job.id}.", "success")

        elif action == "sync":
            org_id = resolve_org()
            if org_id is None and not allow_all:
                raise admin_ops.ActionError("Choose an organization to sync.")
            job = admin_ops.start_job(
                session,
                job_type=JOB_SYNC,
                org_id=org_id,
                requested_by_id=requested_by_id,
            )
            flash(f"Started sync as job #{job.id}.", "success")

        elif action == "drop":
            org_id = resolve_org()
            if org_id is None:
                raise admin_ops.ActionError("Choose an organization to drop.")
            group = q.group(org_id)
            typed = (request.form.get("confirm") or "").strip()
            if not group or typed != group.slug:
                raise admin_ops.ActionError(
                    "Type the organization's slug exactly to confirm dropping its index."
                )
            job = admin_ops.start_job(
                session,
                job_type=JOB_DROP,
                org_id=org_id,
                requested_by_id=requested_by_id,
            )
            flash(f"Dropping the index as job #{job.id}.", "warning")

        elif action == "cancel":
            job_id = request.form.get("job_id", type=int)
            from kalanjiyam.models.search import SearchIndexJob

            job = session.query(SearchIndexJob).get(job_id) if job_id else None
            if job is None:
                raise admin_ops.ActionError("That job no longer exists.")
            if job.scope_org_id is not None and job.scope_org_id not in org_ids:
                raise admin_ops.ActionError("You cannot cancel that job.")
            if job.scope_org_id is None and not allow_all:
                raise admin_ops.ActionError("You cannot cancel that job.")
            if admin_ops.cancel_job(session, job_id):
                flash(f"Asked job #{job_id} to stop.", "success")
            else:
                flash(f"Job #{job_id} had already finished.", "error")

        else:
            flash("Unknown action.", "error")

    except admin_ops.ActionError as e:
        flash(str(e), "error")


# Archival metadata extraction metrics
# ------------------------------------
#
# The OCR/translation dashboard is organised by batch job because that is the
# unit those pipelines run in. Extraction has no jobs: it runs per project, one
# run at a time, so the unit here is the run and the list is a list of documents.
# Everything else -- filters, summary cards, CSV export, org scoping -- follows
# the same shape, because it answers the same question about a different pipeline.


def _org_project_ids(session, org_id: int) -> list[int]:
    """Project ids belonging to one organization."""
    from kalanjiyam.models.group import ProjectGroups

    return [
        pg.project_id
        for pg in session.query(ProjectGroups.project_id)
        .filter_by(group_id=org_id)
        .all()
    ]


def _parse_filter_date(val, is_end: bool = False) -> datetime | None:
    """Parse a date string or date/datetime object for filtering runs by created_at."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, time.max if is_end else time.min)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            d = date.fromisoformat(val)
            return datetime.combine(d, time.max if is_end else time.min)
        except (ValueError, TypeError):
            pass
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            pass
    return None


def _format_date_str(val) -> str:
    """Format a date/datetime or string to YYYY-MM-DD for form rendering."""
    if not val:
        return ""
    if isinstance(val, str):
        val = val.strip()
        try:
            return date.fromisoformat(val).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return val
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    return ""


def _metadata_runs(
    session,
    project_ids=None,
    status="all",
    search="",
    start_date=None,
    end_date=None,
):
    """Extraction runs, newest first, joined to the project they describe.

    `project_ids=None` means every project; an empty list means none, which is
    not the same thing and must not silently widen to everything.
    """
    from sqlalchemy import or_

    from kalanjiyam.models.archival import MetadataExtractionRun as Run
    from kalanjiyam.models.proofing import Project

    query = session.query(Run, Project).outerjoin(
        Project, Run.project_id == Project.id
    )

    if project_ids is not None:
        if not project_ids:
            return []
        query = query.filter(Run.project_id.in_(project_ids))

    if status == "completed":
        query = query.filter(Run.status == "COMPLETED")
    elif status == "partial":
        query = query.filter(Run.status == "PARTIAL")
    elif status == "failed":
        query = query.filter(Run.status == "FAILED")
    elif status == "running":
        query = query.filter(Run.status.in_(["PENDING", "IN_PROGRESS"]))

    if search:
        query = query.filter(
            or_(
                Project.slug.ilike(f"%{search}%"),
                Project.display_title.ilike(f"%{search}%"),
            )
        )

    start_dt = _parse_filter_date(start_date, is_end=False)
    if start_dt is not None:
        query = query.filter(Run.created_at >= start_dt)

    end_dt = _parse_filter_date(end_date, is_end=True)
    if end_dt is not None:
        query = query.filter(Run.created_at <= end_dt)

    return query.order_by(Run.id.desc()).all()


def _metadata_metrics_payload(
    session,
    *,
    project_ids=None,
    is_org_admin=False,
    org=None,
    status="all",
    search="",
    start_date="",
    end_date="",
):
    """Rows and totals for the extraction dashboard."""
    rows = _metadata_runs(
        session,
        project_ids,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )

    def _mean(values):
        values = [v for v in values if v is not None]
        return (sum(values) / len(values)) if values else None

    # Token totals skip runs that reported no usage rather than counting them as
    # zero -- the same rule the CSV and the per-run panel follow.
    tokens_in = [r.total_prompt_tokens for r, _ in rows if r.total_prompt_tokens]
    tokens_out = [r.total_completion_tokens for r, _ in rows if r.total_completion_tokens]
    durations = [r.duration_sec for r, _ in rows if r.duration_sec is not None]
    engine_latencies = [
        r.total_engine_latency_sec
        for r, _ in rows
        if r.total_engine_latency_sec is not None
    ]
    window_times = [
        r.avg_time_per_window_sec
        for r, _ in rows
        if r.avg_time_per_window_sec is not None
    ]
    engine_latencies_per_window = [
        r.avg_engine_latency_sec
        for r, _ in rows
        if r.avg_engine_latency_sec is not None
    ]

    return {
        "runs": [
            {
                "run": run,
                "project": project,
                "slug": project.slug if project else None,
                "title": (
                    project.display_title or project.slug if project else _("(deleted)")
                ),
            }
            for run, project in rows
        ],
        "summary": {
            "runs": len(rows),
            "documents": len({r.project_id for r, _ in rows}),
            "pages_read": sum(r.pages_read or 0 for r, _ in rows),
            "pages_total": sum(r.pages_total or 0 for r, _ in rows),
            "failed": sum(1 for r, _ in rows if r.status == "FAILED"),
            "partial": sum(1 for r, _ in rows if r.status == "PARTIAL"),
            "tokens_in": sum(tokens_in) if tokens_in else None,
            "tokens_out": sum(tokens_out) if tokens_out else None,
            "avg_evidence_verified": _mean(
                [r.evidence_verified_rate for r, _ in rows]
            ),
            "avg_field_confidence": _mean([r.avg_field_confidence for r, _ in rows]),
            "avg_time_taken": _mean(durations),
            "total_time_taken": sum(durations) if durations else None,
            "avg_engine_latency": _mean(engine_latencies),
            "avg_time_per_window": _mean(window_times),
            "avg_engine_latency_per_window": _mean(engine_latencies_per_window),
        },
        "current_status": status,
        "search_query": search,
        "start_date": _format_date_str(start_date),
        "end_date": _format_date_str(end_date),
        "is_org_admin": is_org_admin,
        "org": org,
    }


def _metadata_metrics_csv_response(
    session,
    project_ids=None,
    status="all",
    search="",
    start_date=None,
    end_date=None,
):
    """Per-document extraction metrics, one row per run.

    Deliberately its own export rather than extra columns on the batch-OCR CSV:
    an extraction run belongs to a project, while a `BatchItem` belongs to a job,
    so there is no honest single row for both.

    Every confidence column can legitimately be blank. Three of the OCR engines
    in service produce no confidence signal, so a document read with one has
    nothing to average -- which is why "Pages w/o Confidence" sits beside the
    average rather than the average being quietly reported as 0.
    """
    import csv
    import io

    from flask import Response

    rows = _metadata_runs(
        session,
        project_ids,
        status=status,
        search=search,
        start_date=start_date,
        end_date=end_date,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Run ID",
        "Project",
        "Status",
        "Engine",
        "Model",
        "Model Version",
        "Taxonomy Version",
        "Windows",
        "Windows Failed",
        "Pages Read",
        "Pages Total",
        "Extraction Coverage (%)",
        "Fields Filled",
        "Fields Total",
        "Field Coverage (%)",
        "Avg Field Conf (%)",
        "Min Field Conf (%)",
        "Fields <0.7",
        "Evidence Spans",
        "Evidence Verified",
        "Evidence Verified (%)",
        "Avg Source OCR Conf (%)",
        "Pages w/o Confidence",
        "Prompt Tokens (In)",
        "Completion Tokens (Out)",
        "Total Tokens",
        "Tokens / Window",
        "Tokens / Page Read",
        "Time Taken (Sec)",
        "Avg Time / Window (Sec)",
        "Total Engine Latency (Sec)",
        "Avg Engine Latency (Sec)",
        "Metadata Size (Bytes)",
        "Created At",
        "Completed At",
        "Error Message",
    ])

    def pct(value):
        # "" rather than 0 for a missing score: the two mean different things
        # and a spreadsheet average must not conflate them.
        return round(value * 100, 1) if value is not None else ""

    def num(value):
        return value if value is not None else ""

    for run, project in rows:
        windows = run.windows_completed or 0
        total_latency = run.total_engine_latency_ms
        latency_sec = (total_latency / 1000.0) if total_latency is not None else None
        duration_sec = run.duration_sec
        writer.writerow([
            run.id,
            project.slug if project else "",
            run.status,
            run.engine or "",
            run.model_name or "",
            run.model_version or "",
            run.taxonomy_version or "",
            num(run.windows_total),
            num(run.windows_failed),
            num(run.pages_read),
            num(run.pages_total),
            pct(run.extraction_coverage),
            num(run.fields_filled),
            num(run.fields_total),
            pct(run.field_coverage),
            pct(run.avg_field_confidence),
            pct(run.min_field_confidence),
            num(run.low_conf_field_count),
            num(run.evidence_spans),
            num(run.evidence_verified),
            pct(run.evidence_verified_rate),
            pct(run.avg_source_ocr_confidence),
            num(run.pages_without_confidence),
            # "" rather than 0 when the service reported no usage at all -- a run
            # that cost nothing and a run that did not say must not average
            # together.
            num(run.total_prompt_tokens),
            num(run.total_completion_tokens),
            num(run.total_tokens),
            round(run.tokens_per_window, 1) if run.tokens_per_window is not None else "",
            # An average over an uneven divisor -- see `tokens_per_page`.
            round(run.tokens_per_page, 1) if run.tokens_per_page is not None else "",
            round(duration_sec, 2) if duration_sec is not None else "",
            round(duration_sec / windows, 2)
            if (duration_sec is not None and windows > 0)
            else "",
            round(latency_sec, 2) if latency_sec is not None else "",
            round(latency_sec / windows, 2)
            if (latency_sec is not None and windows > 0)
            else "",
            run.metadata_data_size_bytes or 0,
            run.created_at,
            run.completed_at or "",
            run.error_message or "",
        ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=metadata_extraction_metrics.csv"
        },
    )


class GroupsView(AdminBaseView):
    """Super-admin group management: list/create/edit/delete groups, manage users and books."""

    def is_accessible(self):
        return is_platform_super_admin()

    def inaccessible_callback(self, name, **kwargs):
        return platform_admin_inaccessible()

    @expose("/")
    def index(self):
        require_platform_super_admin()
        page = request.args.get("page", 1, type=int)
        if page < 1:
            page = 1
        per_page = 20
        groups_list, total = q.groups_paginated(page=page, per_page=per_page)
        num_pages = (total + per_page - 1) // per_page if total else 1
        return render_template(
            "admin/groups_list.html",
            groups=groups_list,
            page=page,
            per_page=per_page,
            total=total,
            num_pages=num_pages,
            csrf_token=generate_csrf(),
        )

    @expose("/create", methods=["GET", "POST"])
    def create(self):
        require_platform_super_admin()
        all_users = q.all_users_for_group_select()
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            slug = (request.form.get("slug") or slugify(name)).strip()
            storage_quota_mb = request.form.get("storage_quota_mb", type=int)
            ocr_credit_limit = request.form.get("ocr_credit_limit", type=int)
            translation_credit_limit = request.form.get("translation_credit_limit", type=int)
            admin_user_id = request.form.get("admin_user_id", type=int)
            if not name:
                flash("Name is required.", "error")
                return render_template("admin/group_form.html", group=None, all_users=all_users, csrf_token=generate_csrf())
            if storage_quota_mb is not None and storage_quota_mb < 0:
                flash("Storage quota cannot be negative.", "error")
                return render_template("admin/group_form.html", group=None, all_users=all_users, csrf_token=generate_csrf())
            if ocr_credit_limit is not None and ocr_credit_limit < 0:
                flash("OCR credit limit cannot be negative.", "error")
                return render_template("admin/group_form.html", group=None, all_users=all_users, csrf_token=generate_csrf())
            if translation_credit_limit is not None and translation_credit_limit < 0:
                flash("Translation credit limit cannot be negative.", "error")
                return render_template("admin/group_form.html", group=None, all_users=all_users, csrf_token=generate_csrf())
            session = q.get_session()
            group = db.Group(
                name=name,
                slug=slug,
                description=description,
                storage_quota_bytes=(storage_quota_mb * 1024 * 1024) if storage_quota_mb else None,
                ocr_credit_limit=ocr_credit_limit,
                translation_credit_limit=translation_credit_limit,
                admin_user_id=admin_user_id,
            )
            session.add(group)
            session.flush()
            _promote_org_admin(session, group, admin_user_id)
            session.commit()
            flash("Group created.", "success")
            return redirect(url_for("groups_view.manage", id=group.id))
        return render_template("admin/group_form.html", group=None, all_users=all_users, csrf_token=generate_csrf())

    @expose("/edit/<int:id>", methods=["GET", "POST"])
    def edit(self, id):
        require_platform_super_admin()
        group = q.group(id)
        all_users = q.all_users_for_group_select()
        if not group:
            abort(404)
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            slug = (request.form.get("slug") or slugify(name)).strip()
            storage_quota_mb = request.form.get("storage_quota_mb", type=int)
            ocr_credit_limit = request.form.get("ocr_credit_limit", type=int)
            translation_credit_limit = request.form.get("translation_credit_limit", type=int)
            admin_user_id = request.form.get("admin_user_id", type=int)
            if not name:
                flash("Name is required.", "error")
                return render_template("admin/group_form.html", group=group, all_users=all_users, csrf_token=generate_csrf())
            if not storage_quota_mb is None and storage_quota_mb < 0:
                flash("Storage quota cannot be negative.", "error")
                return render_template("admin/group_form.html", group=group, all_users=all_users, csrf_token=generate_csrf())
            if not ocr_credit_limit is None and ocr_credit_limit < 0:
                flash("OCR credit limit cannot be negative.", "error")
                return render_template("admin/group_form.html", group=group, all_users=all_users, csrf_token=generate_csrf())   
            if not translation_credit_limit is None and translation_credit_limit < 0:
                flash("Translation credit limit cannot be negative.", "error")
                return render_template("admin/group_form.html", group=group, all_users=all_users, csrf_token=generate_csrf())   
            group.name = name
            group.slug = slug
            group.description = description
            group.storage_quota_bytes = (storage_quota_mb * 1024 * 1024) if storage_quota_mb else None
            group.ocr_credit_limit = ocr_credit_limit
            group.translation_credit_limit = translation_credit_limit
            group.admin_user_id = admin_user_id
            session = q.get_session()
            session.add(group)
            session.flush()
            _promote_org_admin(session, group, admin_user_id)
            session.commit()
            flash("Group updated.", "success")
            return redirect(url_for("groups_view.index"))
        return render_template("admin/group_form.html", group=group, all_users=all_users, csrf_token=generate_csrf())

    @expose("/delete/<int:id>", methods=["POST"])
    def delete(self, id):
        require_platform_super_admin()
        group = q.group(id)
        if not group:
            abort(404)
        session = q.get_session()
        session.delete(group)
        session.commit()
        flash("Group deleted.", "success")
        return redirect(url_for("groups_view.index"))

    @expose("/manage/<int:id>", methods=["GET", "POST"])
    def manage(self, id):
        require_platform_super_admin()
        group = q.group(id)
        if not group:
            abort(404)
        projects_page = request.args.get("projects_page", 1, type=int)
        if projects_page < 1:
            projects_page = 1
        projects_per_page = 20
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add_user":
                user_id = request.form.get("user_id", type=int)
                if user_id:
                    q.add_user_to_group(user_id=user_id, group_id=id)
                    flash("User added to group.", "success")
            elif action == "remove_user":
                user_id = request.form.get("user_id", type=int)
                if user_id:
                    q.remove_user_from_group(user_id=user_id, group_id=id)
                    flash("User removed from group.", "success")
            elif action == "add_project":
                project_id = request.form.get("project_id", type=int)
                if project_id:
                    q.add_project_to_group(project_id=project_id, group_id=id)
                    flash("Project added to group.", "success")
            elif action == "remove_project":
                project_id = request.form.get("project_id", type=int)
                if project_id:
                    q.remove_project_from_group(project_id=project_id, group_id=id)
                    flash("Project removed from group.", "success")
            elif action == "set_project_public":
                project_id = request.form.get("project_id", type=int)
                is_public = request.form.get("is_public") == "1"
                if project_id:
                    updated = q.set_project_publicly_viewable(
                        project_id=project_id, group_id=id, is_public=is_public
                    )
                    if updated is None:
                        flash("Book not found in this organization.", "error")
                    else:
                        label = "public on /books/" if is_public else "organization-only"
                        flash(f'"{updated.display_title}" is now {label}.', "success")
            elif action == "update_user_quotas":
                default_user_storage_mb = request.form.get("default_user_storage_mb")
                default_user_ocr_limit = request.form.get("default_user_ocr_limit")
                default_user_translation_limit = request.form.get("default_user_translation_limit")
                
                # Convert values to correct type/None
                default_user_storage_mb = int(default_user_storage_mb) if default_user_storage_mb else None
                default_user_ocr_limit = int(default_user_ocr_limit) if default_user_ocr_limit else None
                default_user_translation_limit = int(default_user_translation_limit) if default_user_translation_limit else None
                
                if default_user_storage_mb is not None and default_user_storage_mb < 0:
                    flash("Default per-user storage quota cannot be negative.", "error")
                elif default_user_ocr_limit is not None and default_user_ocr_limit < 0:
                    flash("Default per-user OCR credit limit cannot be negative.", "error")
                elif default_user_translation_limit is not None and default_user_translation_limit < 0:
                    flash("Default per-user Translation credit limit cannot be negative.", "error")
                else:
                    group.default_user_storage_limit = (default_user_storage_mb * 1024 * 1024) if default_user_storage_mb is not None else None
                    group.default_user_ocr_limit = default_user_ocr_limit
                    group.default_user_translation_limit = default_user_translation_limit
                    session = q.get_session()
                    session.add(group)
                    session.commit()
                    flash("Per-user quotas updated.", "success")
            return redirect(
                url_for(
                    "groups_view.manage",
                    id=id,
                    projects_page=request.form.get("projects_page") or projects_page,
                )
            )
        users = q.users_in_group(id)
        projects_list, projects_total = q.projects_in_group(
            id, page=projects_page, per_page=projects_per_page
        )
        projects_num_pages = (
            (projects_total + projects_per_page - 1) // projects_per_page
            if projects_total
            else 1
        )
        all_projects = q.all_projects_for_group_select()
        all_users = q.all_users_for_group_select()
        projects_in_group_ids = {p.id for p in group.projects}
        users_in_group_ids = {u.id for u in users}
        return render_template(
            "admin/group_manage.html",
            group=group,
            users=users,
            projects=projects_list,
            projects_total=projects_total,
            projects_page=projects_page,
            projects_per_page=projects_per_page,
            projects_num_pages=projects_num_pages,
            all_projects=all_projects,
            all_users=all_users,
            projects_in_group_ids=projects_in_group_ids,
            users_in_group_ids=users_in_group_ids,
            csrf_token=generate_csrf(),
        )


class OrgAdminView(AdminBaseView):
    """Organization-scoped admin dashboard."""

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_org_admin

    def inaccessible_callback(self, name, **kwargs):
        abort(404)

    @expose("/", methods=["GET", "POST"])
    def index(self):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)

        if request.method == "POST":
            action = request.form.get("action")
            session = q.get_session()
            if action == "create_user":
                username = (request.form.get("username") or "").strip()
                email = (request.form.get("email") or "").strip()
                password = (request.form.get("password") or "").strip()
                if not username or not email or not password:
                    flash("Username, email, and password are required.", "error")
                else:
                    user = db.User(username=username, email=email, organization_id=org.id)
                    user.set_password(password)
                    p1_role = session.query(db.Role).filter_by(name=db.SiteRole.P1.value).first()
                    if p1_role:
                        user.roles.append(p1_role)
                    session.add(user)
                    session.flush()
                    session.add(db.UserGroups(user_id=user.id, group_id=org.id))
                    session.commit()
                    flash("User created.", "success")
            elif action == "add_user":
                user_id = request.form.get("user_id", type=int)
                if user_id:
                    q.add_user_to_group(user_id=user_id, group_id=org.id)
                    flash("User added to organization.", "success")
            elif action == "remove_user":
                user_id = request.form.get("user_id", type=int)
                if user_id and user_id != org.admin_user_id:
                    q.remove_user_from_group(user_id=user_id, group_id=org.id)
                    flash("User removed from organization.", "success")
            elif action == "change_password":
                user_id = request.form.get("user_id", type=int)
                new_password = (request.form.get("new_password") or "").strip()
                if not user_id or not new_password:
                    flash("User and password are required.", "error")
                else:
                    user = session.query(db.User).filter_by(id=user_id).first()
                    in_org = session.query(db.UserGroups).filter_by(user_id=user_id, group_id=org.id).first()
                    if user and in_org:
                        user.set_password(new_password)
                        session.add(user)
                        session.commit()
                        flash(f'Password updated for "{user.username}".', "success")
                    else:
                        flash("User not found in this organization.", "error")
            elif action == "change_role":
                user_id = request.form.get("user_id", type=int)
                role_name = (request.form.get("role_name") or "").strip()
                allowed_roles = {db.SiteRole.P1.value, db.SiteRole.P2.value, db.SiteRole.MODERATOR.value}
                if not user_id or role_name not in allowed_roles:
                    flash("Invalid role.", "error")
                else:
                    user = session.query(db.User).filter_by(id=user_id).first()
                    in_org = session.query(db.UserGroups).filter_by(user_id=user_id, group_id=org.id).first()
                    if user and in_org:
                        new_role = session.query(db.Role).filter_by(name=role_name).first()
                        if new_role:
                            user.roles = [r for r in user.roles if r.name not in allowed_roles]
                            user.roles.append(new_role)
                            session.add(user)
                            session.commit()
                            flash(f'Role updated for "{user.username}".', "success")
                    else:
                        flash("User not found in this organization.", "error")
            elif action == "add_project":
                project_id = request.form.get("project_id", type=int)
                if project_id:
                    q.add_project_to_group(project_id=project_id, group_id=org.id)
                    flash("Book added to organization.", "success")
            elif action == "remove_project":
                project_id = request.form.get("project_id", type=int)
                if project_id:
                    q.remove_project_from_group(project_id=project_id, group_id=org.id)
                    flash("Book removed from organization.", "success")
            elif action == "set_project_public":
                project_id = request.form.get("project_id", type=int)
                is_public = request.form.get("is_public") == "1"
                if project_id:
                    updated = q.set_project_publicly_viewable(
                        project_id=project_id, group_id=org.id, is_public=is_public
                    )
                    if updated is None:
                        flash("Book not found in this organization.", "error")
                    else:
                        label = "public on /books/" if is_public else "organization-only"
                        flash(f'"{updated.display_title}" is now {label}.', "success")
            return redirect(url_for("org_admin_view.index"))

        users = q.users_in_group(org.id)
        projects, _ = q.projects_in_group(org.id, page=1, per_page=200)
        all_users = q.all_users_for_group_select()
        all_projects = q.all_projects_for_group_select()
        users_in_group_ids = {u.id for u in users}
        projects_in_group_ids = {p.id for p in projects}
        return render_template(
            "admin/org_dashboard.html",
            org=org,
            users=users,
            projects=projects,
            all_users=all_users,
            all_projects=all_projects,
            users_in_group_ids=users_in_group_ids,
            projects_in_group_ids=projects_in_group_ids,
            csrf_token=generate_csrf(),
        )

    @expose("/analytics")
    def user_analytics(self):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)
            
        session = q.get_session()
        users = q.users_in_group(org.id)
        user_ids = [u.id for u in users]
        
        revisions_count = 0
        if user_ids:
            revisions_count = session.query(db.Revision).filter(db.Revision.author_id.in_(user_ids)).count()
            
        projects, _ = q.projects_in_group(org.id, page=1, per_page=1000)
        
        org_stat = {
            "org": org,
            "users_count": len(users),
            "projects_count": len(projects),
            "revisions_count": revisions_count,
            "ocr_count": org.ocr_credits_used or 0,
            "translation_count": org.translation_credits_used or 0,
            "storage_used": org.storage_used_bytes or 0,
        }
        
        return render_template(
            "admin/org_analytics.html",
            org_stat=org_stat,
            is_platform=False
        )

    @expose("/analytics/users")
    def org_user_analytics(self):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)
            
        session = q.get_session()
        users = q.users_in_group(org.id)
        
        user_stats = []
        for user in users:
            projects_count = session.query(db.Project).filter_by(creator_id=user.id).count()
            revisions_count = session.query(db.Revision).filter_by(author_id=user.id).count()
            ocr_count = session.query(db.UsageLog).filter_by(user_id=user.id, action="run_ocr").count()
            translation_count = user.translation_credits_used or 0
            user_stats.append({
                "user": user,
                "projects_count": projects_count,
                "revisions_count": revisions_count,
                "ocr_count": ocr_count,
                "translation_count": translation_count,
            })
            
        return render_template(
            "admin/org_user_analytics.html",
            org=org,
            user_stats=user_stats,
            is_platform=False
        )

    @expose("/search_index", methods=["GET", "POST"])
    def search_index(self):
        """Search index management, scoped to the admin's own organization."""
        # This return value is the scope. The org is never read from the form.
        org_id = require_org_admin()
        session = q.get_session()
        org_ids = [org_id]

        if request.method == "POST":
            _handle_search_index_action(
                session, org_ids=org_ids, allow_all=False, forced_org_id=org_id
            )
            return redirect(url_for(".search_index"))

        return _render_search_index(
            session, org_ids=org_ids, is_org_admin=True, org_id=org_id
        )

    @expose("/search_index/status")
    def search_index_status(self):
        org_id = require_org_admin()
        session = q.get_session()
        return jsonify(_search_index_status_payload(session, [org_id], org_id=org_id))

    @expose("/metadata_metrics")
    def metadata_metrics(self):
        """Extraction runs for this organization's documents only.

        Scoped through `ProjectGroups`, the same join the batch dashboard uses.
        An org with no projects gets an empty list rather than everyone's runs --
        `_metadata_runs` distinguishes "no projects" from "all projects".
        """
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)

        session = q.get_session()
        return render_template(
            "admin/metadata_metrics.html",
            **_metadata_metrics_payload(
                session,
                project_ids=_org_project_ids(session, org.id),
                is_org_admin=True,
                org=org,
                status=request.args.get("status", "all"),
                search=request.args.get("q", "").strip(),
                start_date=request.args.get("start_date", "").strip(),
                end_date=request.args.get("end_date", "").strip(),
            ),
        )

    @expose("/metadata_metrics/export_csv")
    def metadata_metrics_export_csv(self):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)
        session = q.get_session()
        return _metadata_metrics_csv_response(
            session,
            _org_project_ids(session, org.id),
            status=request.args.get("status", "all"),
            search=request.args.get("q", "").strip(),
            start_date=request.args.get("start_date", "").strip(),
            end_date=request.args.get("end_date", "").strip(),
        )

    @expose("/cli_batch_ocr")
    @expose("/cli_batch_ocr/<int:job_id>")
    def cli_batch_ocr(self, job_id=None):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)

        session = q.get_session()
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
        from kalanjiyam.models.group import ProjectGroups
        from sqlalchemy import func, or_

        # Get all project IDs belonging to this organization
        org_project_ids = [
            pg.project_id
            for pg in session.query(ProjectGroups.project_id).filter_by(group_id=org.id).all()
        ]

        if not org_project_ids:
            summary = _batch_ocr_summary_dict(0.0, 0, 0)
            return render_template(
                "admin/cli_batch_ocr.html",
                is_job_list=True,
                jobs=[],
                jobs_list=[],
                summary=summary,
                selected_job=None,
                item_metrics=[],
                is_org_admin=True,
                org=org,
                page=1,
                per_page=20,
                total=0,
                num_pages=1,
            )

        # Get all batch jobs that contain items associated with this org's projects
        job_ids_for_org = (
            session.query(BatchItem.job_id)
            .filter(BatchItem.project_id.in_(org_project_ids))
            .distinct()
            .all()
        )
        valid_job_ids = [j[0] for j in job_ids_for_org if j[0] is not None]

        # Page 1: Standalone Org Job List View
        if job_id is None:
            category = request.args.get('category', 'all')  # all, proofer, ui_batch, cli_batch
            task_type = request.args.get('task_type', 'all')  # all, ocr, translation
            search_query = request.args.get('q', '').strip()
            page = request.args.get('page', 1, type=int)
            per_page = request.args.get('per_page', 20, type=int)
            if page < 1:
                page = 1
            if per_page < 1 or per_page > 100:
                per_page = 20

            query = session.query(BatchJob).filter(BatchJob.id.in_(valid_job_ids)) if valid_job_ids else session.query(BatchJob).filter(False)

            # 1. Filter by Category
            if category == 'proofer':
                query = query.filter(BatchJob.job_type.in_(['SINGLE_PAGE_PROOFING_OCR', 'SINGLE_PAGE_PROOFING_TRANSLATION']))
            elif category == 'ui_batch':
                query = query.filter(BatchJob.job_type.in_(['UI_BATCH_OCR', 'UI_BATCH_TRANSLATION']))
            elif category == 'cli_batch':
                query = query.filter(BatchJob.job_type.in_(['BATCH_OCR', 'BATCH_OCR_JSONL', 'JSONL_IMPORT']))

            # 2. Filter by Task Type (OCR vs Translation)
            if task_type == 'ocr':
                query = query.filter(or_(
                    BatchJob.job_type.like('%OCR%'),
                    BatchJob.job_type == 'JSONL_IMPORT'
                ))
            elif task_type == 'translation':
                query = query.filter(BatchJob.job_type.like('%TRANSLATION%'))

            # 3. Filter by Search Query
            if search_query:
                query = query.filter(or_(
                    BatchJob.target_uri.ilike(f"%{search_query}%"),
                    BatchJob.job_type.ilike(f"%{search_query}%")
                ))

            # Database-level summary aggregation for this Org
            if valid_job_ids:
                summary_query = (
                    session.query(
                        func.coalesce(func.sum(BatchItem.total_ocr_latency_ms), 0.0),
                        func.coalesce(func.sum(BatchItem.extraction_latency_ms), 0.0),
                        func.coalesce(func.sum(BatchItem.total_pages), 0),
                        func.coalesce(func.sum(BatchItem.source_size_bytes), 0),
                    )
                    .join(BatchJob, BatchItem.job_id == BatchJob.id)
                    .filter(
                        BatchJob.id.in_(valid_job_ids),
                        BatchItem.project_id.in_(org_project_ids),
                    )
                )

                if category == 'proofer':
                    summary_query = summary_query.filter(BatchJob.job_type.in_(['SINGLE_PAGE_PROOFING_OCR', 'SINGLE_PAGE_PROOFING_TRANSLATION']))
                elif category == 'ui_batch':
                    summary_query = summary_query.filter(BatchJob.job_type.in_(['UI_BATCH_OCR', 'UI_BATCH_TRANSLATION']))
                elif category == 'cli_batch':
                    summary_query = summary_query.filter(BatchJob.job_type.in_(['BATCH_OCR', 'BATCH_OCR_JSONL', 'JSONL_IMPORT']))

                if task_type == 'ocr':
                    summary_query = summary_query.filter(or_(
                        BatchJob.job_type.like('%OCR%'),
                        BatchJob.job_type == 'JSONL_IMPORT'
                    ))
                elif task_type == 'translation':
                    summary_query = summary_query.filter(BatchJob.job_type.like('%TRANSLATION%'))

                if search_query:
                    summary_query = summary_query.filter(or_(
                        BatchJob.target_uri.ilike(f"%{search_query}%"),
                        BatchJob.job_type.ilike(f"%{search_query}%")
                    ))

                tot_ocr_ms, tot_extract_ms, tot_pages, tot_source_bytes = (
                    summary_query.first() or (0.0, 0.0, 0, 0)
                )
            else:
                tot_ocr_ms, tot_extract_ms, tot_pages, tot_source_bytes = (0.0, 0.0, 0, 0)

            summary = _batch_ocr_summary_dict(
                total_ocr_ms=float(tot_ocr_ms),
                total_pages=int(tot_pages),
                total_source_size_bytes=int(tot_source_bytes),
                total_extraction_ms=float(tot_extract_ms),
            )

            # Paginated Jobs list
            total_jobs = query.count() if valid_job_ids else 0
            num_pages = max(1, (total_jobs + per_page - 1) // per_page)
            if page > num_pages:
                page = num_pages

            jobs_on_page = (
                query.order_by(BatchJob.id.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all()
            ) if valid_job_ids else []

            for j in jobs_on_page:
                if j.status != 'COMPLETED':
                    _sync_job_and_item_metrics(session, j)

            job_ids = [j.id for j in jobs_on_page]
            stats_map = {}
            if job_ids:
                job_item_stats = (
                    session.query(
                        BatchItem.job_id,
                        func.count(BatchItem.id).label("item_count"),
                        func.coalesce(func.sum(BatchItem.total_pages), 0).label("total_pages"),
                    )
                    .filter(
                        BatchItem.job_id.in_(job_ids),
                        BatchItem.project_id.in_(org_project_ids),
                    )
                    .group_by(BatchItem.job_id)
                    .all()
                )
                stats_map = {row.job_id: (row.item_count, row.total_pages) for row in job_item_stats}

            jobs_list = []
            for j in jobs_on_page:
                item_count, job_pages = stats_map.get(j.id, (0, 0))
                jobs_list.append({
                    "id": j.id,
                    "target_uri": j.target_uri,
                    "job_type": j.job_type,
                    "status": j.status,
                    "created_at": j.created_at,
                    "completed_at": j.completed_at,
                    "item_count": item_count,
                    "total_pages": job_pages,
                })

            return render_template(
                "admin/cli_batch_ocr.html",
                is_job_list=True,
                jobs_list=jobs_list,
                summary=summary,
                current_category=category,
                current_task_type=task_type,
                search_query=search_query,
                is_org_admin=True,
                org=org,
                page=page,
                per_page=per_page,
                total=total_jobs,
                num_pages=num_pages,
            )

        # Page 2: Standalone Org Job Metrics Details View
        if job_id not in valid_job_ids:
            abort(403, description="Access denied to this batch job.")

        selected_job = session.query(BatchJob).get(job_id)
        if not selected_job:
            abort(404)

        if selected_job.status != 'COMPLETED':
            _sync_job_and_item_metrics(session, selected_job)

        job_summary_row = (
            session.query(
                func.coalesce(func.sum(BatchItem.total_ocr_latency_ms), 0.0),
                func.coalesce(func.sum(BatchItem.extraction_latency_ms), 0.0),
                func.coalesce(func.sum(BatchItem.total_pages), 0),
                func.coalesce(func.sum(BatchItem.source_size_bytes), 0),
            )
            .filter(
                BatchItem.job_id == selected_job.id,
                BatchItem.project_id.in_(org_project_ids),
            )
            .first()
        )
        job_total_ocr_ms, job_total_extraction_ms, job_total_pages, job_total_source_size = (
            job_summary_row or (0.0, 0.0, 0, 0)
        )

        summary = _batch_ocr_summary_dict(
            total_ocr_ms=float(job_total_ocr_ms),
            total_pages=int(job_total_pages),
            total_source_size_bytes=int(job_total_source_size),
            total_extraction_ms=float(job_total_extraction_ms),
        )

        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        if page < 1:
            page = 1
        if per_page < 1 or per_page > 100:
            per_page = 25

        items_query = session.query(BatchItem).filter(
            BatchItem.job_id == selected_job.id,
            BatchItem.project_id.in_(org_project_ids),
        )
        total_items = items_query.count()
        num_pages = max(1, (total_items + per_page - 1) // per_page)
        if page > num_pages:
            page = num_pages

        items = (
            items_query.order_by(BatchItem.id.asc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        item_ids = [item.id for item in items]
        pages_by_item = {item.id: [] for item in items}
        if item_ids:
            page_records = (
                session.query(BatchOcrPage)
                .filter(BatchOcrPage.batch_item_id.in_(item_ids))
                .order_by(BatchOcrPage.batch_item_id.asc(), BatchOcrPage.page_number.asc())
                .all()
            )
            for p_rec in page_records:
                pages_by_item[p_rec.batch_item_id].append(p_rec)

        item_metrics = []
        for item in items:
            p_recs = pages_by_item.get(item.id, [])
            pages = item.total_pages or len(p_recs)
            time_sec = (item.total_ocr_latency_ms / 1000.0) if item.total_ocr_latency_ms else None
            trans_time_sec = (item.total_translation_latency_ms / 1000.0) if item.total_translation_latency_ms else None
            avg_per_page_sec = (time_sec / pages) if (time_sec is not None and pages > 0) else None
            avg_trans_per_page_sec = (trans_time_sec / pages) if (trans_time_sec is not None and pages > 0) else None

            page_metrics_list = []
            for p_rec in p_recs:
                p_time_sec = (p_rec.ocr_latency_ms / 1000.0) if p_rec.ocr_latency_ms else None
                p_trans_time_sec = (p_rec.translation_latency_ms / 1000.0) if p_rec.translation_latency_ms else None
                p_eng_lat_sec = (p_rec.engine_latency_ms / 1000.0) if p_rec.engine_latency_ms else None
                page_metrics_list.append({
                    "id": p_rec.id,
                    "page_number": p_rec.page_number,
                    "status": p_rec.status,
                    "time_took_sec": round(p_time_sec, 2) if p_time_sec is not None else None,
                    "translation_time_took_sec": round(p_trans_time_sec, 2) if p_trans_time_sec is not None else None,
                    "extracted_image_size_bytes": p_rec.extracted_image_size_bytes,
                    "cropped_image_size_bytes": p_rec.cropped_image_size_bytes,
                    "ocr_data_size_bytes": p_rec.ocr_data_size_bytes,
                    "translation_data_size_bytes": p_rec.translation_data_size_bytes,
                    "source_lang": p_rec.source_lang,
                    "target_lang": p_rec.target_lang,
                    "engine": p_rec.engine,
                    "confidence": round(p_rec.confidence * 100, 1) if p_rec.confidence is not None else None,
                    "p05": round(p_rec.p05 * 100, 1) if p_rec.p05 is not None else None,
                    "blocks": p_rec.blocks,
                    "chars": p_rec.chars,
                    "engine_latency_sec": round(p_eng_lat_sec, 2) if p_eng_lat_sec is not None else None,
                    "attempt_count": p_rec.attempt_count,
                    "error_message": p_rec.error_message,
                })

            total_eng_lat_sec = (item.total_engine_latency_ms / 1000.0) if item.total_engine_latency_ms else None
            item_metrics.append({
                "id": item.id,
                "name": item.file_path,
                "size_bytes": item.source_size_bytes,
                "extracted_images_size_bytes": item.extracted_images_size_bytes,
                "cropped_images_size_bytes": item.cropped_images_size_bytes,
                "ocr_data_size_bytes": item.ocr_data_size_bytes,
                "translation_data_size_bytes": item.translation_data_size_bytes,
                "source_lang": item.source_lang,
                "target_lang": item.target_lang,
                "engine": item.engine,
                "avg_confidence": round(item.avg_confidence * 100, 1) if item.avg_confidence is not None else None,
                "min_confidence": round(item.min_confidence * 100, 1) if item.min_confidence is not None else None,
                "avg_p05": round(item.avg_p05 * 100, 1) if item.avg_p05 is not None else None,
                "low_conf_page_count": item.low_conf_page_count,
                "total_blocks": item.total_blocks,
                "total_chars": item.total_chars,
                "total_engine_latency_sec": round(total_eng_lat_sec, 2) if total_eng_lat_sec is not None else None,
                "avg_engine_latency_sec": round((total_eng_lat_sec / pages), 2) if (total_eng_lat_sec is not None and pages > 0) else None,
                "pages": pages,
                "time_took_sec": round(time_sec, 2) if time_sec is not None else None,
                "translation_time_took_sec": round(trans_time_sec, 2) if trans_time_sec is not None else None,
                "avg_per_page_sec": round(avg_per_page_sec, 2) if avg_per_page_sec is not None else None,
                "avg_trans_per_page_sec": round(avg_trans_per_page_sec, 2) if avg_trans_per_page_sec is not None else None,
                "status": item.status,
                "error_message": item.error_message,
                "extraction_latency_ms": item.extraction_latency_ms,
                "project_id": item.project_id,
                "page_metrics": page_metrics_list,
            })

        return render_template(
            "admin/cli_batch_ocr.html",
            is_job_list=False,
            selected_job=selected_job,
            item_metrics=item_metrics,
            summary=summary,
            is_org_admin=True,
            org=org,
            page=page,
            per_page=per_page,
            total=total_items,
            num_pages=num_pages,
        )

    @expose("/cli_batch_ocr/<int:job_id>/export_summary_csv")
    def cli_batch_ocr_export_summary_csv(self, job_id):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)

        session = q.get_session()
        import csv
        import io
        from flask import Response
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
        from kalanjiyam.models.group import ProjectGroups
        from sqlalchemy import func

        org_project_ids = [
            pg.project_id
            for pg in session.query(ProjectGroups.project_id).filter_by(group_id=org.id).all()
        ]

        job = session.query(BatchJob).get(job_id)
        if not job:
            abort(404)

        items = (
            session.query(BatchItem)
            .filter(
                BatchItem.job_id == job.id,
                BatchItem.project_id.in_(org_project_ids),
            )
            .all()
        )

        if not items:
            abort(403, description="No accessible items in this batch job.")

        item_ids = [i.id for i in items]
        page_counts_map = {}
        if item_ids:
            page_counts = (
                session.query(BatchOcrPage.batch_item_id, func.count(BatchOcrPage.id))
                .filter(BatchOcrPage.batch_item_id.in_(item_ids))
                .group_by(BatchOcrPage.batch_item_id)
                .all()
            )
            page_counts_map = {b_id: count for b_id, count in page_counts}

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Item ID",
            "Name / File Path",
            "Engine",
            "Pages",
            "Avg Confidence (%)",
            "Min Confidence (%)",
            "Pages <0.7",
            "Avg p05 (%)",
            "Total Blocks",
            "Total Chars",
            "Source Lang",
            "Target Lang",
            "Source Size (Bytes)",
            "Extracted Images Size (Bytes)",
            "Cropped Images Size (Bytes)",
            "OCR Data Size (Bytes)",
            "Translation Data Size (Bytes)",
            "OCR Time Took (Sec)",
            "Total Engine Latency (Sec)",
            "Avg Engine Latency (Sec)",
            "Translation Time Took (Sec)",
            "Avg Per Page OCR Time (Sec)",
            "Avg Per Page Translation Time (Sec)",
            "Status",
            "Extraction Latency (ms)",
            "Error Message",
        ])

        for item in items:
            pages = item.total_pages or page_counts_map.get(item.id, 0)
            time_sec = (item.total_ocr_latency_ms / 1000.0) if item.total_ocr_latency_ms else None
            eng_lat_sec = (item.total_engine_latency_ms / 1000.0) if item.total_engine_latency_ms else None
            avg_eng_lat_sec = (eng_lat_sec / pages) if (eng_lat_sec is not None and pages > 0) else None
            trans_time_sec = (item.total_translation_latency_ms / 1000.0) if item.total_translation_latency_ms else None
            avg_per_page_sec = (time_sec / pages) if (time_sec is not None and pages > 0) else None

            writer.writerow([
                item.id,
                item.file_path,
                item.engine or "",
                pages,
                round(item.avg_confidence * 100, 1) if item.avg_confidence is not None else "",
                round(item.min_confidence * 100, 1) if item.min_confidence is not None else "",
                item.low_conf_page_count if item.low_conf_page_count is not None else 0,
                round(item.avg_p05 * 100, 1) if item.avg_p05 is not None else "",
                item.total_blocks if item.total_blocks is not None else "",
                item.total_chars if item.total_chars is not None else "",
                item.source_lang or "",
                item.target_lang or "",
                item.source_size_bytes or 0,
                item.extracted_images_size_bytes or 0,
                item.cropped_images_size_bytes or 0,
                item.ocr_data_size_bytes or 0,
                item.translation_data_size_bytes or 0,
                round(time_sec, 2) if time_sec is not None else "",
                round(eng_lat_sec, 2) if eng_lat_sec is not None else "",
                round(avg_eng_lat_sec, 2) if avg_eng_lat_sec is not None else "",
                round(trans_time_sec, 2) if trans_time_sec is not None else "",
                round(avg_per_page_sec, 2) if avg_per_page_sec is not None else "",
                round(trans_time_sec / pages, 2) if (trans_time_sec is not None and pages > 0) else "",
                item.status,
                round(item.extraction_latency_ms, 2) if item.extraction_latency_ms else "",
                item.error_message or "",
            ])

        filename = f"batch_job_{job.id}_{org.slug}_document_summary.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @expose("/cli_batch_ocr/<int:job_id>/export_pages_csv")
    def cli_batch_ocr_export_pages_csv(self, job_id):
        org_id = require_org_admin()
        org = q.group(org_id)
        if org is None:
            abort(404)

        session = q.get_session()
        import csv
        import io
        from flask import Response
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
        from kalanjiyam.models.group import ProjectGroups

        org_project_ids = [
            pg.project_id
            for pg in session.query(ProjectGroups.project_id).filter_by(group_id=org.id).all()
        ]

        job = session.query(BatchJob).get(job_id)
        if not job:
            abort(404)

        items = (
            session.query(BatchItem)
            .filter(
                BatchItem.job_id == job.id,
                BatchItem.project_id.in_(org_project_ids),
            )
            .all()
        )
        item_ids = [i.id for i in items]

        if not item_ids:
            abort(403, description="No accessible items in this batch job.")

        p_records = (
            session.query(BatchOcrPage)
            .filter(BatchOcrPage.batch_item_id.in_(item_ids))
            .order_by(BatchOcrPage.batch_item_id.asc(), BatchOcrPage.page_number.asc())
            .all()
        )

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "Item ID",
            "Name / File Path",
            "Page Number",
            "Engine",
            "Confidence (%)",
            "p05 (%)",
            "Blocks",
            "Chars",
            "Source Lang",
            "Target Lang",
            "Extracted Image Size (Bytes)",
            "Cropped Images Size (Bytes)",
            "OCR Data Size (Bytes)",
            "Translation Data Size (Bytes)",
            "OCR Time Took (Sec)",
            "Engine Latency (Sec)",
            "Translation Time Took (Sec)",
            "Status",
            "Attempt Count",
            "Error Message",
        ])

        item_path_map = {i.id: i.file_path for i in items}

        for p in p_records:
            p_time = (p.ocr_latency_ms / 1000.0) if p.ocr_latency_ms else None
            p_eng_lat = (p.engine_latency_ms / 1000.0) if p.engine_latency_ms else None
            p_trans_time = (p.translation_latency_ms / 1000.0) if p.translation_latency_ms else None
            writer.writerow([
                p.batch_item_id,
                item_path_map.get(p.batch_item_id, ""),
                p.page_number,
                p.engine or "",
                round(p.confidence * 100, 1) if p.confidence is not None else "",
                round(p.p05 * 100, 1) if p.p05 is not None else "",
                p.blocks if p.blocks is not None else "",
                p.chars if p.chars is not None else "",
                p.source_lang or "",
                p.target_lang or "",
                p.extracted_image_size_bytes or 0,
                p.cropped_image_size_bytes or 0,
                p.ocr_data_size_bytes or 0,
                p.translation_data_size_bytes or 0,
                round(p_time, 2) if p_time is not None else "",
                round(p_eng_lat, 2) if p_eng_lat is not None else "",
                round(p_trans_time, 2) if p_trans_time is not None else "",
                p.status,
                p.attempt_count,
                p.error_message or "",
            ])

        filename = f"batch_job_{job.id}_{org.slug}_per_page_metrics.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


class ProjectSponsorshipView(sqla.ModelView):
    """View for ProjectSponsorship accessible to moderators and admins."""

    def is_accessible(self):
        return current_user.is_authenticated and (current_user.is_admin or current_user.is_moderator)

    def inaccessible_callback(self, name, **kw):
        abort(404)


class BaseView(sqla.ModelView):
    """Base view for models."""

    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_admin

    def inaccessible_callback(self, name, **kw):
        abort(404)


class UserView(BaseView):
    """Platform user CRUD for super admins. Super-admin accounts are CLI-only."""

    can_delete = True
    can_create = True
    can_edit = True
    list_template = "admin/user_list.html"
    create_template = "admin/user_form.html"
    edit_template = "admin/user_form.html"
    column_list = ["username", "email", "organization_id"]
    column_labels = {"organization_id": "Organization(s)"}
    column_formatters = {
        "organization_id": lambda v, c, m, p: (
            ", ".join(f"{g.name} ({g.slug})" for g in m.groups)
            if m.groups
            else (f"{m.organization.name} ({m.organization.slug})" if m.organization else "—")
        ),
        "email": lambda v, c, m, p: (
            f"{m.email}  [{', '.join(sorted(r.name for r in m.roles))}]"
            if m.roles
            else m.email
        ),
    }
    form_excluded_columns = [
        "password_hash",
        "description",
        "created_at",
        "is_deleted",
        "is_banned",
        "is_verified",
        "organization_id",
        "roles",
        "organization",
    ]
    form_columns = [
        "username",
        "email",
        "password",
        "role_ids",
        "organization_pick",
        "organization_ids",
    ]
    form_extra_fields = {
        "password": PasswordField(
            "Password",
            validators=[validators.Optional()],
            description="Required when creating a user. Leave blank on edit to keep the current password.",
        ),
        "organization_pick": SelectField(
            "Organization",
            coerce=int,
            choices=[],
            validators=[validators.Optional()],
            description="Assigned organization for standard users.",
        ),
        "organization_ids": SelectMultipleField(
            "Organizations (Multiple)",
            coerce=int,
            choices=[],
            validators=[validators.Optional()],
            description="Assigned organizations for Master Users (hold Ctrl/Cmd to select multiple).",
        ),
        "role_ids": SelectMultipleField("Roles", coerce=int, choices=[]),
    }
    form_base_class = SecureForm

    def get_query(self):
        return super().get_query().filter_by(is_deleted=False)

    def get_count_query(self):
        return super().get_count_query().filter_by(is_deleted=False)

    def _prepare_user_form(self, form, model=None):
        form.role_ids.choices = assignable_role_choices(self.session)
        form.organization_pick.choices = organization_choices()
        if hasattr(form, "organization_ids"):
            form.organization_ids.choices = organization_multi_choices()
        if model is not None and request.method != "POST":
            form.role_ids.data = [
                r.id for r in model.roles if r.name in WEB_ASSIGNABLE_ROLES
            ]
            form.organization_pick.data = model.organization_id or 0
            if hasattr(form, "organization_ids"):
                user_group_ids = [
                    ug.group_id
                    for ug in self.session.query(db.UserGroups).filter_by(user_id=model.id).all()
                ]
                form.organization_ids.data = user_group_ids

    def create_form(self, obj=None):
        form = super().create_form(obj)
        self._prepare_user_form(form)
        return form

    def edit_form(self, obj=None):
        form = super().edit_form(obj)
        self._prepare_user_form(form, obj)
        return form

    def validate_form(self, form):
        if not super().validate_form(form):
            return False
        if hasattr(form, "password") and not form._obj and not form.password.data:
            form.password.errors.append("Password is required when creating a user.")
            return False
        return True

    def on_model_change(self, form, model, is_created):
        sync_user_org_and_roles(form, model, self.session, is_created=is_created)

    @expose("/delete/", methods=("POST",))
    def delete_view(self):
        """Delete without the generic Flask-Admin success flash (we message in delete_model)."""
        return_url = get_redirect_target() or self.get_url(".index_view")
        if not self.can_delete:
            return redirect(return_url)
        form = self.delete_form()
        if self.validate_form(form):
            model = self.get_one(form.id.data)
            if model is None:
                flash(gettext("Record does not exist."), "error")
            elif self.delete_model(model):
                return redirect(return_url)
        else:
            flash_errors(form, message="Failed to delete record. %(error)s")
        return redirect(return_url)

    def delete_model(self, model):
        try:
            validate_user_deletable(model)
        except ValueError as exc:
            flash(str(exc), "error")
            return False
        username = model.username
        try:
            soft_delete_user(model, self.session)
            self.session.commit()
        except Exception:
            self.session.rollback()
            log.exception("Failed to delete user %s", model.id)
            flash("Failed to delete user. Check server logs.", "error")
            return False
        flash(
            f'User "{username}" removed. You can recreate with the same username/email.',
            "success",
        )
        return True


class ProjectView(BaseView):
    """Super-admin list/edit for proofing projects (books)."""

    can_create = False
    list_template = "admin/project_list.html"
    column_list = ["slug", "display_title", "is_publicly_viewable", "creator", "creator_mode"]
    column_labels = {
        "is_publicly_viewable": "Public on /books/",
        "creator_mode": "Creation Mode",
    }
    form_columns = ["slug", "display_title", "is_publicly_viewable", "description"]
    form_excluded_columns = ["creator", "board", "pages", "created_at", "updated_at"]


class ReportedIssueView(BaseView):
    """Super-admin view for user-reported issues."""

    column_list = ["id", "name", "email", "category", "message", "status", "created_at"]
    column_searchable_list = ["name", "email", "category", "message"]
    column_filters = ["category", "status", "created_at"]
    column_editable_list = ["status"]
    column_default_sort = ("created_at", True)
    form_columns = ["name", "email", "category", "message", "status"]


def create_admin_manager(app):
    session = q.get_session_class()
    url_prefix = app.config.get("APPLICATION_URL_PREFIX", "")
    admin_url = f"{url_prefix}/admin"
    admin = Admin(
        app,
        name="Kalanjiyam",
        index_view=KalanjiyamIndexView(url=admin_url),
        url=admin_url,
    )

    admin.add_view(
        PlatformView(
            name="Platform",
            category="Access",
            url="platform",
            endpoint="platform_view",
        )
    )
    admin.add_view(
        GroupsView(name="Groups", category="Access", url="groups", endpoint="groups_view")
    )
    admin.add_view(
        OrgAdminView(
            name="My Organization",
            category="Access",
            url="org",
            endpoint="org_admin_view",
        )
    )
    # Redirect /admin/groups -> /admin/groups/ (Flask-Admin registers with trailing slash)
    @app.route(f"{admin_url}/groups")
    def _redirect_groups_trailing_slash():
        return redirect(url_for("groups_view.index"))

    admin.add_view(ProjectView(db.Project, session))
    admin.add_view(UserView(db.User, session))
    admin.add_view(BaseView(db.Text, session))
    admin.add_view(ProjectSponsorshipView(db.ProjectSponsorship, session))
    admin.add_view(ReportedIssueView(db.ReportedIssue, session, name="Reported Issues"))

    return admin
