"""Views for basic site pages."""

from datetime import datetime, timedelta
from pathlib import Path

import json
import math
import os
import re
import redis

from flask import Blueprint, current_app, flash, make_response, render_template, request, redirect, url_for
from flask_babel import lazy_gettext as _l
from flask_login import current_user
from flask_wtf import FlaskForm
from slugify import slugify
from sqlalchemy import orm
from wtforms import FileField, MultipleFileField, RadioField, StringField
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

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _is_allowed_document_file(filename: str) -> bool:
    """True iff we accept this type of document/image upload."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _natural_sort_key(s: str):
    """Sort strings with embedded numbers naturally (e.g., page_2 before page_10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]


def _required_if_archive(message: str):
    def fn(form, field):
        source = form.pdf_source.data
        if source == "archive.org" and not field.data:
            raise ValidationError(message)

    return fn


def _required_if_local(message: str):
    def fn(form, field):
        source = form.pdf_source.data
        if source == "local":
            data = field.data
            if not data:
                raise ValidationError(message)
            if isinstance(data, list):
                if not any(bool(getattr(f, "filename", None)) for f in data):
                    raise ValidationError(message)
            elif hasattr(data, "filename") and not data.filename:
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
    local_file = MultipleFileField(
        _l("Document or images"),
        validators=[
            _required_if_local(_l("Please provide a document or image file(s) to upload."))
        ],
    )
    local_title = StringField(
        _l("Title of the book (you can change this later)"),
        validators=[
            _required_if_local(
                _l("Please provide a title for your document or images."),
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
    selected_org = (request.args.get("org", "all")).strip()
    sort_field = (request.args.get("sort", "created")).strip().lower()
    sort_order = (request.args.get("order", "desc")).strip().lower()

    # Parse single or multiple condition issue filters
    raw_issues = request.args.getlist("issue")
    if not raw_issues:
        raw_issue_str = request.args.get("issue", "")
        if raw_issue_str:
            raw_issues = [s.strip() for s in raw_issue_str.split(",") if s.strip()]
    selected_issues = [i.strip() for i in raw_issues if i and i.strip() and i.strip() != "all"]

    # 1. Parse pagination parameters safely
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", 20))
        if per_page not in (10, 20, 50, 100):
            per_page = 20
    except (ValueError, TypeError):
        per_page = 20

    session = q.get_session()

    # 2. Collect available organizations for filtering
    user_organizations = []
    if current_user.is_authenticated:
        if getattr(current_user, "is_super_admin", False):
            user_organizations = list(q.groups())
        elif getattr(current_user, "is_master_user", False):
            if getattr(current_user, "groups", None):
                user_organizations = list(current_user.groups)
            elif getattr(current_user, "id", None):
                user_organizations = (
                    session.query(db.Group)
                    .join(db.UserGroups, db.UserGroups.group_id == db.Group.id)
                    .filter(db.UserGroups.user_id == current_user.id)
                    .all()
                )

    # 3. Eagerly load groups to perform authorization check without N+1 queries
    all_projects = (
        session.query(db.Project)
        .options(orm.selectinload(db.Project.groups))
        .all()
    )

    # 4. Filter projects based on user access permissions (tenant scope)
    accessible_projects = [p for p in all_projects if q.user_can_view_proofing_project(current_user, p)]
    has_any_projects = bool(accessible_projects)

    projects = accessible_projects

    # Collect all available condition tags across accessible projects for the filter UI
    available_condition_tags = set()
    for p in accessible_projects:
        if p.condition_tags and isinstance(p.condition_tags, list):
            for t in p.condition_tags:
                t_name = t.get("name") if isinstance(t, dict) else (str(t) if isinstance(t, str) else "")
                if t_name and t_name.strip():
                    available_condition_tags.add(t_name.strip())
    available_condition_tags = sorted(available_condition_tags, key=lambda s: s.lower())

    # 5. Filter by condition tags / issues if specified
    if selected_issues:
        selected_issues_lower = {i.lower() for i in selected_issues}
        projects = [
            p for p in projects
            if any(
                (tag.get("name", "").lower() in selected_issues_lower)
                for tag in (p.condition_tag_list or [])
            )
        ]

    # 6. Filter by organization if specified
    if selected_org and selected_org != "all":
        projects = [
            p for p in projects
            if any(g.slug == selected_org for g in p.groups)
        ]

    # 7. Filter by creator mode if specified
    if selected_mode and selected_mode != "all":
        projects = [p for p in projects if getattr(p, "creator_mode", None) == selected_mode]

    # 8. Full tenant search filtering (matching display_title, print_title, author, or slug)
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

    template_kwargs = {
        "projects": paginated_projects,
        "statuses_per_project": statuses_per_project,
        "progress_per_project": progress_per_project,
        "pages_per_project": pages_per_project,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "total_projects": total_projects,
        "search_query": search_query,
        "selected_mode": selected_mode,
        "selected_org": selected_org,
        "selected_issues": selected_issues,
        "available_condition_tags": available_condition_tags,
        "user_organizations": user_organizations,
        "sort_field": sort_field,
        "sort_order": sort_order,
        "has_any_projects": has_any_projects,
    }

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.args.get("ajax") == "1"
    if is_ajax:
        rendered = render_template("proofing/_projects_list.html", **template_kwargs)
        resp = make_response(rendered)
        resp.headers["X-Total-Projects"] = str(total_projects)
        return resp

    return render_template("proofing/index.html", **template_kwargs)


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
        getattr(current_user, "is_p1", False)
        or getattr(current_user, "is_p2", False)
        or getattr(current_user, "is_moderator", False)
        or getattr(current_user, "is_master_user", False)
        or getattr(current_user, "is_org_admin", False)
        or getattr(current_user, "is_super_admin", False)
    )

    session = q.get_session()
    user_organizations = []
    if current_user.is_authenticated:
        if getattr(current_user, "groups", None):
            user_organizations = list(current_user.groups)
        elif getattr(current_user, "id", None):
            user_organizations = (
                session.query(db.Group)
                .join(db.UserGroups, db.Group.id == db.UserGroups.group_id)
                .filter(db.UserGroups.user_id == current_user.id)
                .all()
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

    system_settings = q.get_system_settings()
    default_trans_engine = (
        getattr(system_settings, "default_translation_engine", "indictrans2")
        or "indictrans2"
    )
    rec_trans_engine = getattr(
        system_settings, "recommended_translation_engine", None
    )
    is_super_admin = getattr(current_user, "is_super_admin", False)

    from kalanjiyam.utils.translation_engine import (
        build_translation_choices,
        normalize_translation_engine,
        get_supported_languages_list,
    )
    engines = build_translation_choices(
        is_super_admin=is_super_admin,
        recommended_engine=rec_trans_engine,
        default_engine=default_trans_engine,
    )
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
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        filename = file.filename
        if Path(filename).suffix not in (".docx", ".doc"):
            flash(_l("Please upload a Word document (.docx)."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        source_lang = request.form.get("source_lang", "sa")
        target_lang = request.form.get("target_lang", "en")
        engine_val = request.form.get("engine", default_trans_engine)
        engine = normalize_translation_engine(engine_val)
        glossary = request.form.get("glossary") or None

        # Validate engine
        from kalanjiyam.utils.translation_engine import TranslationEngineFactory
        if not TranslationEngineFactory.is_supported(engine):
            flash(_l("Unsupported translation engine selected."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

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
            ) and not user_organizations:
                flash(_l("Your account is not assigned to an organization."), "error")
                return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)
        title = form.local_title.data

        slug = slugify(title)

        # Check DB before writing files to storage to prevent overwriting existing project files
        existing_proj = session.query(db.Project).filter_by(slug=slug).first()
        if existing_proj:
            flash(_l('Project "%(title)s" already exists. Please choose a different title.', title=title), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        selected_org_slug = request.form.get("selected_org_slug")
        org_slug = "open-tenant"
        if current_user.is_authenticated:
            user_org_map = {g.slug: g for g in user_organizations} if user_organizations else {}
            if selected_org_slug and selected_org_slug in user_org_map:
                org_slug = selected_org_slug
            else:
                from kalanjiyam.utils.org_access import user_organization_id
                org_id = user_organization_id(current_user)
                if org_id:
                    group = session.query(db.Group).get(org_id)
                    if group:
                        org_slug = group.slug

        raw_files = request.files.getlist("local_file")
        if not raw_files or not any(getattr(f, "filename", None) for f in raw_files):
            if isinstance(form.local_file.data, list):
                raw_files = form.local_file.data
            elif form.local_file.data:
                raw_files = [form.local_file.data]

        uploaded_files = [f for f in raw_files if f and getattr(f, "filename", None)]
        if not uploaded_files:
            flash(_l("Please upload a file or images."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        for f in uploaded_files:
            if not _is_allowed_document_file(f.filename):
                flash(_l("Unsupported file type: %(filename)s. Please upload a PDF, DOCX, or JPG/PNG image(s).", filename=f.filename), "error")
                return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        is_multiple = len(uploaded_files) > 1
        all_are_images = all(Path(f.filename).suffix.lower() in IMAGE_EXTENSIONS for f in uploaded_files)

        if is_multiple and not all_are_images:
            flash(_l("When uploading multiple files, all files must be images (.jpg, .jpeg, .png, .webp)."), "error")
            return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        is_image_upload = all_are_images
        first_filename = uploaded_files[0].filename
        is_uploaded_docx = (not is_image_upload) and Path(first_filename).suffix.lower() in (".docx", ".doc")

        upload_size = 0
        for f in uploaded_files:
            if hasattr(f, "stream"):
                cur_pos = f.stream.tell()
                f.stream.seek(0, 2)
                upload_size += f.stream.tell()
                f.stream.seek(cur_pos)

        if current_user.is_authenticated:
            ensure_storage_quota_for_user(current_user, upload_size)
        else:
            if upload_size > guest_upload_limit * 1024 * 1024:
                flash(
                    _l(
                        "Upload size exceeds the allowed limit of %(limit)sMB for guest users.",
                        limit=guest_upload_limit,
                    ),
                    "error",
                )
                return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)

        # Save the original file so that it can be processed/downloaded later.
        # The Celery worker fetches it from storage by key, so web and worker
        # don't need a shared filesystem.
        from kalanjiyam.utils.storage import get_storage, pdf_key, project_docx_key, project_raw_image_key

        source_pdf_key = None
        source_docx_key = None
        image_keys = None

        if is_uploaded_docx:
            source_docx_key = project_docx_key(slug, org_slug=org_slug)
            uploaded_files[0].stream.seek(0)
            get_storage().save(source_docx_key, uploaded_files[0].stream)
        elif is_image_upload:
            sorted_images = sorted(uploaded_files, key=lambda f: _natural_sort_key(f.filename))
            image_keys = []
            for idx, img_file in enumerate(sorted_images, start=1):
                ext = Path(img_file.filename).suffix.lower() or ".jpg"
                staged_name = f"{idx}{ext}"
                img_key = project_raw_image_key(slug, staged_name, org_slug=org_slug)
                img_file.stream.seek(0)
                get_storage().save(img_key, img_file.stream)
                image_keys.append(img_key)
        else:
            source_pdf_key = pdf_key(slug, org_slug=org_slug)
            uploaded_files[0].stream.seek(0)
            get_storage().save(source_pdf_key, uploaded_files[0].stream)

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
                    "image_keys": image_keys,
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
                image_keys=image_keys,
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

        doc_type = "images" if is_image_upload else ("docx" if is_uploaded_docx else "pdf")
        return render_template(
            "proofing/create-project-post.html",
            status=task.status,
            current=0,
            total=0,
            percent=0,
            task_id=task.id,
            doc_type=doc_type,
        )

    return render_template("proofing/create-project.html", form=form, guest_upload_limit=guest_upload_limit, engines=engines, languages=languages, user_organizations=user_organizations)


@bp.route("/status/<task_id>")
def create_project_status(task_id):
    """AJAX summary of the task."""
    r = project_tasks.create_project.AsyncResult(task_id)

    info = r.info or {}
    error = None
    doc_type = "pdf"
    if isinstance(info, dict):
        doc_type = info.get("doc_type", "pdf")

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
        doc_type=doc_type,
    )


@bp.route("/recent-changes")
def recent_changes():
    """Show recent changes across all projects with search and date range filtering."""
    from datetime import datetime, timedelta, date as dt_date
    from sqlalchemy import or_

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = max(1, min(100, int(request.args.get("per_page", 25))))
    except (ValueError, TypeError):
        per_page = 25

    search_query = request.args.get("q", "").strip()
    start_date_str = request.args.get("start_date", "").strip()
    end_date_str = request.args.get("end_date", "").strip()
    quick_range = request.args.get("range", "").strip()

    # Quick date range presets
    today = datetime.utcnow().date()
    if quick_range == "today":
        start_date_str = today.strftime("%Y-%m-%d")
        end_date_str = today.strftime("%Y-%m-%d")
    elif quick_range == "7d":
        start_date_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date_str = today.strftime("%Y-%m-%d")
    elif quick_range == "30d":
        start_date_str = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        end_date_str = today.strftime("%Y-%m-%d")

    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            start_date = None
            start_date_str = ""
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            end_date = None
            end_date_str = ""

    session = q.get_session()

    # 1. Fetch accessible projects for current user in one query with eager loaded groups
    all_projects = (
        session.query(db.Project)
        .options(orm.selectinload(db.Project.groups))
        .all()
    )
    accessible_projects = [p for p in all_projects if q.user_can_view_proofing_project(current_user, p)]
    accessible_project_ids = [p.id for p in accessible_projects]

    if not accessible_project_ids:
        return render_template(
            "proofing/recent-changes.html",
            recent_activity=[],
            page=1,
            per_page=per_page,
            total_pages=1,
            total_items=0,
            search_query=search_query,
            start_date=start_date_str,
            end_date=end_date_str,
            quick_range=quick_range,
        )

    # 2. Exclude bot edits
    bot_user = q.user(consts.BOT_USERNAME)
    bot_id = bot_user.id if bot_user else None

    # Base filter for revisions scoped to accessible projects
    rev_filters = [db.Revision.project_id.in_(accessible_project_ids)]
    if bot_id:
        rev_filters.append(db.Revision.author_id != bot_id)
    proj_filters = [db.Project.id.in_(accessible_project_ids)]

    # Date range filters
    if start_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        rev_filters.append(db.Revision.created >= start_dt)
        proj_filters.append(db.Project.created_at >= start_dt)
    if end_date:
        end_dt = datetime.combine(end_date, datetime.max.time())
        rev_filters.append(db.Revision.created <= end_dt)
        proj_filters.append(db.Project.created_at <= end_dt)

    # Search filter
    if search_query:
        search_pattern = f"%{search_query}%"
        rev_filters.append(
            or_(
                db.Revision.summary.ilike(search_pattern),
                db.Revision.project.has(db.Project.display_title.ilike(search_pattern)),
                db.Revision.author.has(db.User.username.ilike(search_pattern)),
            )
        )
        proj_filters.append(
            or_(
                db.Project.display_title.ilike(search_pattern),
                db.Project.author.ilike(search_pattern),
                db.Project.slug.ilike(search_pattern),
            )
        )

    # Counts
    total_revisions = session.query(db.Revision.id).filter(*rev_filters).count()
    total_projects = session.query(db.Project.id).filter(*proj_filters).count()
    total_items = total_revisions + total_projects
    total_pages = max(1, math.ceil(total_items / per_page)) if total_items else 1
    if page > total_pages:
        page = total_pages

    # 3. Efficiently fetch items needed up to the current page with full eager loading
    fetch_limit = page * per_page
    revisions = (
        session.query(db.Revision)
        .options(
            orm.joinedload(db.Revision.author),
            orm.joinedload(db.Revision.project),
            orm.joinedload(db.Revision.page),
            orm.joinedload(db.Revision.status),
        )
        .filter(*rev_filters)
        .order_by(db.Revision.created.desc())
        .limit(fetch_limit)
        .all()
    )

    projects = (
        session.query(db.Project)
        .options(
            orm.joinedload(db.Project.creator),
        )
        .filter(*proj_filters)
        .order_by(db.Project.created_at.desc())
        .limit(fetch_limit)
        .all()
    )

    # Combine into activity list and sort chronologically
    all_activity = [("revision", r.created, r) for r in revisions] + [
        ("project", p.created_at, p) for p in projects
    ]
    all_activity.sort(key=lambda x: x[1], reverse=True)

    # Slice current page
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_activity = all_activity[start_idx:end_idx]

    # 4. Compute diffs ONLY for the revisions on the current page
    page_revisions = [item[2] for item in page_activity if item[0] == "revision"]
    if page_revisions:
        from kalanjiyam.utils.diff import revision_diff
        from kalanjiyam.utils import proofing_utils

        for r in page_revisions:
            cur_text = proofing_utils.revision_plain_content(r)
            prev_r = (
                session.query(db.Revision)
                .filter(
                    db.Revision.page_id == r.page_id,
                    db.Revision.created < r.created
                )
                .order_by(db.Revision.created.desc())
                .first()
            )
            if prev_r:
                r.prev_revision_id = prev_r.id
                prev_text = proofing_utils.revision_plain_content(prev_r)
                r.diff = revision_diff(prev_text, cur_text)
            else:
                r.prev_revision_id = None
                if cur_text:
                    r.diff = revision_diff("", cur_text)
                else:
                    r.diff = None

    return render_template(
        "proofing/recent-changes.html",
        recent_activity=page_activity,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        total_items=total_items,
        search_query=search_query,
        start_date=start_date_str,
        end_date=end_date_str,
        quick_range=quick_range,
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
    system_settings = q.get_system_settings()
    default_trans_engine = (
        getattr(system_settings, "default_translation_engine", "indictrans2")
        or "indictrans2"
    )
    rec_trans_engine = getattr(
        system_settings, "recommended_translation_engine", None
    )
    is_super_admin = getattr(current_user, "is_super_admin", False)

    from kalanjiyam.utils.translation_engine import (
        build_translation_choices,
        normalize_translation_engine,
        get_supported_languages_list,
    )
    from kalanjiyam.tasks.translation import run_docx_translation

    engines = build_translation_choices(
        is_super_admin=is_super_admin,
        recommended_engine=rec_trans_engine,
        default_engine=default_trans_engine,
    )
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
        engine_val = request.form.get("engine", default_trans_engine)
        engine = normalize_translation_engine(engine_val)
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


