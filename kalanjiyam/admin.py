"""Manages an internal admin view for site data."""

from flask import abort, render_template, request, flash, redirect, url_for, send_file, current_app
from flask_admin import Admin, AdminIndexView, expose, BaseView as AdminBaseView
from flask_admin.contrib import sqla
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.utils.assets import get_page_image_filepath





class KalanjiyamIndexView(AdminIndexView):
    def is_accessible(self):
        return current_user.is_authenticated and current_user.is_moderator
    
    def inaccessible_callback(self, name, **kwargs):
        abort(404)
    
    @expose("/")
    def index(self):
        # For admin users, show the export/import dashboard
        if current_user.is_admin:
            projects = q.projects()
            print(f"DEBUG: Rendering admin dashboard with {len(projects)} projects")
            return render_template("admin/export_import.html", projects=projects)
        
        # For moderators, show the default admin interface
        return super().index()
    
    @expose('/export/project/<project_slug>')
    @login_required
    def export_project(self, project_slug):
        """Export a single project as a ZIP file."""
        if not current_user.is_admin:
            abort(404)
        
        project = q.project(project_slug)
        if not project:
            abort(404)
        
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
        if not current_user.is_admin:
            abort(404)
        
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
                project_data = self._export_project_data(project)
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
            return redirect(url_for('admin.index'))
    
    @expose('/import', methods=['GET', 'POST'])
    @login_required
    def import_project(self):
        """Import a project from a ZIP file."""
        if not current_user.is_admin:
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
        if not current_user.is_admin:
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
            
            author = self._get_or_create_user(session, revision_data['author_username'])
            status = self._get_or_create_page_status(session, revision_data['status_name'])
            
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
            author = self._get_or_create_user(session, translation_data['author_username'])
            
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


class BaseView(sqla.ModelView):
    """Base view for models.

    By default, only admins can see model data.
    """

    def is_accessible(self):
        return current_user.is_admin

    def inaccessible_callback(self, name, **kw):
        abort(404)


class ModeratorBaseView(sqla.ModelView):
    """Base view for models that moderators are allowed to access."""

    def is_accessible(self):
        return current_user.is_moderator

    def inaccessible_callback(self, name, **kw):
        abort(404)


class UserView(BaseView):
    column_list = form_columns = ["username", "email"]
    can_delete = False


class TextBlockView(BaseView):
    column_list = form_columns = ["text", "slug", "xml"]


class TextView(BaseView):
    column_list = form_columns = ["slug", "title"]

    form_widget_args = {"header": {"readonly": True}}


class ProjectView(BaseView):
    column_list = ["slug", "display_title", "creator"]
    form_excluded_columns = ["creator", "board", "pages", "created_at", "updated_at"]


class DictionaryView(BaseView):
    column_list = form_columns = ["slug", "title"]


class GenreView(ModeratorBaseView):
    pass


class SponsorshipView(ModeratorBaseView):
    column_labels = dict(
        sa_title="Sanskrit title",
        en_title="English title",
        cost_inr="Estimated cost (INR)",
    )
    create_template = "admin/sponsorship_create.html"
    edit_template = "admin/sponsorship_edit.html"


class ContributorInfoView(ModeratorBaseView):
    column_labels = dict(
        sa_title="Sanskrit title",
        title="Title, occupation, role, etc.",
        description="Description (short biography)",
    )
    create_template = "admin/sponsorship_create.html"
    edit_template = "admin/sponsorship_edit.html"


def create_admin_manager(app):
    session = q.get_session_class()
    admin = Admin(
        app,
        name="Kalanjiyam",
        index_view=KalanjiyamIndexView(),
    )

    admin.add_view(DictionaryView(db.Dictionary, session))
    admin.add_view(ProjectView(db.Project, session))
    admin.add_view(TextBlockView(db.TextBlock, session))
    admin.add_view(TextView(db.Text, session))
    admin.add_view(UserView(db.User, session))
    admin.add_view(GenreView(db.Genre, session))
    admin.add_view(SponsorshipView(db.ProjectSponsorship, session))
    admin.add_view(ContributorInfoView(db.ContributorInfo, session))
    
    # Export/Import functionality is now integrated into the main admin index view

    return admin
