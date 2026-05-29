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
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import kalanjiyam.database as db
import kalanjiyam.queries as q

log = logging.getLogger(__name__)
from kalanjiyam.admin_user import (
    WEB_ASSIGNABLE_ROLES,
    assignable_role_choices,
    organization_choices,
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

    @after_this_request
    def _cleanup(response):
        zip_path.unlink(missing_ok=True)
        return response


class KalanjiyamIndexView(AdminIndexView):
    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_moderator
    
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
        if not (is_platform_super_admin() or current_user.is_org_admin):
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
            flash(f"Export failed: {str(e)}")
            return redirect(url_for('admin.index'))
    
    @expose('/export/all-projects')
    @login_required
    def export_all_projects(self):
        """Export all projects as a single ZIP file."""
        if not (is_platform_super_admin() or current_user.is_org_admin):
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
                pdf_source = (
                    Path(current_app.config["UPLOAD_FOLDER"]) / "projects" / project.slug / "pdf" / "source.pdf"
                )
                if pdf_source.exists():
                    (files_dir / "source.pdf").write_bytes(pdf_source.read_bytes())
                pages_dir = files_dir / "pages"
                pages_dir.mkdir(exist_ok=True)
                for page in project.pages:
                    image_path = get_page_image_filepath(project.slug, page.slug)
                    if image_path.exists():
                        (pages_dir / f"{page.slug}.jpg").write_bytes(image_path.read_bytes())

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
            flash(f"Export failed: {str(e)}")
            return redirect(url_for('admin.index'))
    
    @expose('/export-import')
    @login_required
    def export_import_dashboard(self):
        """Export/import dashboard for super admins."""
        if not is_platform_super_admin():
            if current_user.is_org_admin:
                return redirect(url_for("org_admin_view.index"))
            abort(404)
        projects = self._projects_for_current_admin()
        return render_template("admin/export_import.html", projects=projects)

    @expose('/import', methods=['GET', 'POST'])
    @login_required
    def import_project(self):
        """Import a project from a ZIP file."""
        if not is_platform_super_admin():
            abort(404)
        
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
                result = self._extract_and_import_project(temp_file, session)
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
    
    @expose('/import/all-projects', methods=['GET', 'POST'])
    @login_required
    def import_all_projects(self):
        """Import all projects from a ZIP file."""
        if not is_platform_super_admin():
            abort(404)
        
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
            page_data = {
                'slug': page.slug,
                'order': page.order,
                'version': page.version,
                'ocr_bounding_boxes': page.ocr_bounding_boxes,
                'page_width': page.page_width,
                'page_height': page.page_height,
                'status_name': page.status.name if page.status else None
            }
            project_data['pages'].append(page_data)
            
            # Export revisions for this page
            for revision in page.revisions:
                revision_data = {
                    'revision_key': revision.id,
                    'page_slug': page.slug,
                    'author_username': revision.author.username if revision.author else None,
                    'status_name': revision.status.name if revision.status else None,
                    'created': revision.created.isoformat(),
                    'summary': revision.summary,
                    'content': revision.content,
                    'content_format': getattr(revision, 'content_format', 'plain'),
                    'document': getattr(revision, 'document', None),
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
        return render_template(
            "admin/platform_dashboard.html",
            orgs=orgs,
            org_count=len(orgs),
            total_storage_used=total_storage_used,
            total_ocr_used=total_ocr_used,
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
            admin_user_id = request.form.get("admin_user_id", type=int)
            if not name:
                flash("Name is required.", "error")
                return render_template("admin/group_form.html", group=None, all_users=all_users, csrf_token=generate_csrf())
            session = q.get_session()
            group = db.Group(
                name=name,
                slug=slug,
                description=description,
                storage_quota_bytes=(storage_quota_mb * 1024 * 1024) if storage_quota_mb else None,
                ocr_credit_limit=ocr_credit_limit,
                admin_user_id=admin_user_id,
            )
            session.add(group)
            session.flush()
            _promote_org_admin(session, group, admin_user_id)
            session.commit()
            flash("Group created.")
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
            admin_user_id = request.form.get("admin_user_id", type=int)
            if not name:
                flash("Name is required.", "error")
                return render_template("admin/group_form.html", group=group, all_users=all_users, csrf_token=generate_csrf())
            group.name = name
            group.slug = slug
            group.description = description
            group.storage_quota_bytes = (storage_quota_mb * 1024 * 1024) if storage_quota_mb else None
            group.ocr_credit_limit = ocr_credit_limit
            group.admin_user_id = admin_user_id
            session = q.get_session()
            session.add(group)
            session.flush()
            _promote_org_admin(session, group, admin_user_id)
            session.commit()
            flash("Group updated.")
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
        flash("Group deleted.")
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
                    flash("User added to group.")
            elif action == "remove_user":
                user_id = request.form.get("user_id", type=int)
                if user_id:
                    q.remove_user_from_group(user_id=user_id, group_id=id)
                    flash("User removed from group.")
            elif action == "add_project":
                project_id = request.form.get("project_id", type=int)
                if project_id:
                    q.add_project_to_group(project_id=project_id, group_id=id)
                    flash("Project added to group.")
            elif action == "remove_project":
                project_id = request.form.get("project_id", type=int)
                if project_id:
                    q.remove_project_from_group(project_id=project_id, group_id=id)
                    flash("Project removed from group.")
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


class BaseView(sqla.ModelView):
    """Base view for models.

    By default, only platform super admins can see model data.
    """

    def is_accessible(self):
        return is_platform_super_admin()

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
    column_labels = {"organization_id": "Organization"}
    column_formatters = {
        "organization_id": lambda v, c, m, p: (
            f"{m.organization.name} ({m.organization.slug})"
            if m.organization
            else "—"
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
    form_columns = ["username", "email", "password", "organization_pick", "role_ids"]
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
        if model is not None:
            form.role_ids.data = [
                r.id for r in model.roles if r.name in WEB_ASSIGNABLE_ROLES
            ]
            form.organization_pick.data = model.organization_id or 0

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

    list_template = "admin/project_list.html"
    column_list = ["slug", "display_title", "is_publicly_viewable", "creator"]
    column_labels = {"is_publicly_viewable": "Public on /books/"}
    form_columns = ["slug", "display_title", "is_publicly_viewable", "description"]
    form_excluded_columns = ["creator", "board", "pages", "created_at", "updated_at"]


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

    return admin
