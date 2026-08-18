"""Views for basic site pages."""

from datetime import datetime, timedelta
from pathlib import Path

import json
import math
import os
import redis

from flask import Blueprint, current_app, flash, render_template, request, redirect, url_for
from flask_babel import lazy_gettext as _l
from flask_login import current_user
from flask_wtf import FlaskForm
from slugify import slugify
from sqlalchemy import orm
from wtforms import FileField, RadioField, StringField
from wtforms.validators import DataRequired, ValidationError
from wtforms.widgets import TextArea

from kalanjiyam import consts
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.tasks import projects as project_tasks
from kalanjiyam.utils.quotas import ensure_storage_quota_for_user
from kalanjiyam.views.proofing.decorators import moderator_required, p2_required

bp = Blueprint("proofing", __name__)


def _is_allowed_document_file(filename: str) -> bool:
    """True iff we accept this type of document upload."""
    return Path(filename).suffix in (".pdf", ".docx", ".doc")


def _required_if_archive(message: str):
    def fn(form, field):
        source = form.pdf_source.data
        if source == "archive.org" and not field.data:
            raise ValidationError(message)

    return fn


def _required_if_local(message: str):
    def fn(form, field):
        source = form.pdf_source.data
        if source == "local" and not field.data:
            raise ValidationError(message)

    return fn


class CreateProjectForm(FlaskForm):
    pdf_source = RadioField(
        _l("Source"),
        choices=[
            ("archive.org", _l("From archive.org")),
            ("local", _l("From my computer")),
        ],
        validators=[DataRequired()],
    )
    archive_identifier = StringField(
        _l("archive.org identifier"),
        validators=[
            _required_if_archive(_l("Please provide a valid archive.org identifier."))
        ],
    )
    local_file = FileField(
        _l("PDF file"), validators=[_required_if_local(_l("Please provide a PDF file."))]
    )
    local_title = StringField(
        _l("Title of the book (you can change this later)"),
        validators=[
            _required_if_local(
                _l("Please provide a title for your PDF."),
            )
        ],
    )

    license = RadioField(
        _l("License"),
        choices=[
            ("public", _l("Public domain")),
            ("copyrighted", _l("Copyrighted")),
            ("other", _l("Other")),
        ],
        validators=[DataRequired()],
    )
    custom_license = StringField(
        _l("License"),
        widget=TextArea(),
        render_kw={
            "placeholder": _l("Please tell us about this book's license."),
        },
    )


@bp.route("/")
def index():
    """List all available proofing projects with full tenant search, pagination, and Redis caching."""

    search_query = (request.args.get("q", "") or request.args.get("query", "")).strip()
    selected_mode = (request.args.get("mode", "all")).strip().lower()
    sort_field = (request.args.get("sort", "created")).strip().lower()
    sort_order = (request.args.get("order", "desc")).strip().lower()

    # 1. Parse pagination parameters safely
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = max(1, min(100, int(request.args.get("per_page", 20))))
    except (ValueError, TypeError):
        per_page = 20

    session = q.get_session()

    # 2. Eagerly load groups to perform authorization check without N+1 queries
    all_projects = (
        session.query(db.Project)
        .options(orm.selectinload(db.Project.groups))
        .all()
    )

    # 3. Filter projects based on user access permissions (tenant scope)
    projects = [p for p in all_projects if q.user_can_view_proofing_project(current_user, p)]

    # 4. Filter by creator mode if specified
    if selected_mode and selected_mode != "all":
        projects = [p for p in projects if getattr(p, "creator_mode", None) == selected_mode]

    # 5. Full tenant search filtering (matching display_title, print_title, author, or slug)
    if search_query:
        q_lower = search_query.lower()
        projects = [
            p for p in projects
            if q_lower in (p.display_title or "").lower()
            or q_lower in (p.print_title or "").lower()
            or q_lower in (p.author or "").lower()
            or q_lower in (p.slug or "").lower()
        ]

    # 6. Server-side sorting
    reverse = (sort_order == "desc")
    if sort_field == "title":
        projects.sort(key=lambda x: (x.display_title or "").lower(), reverse=reverse)
    else:
        # Default sort by created_at date
        projects.sort(key=lambda x: x.created_at, reverse=reverse)

    total_projects = len(projects)
    total_pages = max(1, math.ceil(total_projects / per_page)) if total_projects else 1
    if page > total_pages:
        page = total_pages

    # 7. Slice current page items
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_projects = projects[start_idx:end_idx]

    # 8. Eagerly load pages & page status for ONLY current page projects
    paginated_project_ids = [p.id for p in paginated_projects]
    if paginated_project_ids:
        session.query(db.Project).options(
            orm.selectinload(db.Project.pages).joinedload(db.Page.status)
        ).filter(db.Project.id.in_(paginated_project_ids)).all()

    status_classes = {
        SitePageStatus.R2: "bg-green-200",
        SitePageStatus.R1: "bg-yellow-200",
        SitePageStatus.R0: "bg-red-300",
        SitePageStatus.SKIP: "bg-slate-100",
    }

    # 9. Initialize Redis connection safely
    r_client = None
    try:
        r_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        r_client.ping()
    except Exception:
        r_client = None

    statuses_per_project = {}
    progress_per_project = {}
    pages_per_project = {}

    for project in paginated_projects:
        updated_ts = int(project.updated_at.timestamp()) if getattr(project, "updated_at", None) else 0
        cache_key = f"proofing:proj_stats:{project.id}:{updated_ts}"
        cached_data = None

        if r_client:
            try:
                raw_bytes = r_client.get(cache_key)
                if raw_bytes:
                    cached_data = json.loads(raw_bytes.decode("utf-8"))
            except Exception:
                cached_data = None

        if cached_data:
            statuses_per_project[project.id] = cached_data["statuses"]
            progress_per_project[project.id] = cached_data["progress"]
            pages_per_project[project.id] = cached_data["pages"]
            continue

        page_statuses = [p.status.name for p in project.pages]

        if not page_statuses:
            statuses_per_project[project.id] = {}
            pages_per_project[project.id] = 0
            progress_per_project[project.id] = 0
            cached_payload = {"statuses": {}, "progress": 0, "pages": 0}
        else:
            num_pages = len(page_statuses)
            project_counts = {}
            progress_val = 0
            for enum_value, class_ in status_classes.items():
                fraction = page_statuses.count(enum_value) / num_pages
                project_counts[class_] = fraction
                if enum_value == SitePageStatus.R0:
                    progress_val = 1 - fraction

            statuses_per_project[project.id] = project_counts
            pages_per_project[project.id] = num_pages
            progress_per_project[project.id] = progress_val
            cached_payload = {
                "statuses": project_counts,
                "progress": progress_val,
                "pages": num_pages,
            }

        if r_client:
            try:
                r_client.setex(cache_key, 3600, json.dumps(cached_payload))
            except Exception:
                pass

    return render_template(
        "proofing/index.html",
        projects=paginated_projects,
        statuses_per_project=statuses_per_project,
        progress_per_project=progress_per_project,
        pages_per_project=pages_per_project,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_projects=total_projects,
        search_query=search_query,
        selected_mode=selected_mode,
        sort_field=sort_field,
        sort_order=sort_order,
    )


@bp.route("/help")
def help_index():
    """Display index of all guidelines and manuals."""
    return render_template("proofing/help.html")


@bp.route("/help/beginners-guide")
def beginners_guide():
    """Display our minimal proofing guidelines."""
    return render_template("proofing/beginners-guide.html")


@bp.route("/help/complete-guide")
def complete_guide():
    """Display our complete proofing guidelines."""
    return render_template("proofing/complete-guide.html")


@bp.route("/help/editor-guide")
def editor_guide():
    """Describe how to use the page editor."""
    return render_template("proofing/editor-guide.html")


@bp.route("/create-project", methods=["GET", "POST"])
def create_project():
    if not current_app.config.get("ENABLE_GUEST_ACCESS", True) and not current_user.is_authenticated:
        flash(_l("Guest project creation is disabled. Please log in to create a project."), "warning")
        return redirect(url_for("auth.login"))

    settings = q.get_system_settings()
    guest_upload_limit = getattr(settings, "unregistered_user_upload_limit", 10)

    # Authorization checks
    is_p2_or_admin = (
        getattr(current_user, "is_p2", False)
        or getattr(current_user, "is_moderator", False)
        or getattr(current_user, "is_org_admin", False)
        or getattr(current_user, "is_super_admin", False)
    )

    is_open_tenant = False
    if current_user.is_authenticated:
        from kalanjiyam.utils.org_access import user_organization_id
        try:
            open_tenant = q.get_or_create_open_tenant()
            is_open_tenant = (user_organization_id(current_user) == open_tenant.id)
        except Exception:
            pass

    allowed = (
        not current_user.is_authenticated  # Guest
        or (current_user.is_authenticated and is_open_tenant)  # Registered in open-tenant
        or is_p2_or_admin  # Enterprise P2 or Admin
    )
    if not allowed:
        flash(_l("Sorry, you aren't authorized to use this feature."), "error")
        return redirect(url_for("proofing.index"))

    # Rate limiting for guest users
    if not current_user.is_authenticated:
        from kalanjiyam.utils.rate_limit import is_rate_limited
        ip_address = request.remote_addr
        fingerprint_id = request.cookies.get("device_fingerprint")
        limit = settings.unregistered_user_project_limit
        if is_rate_limited("create_project", ip_address, fingerprint_id, limit=limit):
            flash(
                _l(
                    "Rate limit exceeded. Guests can only create %(limit)s projects per 24 hours.",
                    limit=limit,
                ),
                "error",
            )
            return redirect(url_for("proofing.index"))

    from kalanjiyam.utils.translation_engine import get_available_translation_engines, get_supported_languages_list
    engines = get_available_translation_engines()
    languages = get_supported_languages_list()

    form = CreateProjectForm()

    if request.method == "POST" and request.form.get("docx_workflow") == "direct":
        import uuid
        import json
        import redis
        import os
        from kalanjiyam.tasks.translation import run_docx_translation

        file = request.files.get("local_file")
        if not file or not file.filename:
            flash(_l("Please upload a file."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)

        filename = file.filename
        if Path(filename).suffix not in (".docx", ".doc"):
            flash(_l("Please upload a Word document (.docx)."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)

        source_lang = request.form.get("source_lang", "sa")
        target_lang = request.form.get("target_lang", "en")
        engine = request.form.get("engine", "indictrans2")
        glossary = request.form.get("glossary") or None

        # Validate engine
        from kalanjiyam.utils.translation_engine import TranslationEngineFactory
        if not TranslationEngineFactory.is_supported(engine):
            flash(_l("Unsupported translation engine selected."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)

        docx_id = str(uuid.uuid4())
        from kalanjiyam.utils.storage import get_storage, docx_upload_key
        storage = get_storage()
        storage.save(docx_upload_key(docx_id), file.stream)

        # Store docx original filename and parameters in Redis
        r_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        r_client.setex(
            f"docx_info:{docx_id}",
            86400,
            json.dumps({
                "original_filename": filename,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "engine": engine,
                "glossary": glossary
            })
        )

        task = run_docx_translation.delay(
            app_env=current_app.config["KALANJIYAM_ENVIRONMENT"],
            docx_id=docx_id,
            source_lang=source_lang,
            target_lang=target_lang,
            engine=engine,
            glossary=glossary,
            creator_id=current_user.id if current_user.is_authenticated else None,
        )

        from kalanjiyam.utils.user_tasks import add_user_task, get_user_identifier
        user_id = get_user_identifier(current_user, request)
        if user_id:
            add_user_task(
                user_identifier=user_id,
                task_id=task.id,
                task_type="docx_translation",
                project_slug="",
                project_title=filename,
                extra_info={"docx_id": docx_id, "glossary": glossary}
            )

        return render_template(
            "proofing/docx-translate-post.html",
            task_id=task.id,
            docx_id=docx_id,
            status="PENDING",
            percent=0,
            current=0,
            total=0,
        )

    if form.validate_on_submit():
        if current_user.is_authenticated:
            if current_app.config.get("DEFAULT_PROJECT_REQUIRES_ORG", True) and not getattr(
                current_user, "organization_id", None
            ):
                flash(_l("Your account is not assigned to an organization."), "error")
                return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)
        title = form.local_title.data

        slug = slugify(title)

        session = q.get_session()

        # Check DB before writing files to storage to prevent overwriting existing project files
        existing_proj = session.query(db.Project).filter_by(slug=slug).first()
        if existing_proj:
            flash(_l('Project "%(title)s" already exists. Please choose a different title.', title=title), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)

        org_slug = "open-tenant"
        if current_user.is_authenticated:
            from kalanjiyam.utils.org_access import user_organization_id
            org_id = user_organization_id(current_user)
            if org_id:
                group = session.query(db.Group).get(org_id)
                if group:
                    org_slug = group.slug

        filename = form.local_file.raw_data[0].filename
        if not _is_allowed_document_file(filename):
            flash(_l("Please upload a PDF or DOCX."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)
        upload_size = 0
        if form.local_file.data and hasattr(form.local_file.data, "stream"):
            cur_pos = form.local_file.data.stream.tell()
            form.local_file.data.stream.seek(0, 2)
            upload_size = form.local_file.data.stream.tell()
            form.local_file.data.stream.seek(cur_pos)
        
        if current_user.is_authenticated:
            ensure_storage_quota_for_user(current_user, upload_size)
        else:
            if upload_size > guest_upload_limit * 1024 * 1024:
                flash(
                    _l(
                        "PDF size exceeds the allowed limit of %(limit)sMB for guest users.",
                        limit=guest_upload_limit,
                    ),
                    "error",
                )
                return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)

        # Save the original file so that it can be processed/downloaded later.
        # The Celery worker fetches it from storage by key, so web and worker
        # don't need a shared filesystem.
        from kalanjiyam.utils.storage import get_storage, pdf_key, project_docx_key

        is_uploaded_docx = filename.lower().endswith((".docx", ".doc"))
        source_pdf_key = None
        source_docx_key = None

        if is_uploaded_docx:
            source_docx_key = project_docx_key(slug, org_slug=org_slug)
            form.local_file.data.stream.seek(0)
            get_storage().save(source_docx_key, form.local_file.data.stream)
        else:
            source_pdf_key = pdf_key(slug, org_slug=org_slug)
            form.local_file.data.stream.seek(0)
            get_storage().save(source_pdf_key, form.local_file.data.stream)

        # Log usage action for guests
        if not current_user.is_authenticated:
            from kalanjiyam.utils.rate_limit import log_usage_action
            log_usage_action(
                action="create_project",
                ip_address=request.remote_addr,
                fingerprint_id=request.cookies.get("device_fingerprint"),
                project_slug=slug
            )

        if not current_user.is_authenticated:
            # Guest split task is routed to low-priority queue
            task = project_tasks.create_project.apply_async(
                kwargs={
                    "display_title": title,
                    "pdf_key": source_pdf_key,
                    "docx_key": source_docx_key,
                    "app_environment": current_app.config["KALANJIYAM_ENVIRONMENT"],
                    "creator_id": None,
                    "fingerprint_id": request.cookies.get("device_fingerprint"),
                    "org_slug": org_slug,
                },
                queue="low_priority"
            )
        else:
            task = project_tasks.create_project.delay(
                display_title=title,
                pdf_key=source_pdf_key,
                docx_key=source_docx_key,
                app_environment=current_app.config["KALANJIYAM_ENVIRONMENT"],
                creator_id=current_user.id,
                org_slug=org_slug,
            )

        from kalanjiyam.utils.user_tasks import add_user_task, get_user_identifier
        user_id = get_user_identifier(current_user, request)
        if user_id:
            add_user_task(
                user_identifier=user_id,
                task_id=task.id,
                task_type="create_project",
                project_slug=slug,
                project_title=title,
            )

        return render_template(
            "proofing/create-project-post.html",
            status=task.status,
            current=0,
            total=0,
            percent=0,
            task_id=task.id,
        )

    return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages)


@bp.route("/status/<task_id>")
def create_project_status(task_id):
    """AJAX summary of the task."""
    r = project_tasks.create_project.AsyncResult(task_id)

    info = r.info or {}
    error = None
    if isinstance(info, Exception):
        current = total = percent = 0
        slug = None
        error = str(info)
    elif r.status == 'FAILURE':
        current = total = percent = 0
        slug = None
        error = str(info) if info else "An error occurred during project creation."
    else:
        current = info.get("current", 100)
        total = info.get("total", 100)
        slug = info.get("slug", None)
        percent = 100 * current / total

    return render_template(
        "include/task-progress.html",
        status=r.status,
        current=current,
        total=total,
        percent=percent,
        slug=slug,
        error=error,
    )


@bp.route("/recent-changes")
def recent_changes():
    """Show recent changes across all projects."""
    num_per_page = 100

    # Exclude bot edits, which overwhelm all other edits on the site.
    bot_user = q.user(consts.BOT_USERNAME)
    assert bot_user, "Bot user not defined"

    session = q.get_session()
    recent_revisions = (
        session.query(db.Revision)
        .filter(db.Revision.author_id != bot_user.id)
        .order_by(db.Revision.created.desc())
        .limit(num_per_page * 2)  # Fetch more to allow filtering
        .all()
    )
    recent_revisions = [r for r in recent_revisions if q.user_can_view_proofing_project(current_user, r.project)][:num_per_page]

    # Pre-calculate diffs against previous page revision
    if recent_revisions:
        page_ids = list({r.page_id for r in recent_revisions})
        all_page_revs = (
            session.query(db.Revision)
            .filter(db.Revision.page_id.in_(page_ids))
            .order_by(db.Revision.created.desc())
            .all()
        )
        revs_by_page = {}
        for rev in all_page_revs:
            revs_by_page.setdefault(rev.page_id, []).append(rev)

        from kalanjiyam.utils.diff import revision_diff

        from kalanjiyam.utils import proofing_utils

        for r in recent_revisions:
            page_revs = revs_by_page.get(r.page_id, [])
            try:
                idx = page_revs.index(r)
            except ValueError:
                idx = -1

            cur_text = proofing_utils.revision_plain_content(r)
            if idx != -1 and idx + 1 < len(page_revs):
                prev_r = page_revs[idx + 1]
                r.prev_revision_id = prev_r.id
                prev_text = proofing_utils.revision_plain_content(prev_r)
                r.diff = revision_diff(prev_text, cur_text)
            else:
                r.prev_revision_id = None
                if cur_text:
                    r.diff = revision_diff("", cur_text)
                else:
                    r.diff = None

    recent_activity = [("revision", r.created, r) for r in recent_revisions]

    recent_projects = (
        session.query(db.Project)
        .order_by(db.Project.created_at.desc())
        .limit(num_per_page * 2)
        .all()
    )
    recent_projects = [p for p in recent_projects if q.user_can_view_proofing_project(current_user, p)][:num_per_page]
    recent_activity += [("project", p.created_at, p) for p in recent_projects]

    recent_activity.sort(key=lambda x: x[1], reverse=True)
    recent_activity = recent_activity[:num_per_page]
    return render_template(
        "proofing/recent-changes.html", recent_activity=recent_activity
    )


@bp.route("/talk")
def talk():
    """Show discussion across all projects."""
    projects = [p for p in q.projects() if q.user_can_view_proofing_project(current_user, p)]

    # FIXME: optimize this once we have a higher thread volume.
    all_threads = [(p, t) for p in projects for t in p.board.threads]
    all_threads.sort(key=lambda x: x[1].updated_at, reverse=True)

    return render_template("proofing/talk.html", all_threads=all_threads)


@bp.route("/admin/dashboard/")
@moderator_required
def dashboard():
    now = datetime.now()
    days_ago_30d = now - timedelta(days=30)
    days_ago_7d = now - timedelta(days=7)
    days_ago_1d = now - timedelta(days=1)

    session = q.get_session()
    bot = session.query(db.User).filter_by(username=consts.BOT_USERNAME).one()
    bot_id = bot.id

    revisions_30d = (
        session.query(db.Revision)
        .filter(
            (db.Revision.created >= days_ago_30d) & (db.Revision.author_id != bot_id)
        )
        .options(orm.load_only(db.Revision.created, db.Revision.author_id))
        .order_by(db.Revision.created)
        .all()
    )
    revisions_7d = [x for x in revisions_30d if x.created >= days_ago_7d]
    revisions_1d = [x for x in revisions_7d if x.created >= days_ago_1d]
    num_revisions_30d = len(revisions_30d)
    num_revisions_7d = len(revisions_7d)
    num_revisions_1d = len(revisions_1d)

    num_contributors_30d = len({x.author_id for x in revisions_30d if x.author_id is not None})
    num_contributors_7d = len({x.author_id for x in revisions_7d if x.author_id is not None})
    num_contributors_1d = len({x.author_id for x in revisions_1d if x.author_id is not None})

    return render_template(
        "proofing/dashboard.html",
        num_revisions_30d=num_revisions_30d,
        num_revisions_7d=num_revisions_7d,
        num_revisions_1d=num_revisions_1d,
        num_contributors_30d=num_contributors_30d,
        num_contributors_7d=num_contributors_7d,
        num_contributors_1d=num_contributors_1d,
    )


@bp.route("/api/tasks")
def get_tasks_api():
    """Retrieve background tasks for the current user."""
    from kalanjiyam.utils.user_tasks import get_user_tasks, get_user_identifier
    user_id = get_user_identifier(current_user, request)
    if not user_id:
        return {"tasks": []}
    
    try:
        tasks = get_user_tasks(user_id)
        # Limit to the most recent 10 tasks to keep UI clean and fast
        return {"tasks": tasks[:10]}
    except Exception as e:
        current_app.logger.warning(f"Error fetching tasks: {e}")
        return {"tasks": []}, 500


@bp.route("/api/tasks/<task_id>/cancel", methods=["POST"])
def cancel_task_api(task_id):
    """Cancel a background task for the current user."""
    from kalanjiyam.utils.user_tasks import cancel_user_task, get_user_identifier
    user_id = get_user_identifier(current_user, request)
    if not user_id:
        return {"error": "Unauthorized"}, 401
        
    try:
        success = cancel_user_task(user_id, task_id)
        if success:
            return {"success": True}
        return {"error": "Task not found or not in active state"}, 400
    except Exception as e:
        current_app.logger.warning(f"Error cancelling task: {e}")
        return {"error": "Internal server error"}, 500


@bp.route("/translate/docx", methods=["GET", "POST"])
def docx_translate():
    import uuid
    import json
    import redis
    import os
    from flask import abort
    from kalanjiyam.utils.translation_engine import get_available_translation_engines, get_supported_languages_list
    from kalanjiyam.tasks.translation import run_docx_translation

    engines = get_available_translation_engines()
    languages = get_supported_languages_list()

    if request.method == "POST":
        # Check if file uploaded
        file = request.files.get("file")
        if not file or not file.filename:
            flash(_l("Please upload a file."), "error")
            return render_template("proofing/docx-translate.html", engines=engines, languages=languages)

        filename = file.filename
        if Path(filename).suffix not in (".docx", ".doc"):
            flash(_l("Please upload a Word document (.docx)."), "error")
            return render_template("proofing/docx-translate.html", engines=engines, languages=languages)

        source_lang = request.form.get("source_lang", "sa")
        target_lang = request.form.get("target_lang", "en")
        engine = request.form.get("engine", "indictrans2")
        glossary = request.form.get("glossary") or None

        # Validate engine
        from kalanjiyam.utils.translation_engine import TranslationEngineFactory
        if not TranslationEngineFactory.is_supported(engine):
            flash(_l("Unsupported translation engine selected."), "error")
            return render_template("proofing/docx-translate.html", engines=engines, languages=languages)

        docx_id = str(uuid.uuid4())
        from kalanjiyam.utils.storage import get_storage, docx_upload_key
        storage = get_storage()
        storage.save(docx_upload_key(docx_id), file.stream)

        # Store docx original filename and parameters in Redis
        r_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        r_client.setex(
            f"docx_info:{docx_id}",
            86400,
            json.dumps({
                "original_filename": filename,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "engine": engine,
                "glossary": glossary
            })
        )

        task = run_docx_translation.delay(
            app_env=current_app.config["KALANJIYAM_ENVIRONMENT"],
            docx_id=docx_id,
            source_lang=source_lang,
            target_lang=target_lang,
            engine=engine,
            glossary=glossary,
            creator_id=current_user.id if current_user.is_authenticated else None,
        )

        from kalanjiyam.utils.user_tasks import add_user_task, get_user_identifier
        user_id = get_user_identifier(current_user, request)
        if user_id:
            add_user_task(
                user_identifier=user_id,
                task_id=task.id,
                task_type="docx_translation",
                project_slug="",
                project_title=filename,
                extra_info={"docx_id": docx_id, "glossary": glossary}
            )

        return render_template(
            "proofing/docx-translate-post.html",
            task_id=task.id,
            docx_id=docx_id,
            status="PENDING",
            percent=0,
            current=0,
            total=0,
        )

    return render_template("proofing/docx-translate.html", engines=engines, languages=languages)


@bp.route("/translate/docx/status/<task_id>")
def docx_translate_status(task_id):
    from celery.result import AsyncResult
    from kalanjiyam.tasks import app as celery_app
    
    r = AsyncResult(task_id, app=celery_app)
    info = r.info or {}
    
    error = None
    if isinstance(info, Exception):
        current = total = percent = 0
        error = str(info)
    elif r.status == 'FAILURE':
        current = total = percent = 0
        error = str(info) if info else "An error occurred during translation."
    else:
        current = info.get("current", 0)
        total = info.get("total", 0)
        percent = info.get("percent", 0)
        
    return {
        "status": r.status,
        "current": current,
        "total": total,
        "percent": percent,
        "error": error
    }


@bp.route("/translate/docx/download/<docx_id>")
def docx_translate_download(docx_id):
    import redis
    import os
    import json
    from flask import abort
    from kalanjiyam.utils.storage import get_storage, docx_translation_key
    storage = get_storage()
    trans_key = docx_translation_key(docx_id)
    
    if not storage.exists(trans_key):
        abort(404, description=_l("Translated file not found."))

    r_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    info_json = r_client.get(f"docx_info:{docx_id}")
    if info_json:
        info = json.loads(info_json)
        original_filename = info.get("original_filename", "translated_document.docx")
        base_name = Path(original_filename).stem
        download_name = f"{base_name}_translated.docx"
    else:
        download_name = "translated_document.docx"

    return storage.serve(trans_key, as_attachment=True, download_name=download_name)


