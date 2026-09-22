"""Views for basic site pages."""

import json
import math
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import redis
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_babel import lazy_gettext as _l
from flask_login import current_user
from flask_wtf import FlaskForm
from slugify import slugify
from sqlalchemy import and_, func, or_, orm
from wtforms import BooleanField, MultipleFileField, RadioField, StringField
from wtforms.validators import DataRequired, ValidationError
from wtforms.widgets import TextArea

from kalanjiyam import consts
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.tasks import PRIORITY_BATCH, PRIORITY_LOW
from kalanjiyam.tasks import projects as project_tasks
from kalanjiyam.utils import project_utils
from kalanjiyam.utils.quotas import ensure_storage_quota_for_user
from kalanjiyam.views.proofing.decorators import moderator_required

bp = Blueprint("proofing", __name__)


@bp.before_request
def _require_guest_access_or_login():
    if (
        not current_app.config.get("ENABLE_GUEST_ACCESS", True)
        and not current_user.is_authenticated
    ):
        return redirect(url_for("auth.sign_in"))


ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _is_allowed_document_file(filename: str) -> bool:
    """True iff we accept this type of document/image upload."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def _natural_sort_key(s: str):
    """Sort strings with embedded numbers naturally (e.g., page_2 before page_10)."""
    return [
        int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)
    ]


def _filename_to_project_title(filename: str, fallback_index: int = 1) -> str:
    """Convert an image filename into a clean project title."""
    base = re.sub(r"\.[^/.]+$", "", filename.strip())
    clean = re.sub(r"[_\-]+", " ", base).strip()
    if clean:
        return clean[0].upper() + clean[1:]
    if base:
        return base
    return f"Project {fallback_index}"


def _escape_like(value: str) -> str:
    """Escape SQL LIKE wildcard characters (%, _) in a value for safe use in LIKE patterns."""
    return value.replace("%", r"\%").replace("_", r"\_")


def _current_org_id() -> int | None:
    """Return the organization_id for folder scoping based on the current user."""
    from kalanjiyam.utils.org_access import user_organization_id

    if current_user.is_authenticated:
        return user_organization_id(current_user)
    return None


def is_group_images_enabled(request_form) -> bool:
    """Determine whether multiple images should be grouped into one project."""
    if "group_images" in request_form:
        val = str(request_form.get("group_images", "")).lower()
        return val in ("1", "true", "on", "yes", "y")
    if "group_images_submitted" in request_form:
        return False
    return True


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


def _required_if_local_title(message: str):
    def fn(form, field):
        source = form.pdf_source.data
        if source == "local":
            raw_files = request.files.getlist("local_file")
            uploaded = [f for f in raw_files if f and getattr(f, "filename", None)]
            is_multi_image = len(uploaded) > 1 and all(
                Path(f.filename).suffix.lower() in IMAGE_EXTENSIONS for f in uploaded
            )
            is_multi_pdf = len(uploaded) > 1 and all(
                Path(f.filename).suffix.lower() == ".pdf" for f in uploaded
            )
            # If multiple images and group_images is unchecked, or multiple PDFs, title is not required
            group_images = is_group_images_enabled(request.form)
            if (is_multi_image and not group_images) or is_multi_pdf:
                return
            data = field.data
            if not data or not data.strip():
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
            _required_if_local(
                _l("Please provide a document or image file(s) to upload.")
            )
        ],
    )
    local_title = StringField(
        _l("Title of the book (you can change this later)"),
        validators=[
            _required_if_local_title(
                _l("Please provide a title for your document or images."),
            )
        ],
    )
    folder = StringField(
        _l("Folder (optional)"),
        render_kw={
            "placeholder": _l("e.g. Literature/Poetry or Philosophy"),
        },
    )
    tags = StringField(
        _l("Tags (optional)"),
        render_kw={
            "placeholder": _l("Comma-separated tags, e.g. Sanskrit, Manuscript"),
        },
    )
    group_images = BooleanField(
        _l("Group into one project"),
        default=True,
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
    selected_folder = (request.args.get("folder", "")).strip()
    selected_tag = (request.args.get("tag", "")).strip()

    # Parse single or multiple condition issue filters
    raw_issues = request.args.getlist("issue")
    if not raw_issues:
        raw_issue_str = request.args.get("issue", "")
        if raw_issue_str:
            raw_issues = [s.strip() for s in raw_issue_str.split(",") if s.strip()]
    selected_issues = [
        i.strip() for i in raw_issues if i and i.strip() and i.strip() != "all"
    ]

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

    # 3. Base query filtered by user tenant permissions in SQL
    device_fp = request.cookies.get("device_fingerprint")
    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )
    has_any_projects = base_query.first() is not None

    # Collect all available condition tags across accessible projects for the filter UI
    available_condition_tags = set()
    tag_rows = (
        base_query.with_entities(db.Project.condition_tags)
        .filter(db.Project.condition_tags.isnot(None))
        .all()
    )
    for (c_tags,) in tag_rows:
        if c_tags and isinstance(c_tags, list):
            for t in c_tags:
                t_name = (
                    t.get("name")
                    if isinstance(t, dict)
                    else (str(t) if isinstance(t, str) else "")
                )
                if t_name and t_name.strip():
                    available_condition_tags.add(t_name.strip())
    available_condition_tags = sorted(available_condition_tags, key=lambda s: s.lower())

    # Collect available folders and project tags across accessible projects
    target_org_id = None
    if selected_org and selected_org != "all":
        target_group = session.query(db.Group).filter_by(slug=selected_org).first()
        if target_group:
            target_org_id = target_group.id
        elif not getattr(current_user, "is_super_admin", False):
            target_org_id = -1
    elif not getattr(current_user, "is_super_admin", False):
        target_org_id = _current_org_id()

    available_folders = project_utils.get_all_available_folders(
        session,
        base_query=base_query,
        organization_id=target_org_id,
        creator_id=current_user.id if current_user.is_authenticated else None,
        fingerprint_id=device_fp if not current_user.is_authenticated else None,
        is_super_admin=getattr(current_user, "is_super_admin", False) and (not selected_org or selected_org == "all"),
    )
    has_any_folders = len(available_folders) > 0
    has_any_items = has_any_projects or has_any_folders

    available_project_tags = set()
    tag_rows_proj = (
        base_query.with_entities(db.Project.tags)
        .filter(db.Project.tags.isnot(None))
        .all()
    )
    for (t_val,) in tag_rows_proj:
        for t_item in project_utils.normalize_tags(t_val):
            available_project_tags.add(t_item)
    available_project_tags = sorted(available_project_tags, key=lambda s: s.lower())

    query = base_query

    # 4. Filter by organization if specified
    if selected_org and selected_org != "all":
        query = query.filter(db.Project.groups.any(db.Group.slug == selected_org))

    # 5. Filter by creator mode if specified
    if selected_mode and selected_mode != "all":
        query = query.filter(db.Project.creator_mode == selected_mode)

    # 5b. Filter by folder if specified
    if selected_folder:
        query = query.filter(
            or_(
                db.Project.folder == selected_folder,
                db.Project.folder.like(f"{selected_folder}/%"),
            )
        )

    # 6. Full tenant search filtering (matching display_title, print_title, author, or slug)
    if search_query:
        like_pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                db.Project.display_title.ilike(like_pattern),
                db.Project.print_title.ilike(like_pattern),
                db.Project.author.ilike(like_pattern),
                db.Project.slug.ilike(like_pattern),
            )
        )

    # 7. Server-side sorting
    if sort_field == "title":
        order_col = func.lower(func.coalesce(db.Project.display_title, ""))
    else:
        # Default sort by created_at date
        order_col = db.Project.created_at

    if sort_order == "desc":
        query = query.order_by(order_col.desc(), db.Project.id.desc())
    else:
        query = query.order_by(order_col.asc(), db.Project.id.asc())

    # 8. Server-side pagination and filtering
    if selected_issues or selected_tag:
        candidates = (
            query.options(orm.selectinload(db.Project.groups))
            .all()
        )
        filtered_projects = candidates
        if selected_issues:
            selected_issues_lower = {i.lower() for i in selected_issues}
            filtered_projects = [
                p
                for p in filtered_projects
                if any(
                    (tag.get("name", "").lower() in selected_issues_lower)
                    for tag in (p.condition_tag_list or [])
                )
            ]
        if selected_tag:
            sel_tag_lower = selected_tag.lower()
            filtered_projects = [
                p
                for p in filtered_projects
                if any(t.lower() == sel_tag_lower for t in (p.tag_list or []))
            ]
        total_projects = len(filtered_projects)
        total_pages = (
            max(1, math.ceil(total_projects / per_page)) if total_projects else 1
        )
        if page > total_pages:
            page = total_pages
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_projects = filtered_projects[start_idx:end_idx]
    else:
        total_projects = query.count()
        total_pages = (
            max(1, math.ceil(total_projects / per_page)) if total_projects else 1
        )
        if page > total_pages:
            page = total_pages
        start_idx = (page - 1) * per_page
        paginated_projects = (
            query.options(orm.selectinload(db.Project.groups))
            .offset(start_idx)
            .limit(per_page)
            .all()
        )

    # Build folder contents tree for Folder View
    folder_scope_query = base_query
    if selected_org and selected_org != "all":
        folder_scope_query = folder_scope_query.filter(
            db.Project.groups.any(db.Group.slug == selected_org)
        )
    if selected_mode and selected_mode != "all":
        folder_scope_query = folder_scope_query.filter(
            db.Project.creator_mode == selected_mode
        )
    if search_query:
        like_pattern = f"%{search_query}%"
        folder_scope_query = folder_scope_query.filter(
            or_(
                db.Project.display_title.ilike(like_pattern),
                db.Project.print_title.ilike(like_pattern),
                db.Project.author.ilike(like_pattern),
                db.Project.slug.ilike(like_pattern),
            )
        )
    all_scope_projects = folder_scope_query.options(
        orm.selectinload(db.Project.groups)
    ).all()
    if selected_tag:
        sel_tag_lower = selected_tag.lower()
        all_scope_projects = [
            p
            for p in all_scope_projects
            if any(t.lower() == sel_tag_lower for t in (p.tag_list or []))
        ]

    folder_contents = project_utils.get_folder_contents(
        all_scope_projects,
        current_folder=selected_folder,
        all_known_folders=available_folders,
    )

    all_display_projects = list(paginated_projects)
    for dp in folder_contents["direct_projects"]:
        if dp.id not in [p.id for p in all_display_projects]:
            all_display_projects.append(dp)

    all_display_project_ids = [p.id for p in all_display_projects]
    if all_display_project_ids:
        session.query(db.Project).options(
            orm.selectinload(db.Project.pages).joinedload(db.Page.status)
        ).filter(db.Project.id.in_(all_display_project_ids)).all()

    status_classes = {
        SitePageStatus.R2: "bg-green-200",
        SitePageStatus.R1: "bg-yellow-200",
        SitePageStatus.R0: "bg-red-300",
        SitePageStatus.SKIP: "bg-slate-100",
    }

    # 9. Initialize Redis connection safely
    r_client = None
    try:
        r_client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0")
        )
        r_client.ping()
    except Exception:
        r_client = None

    statuses_per_project = {}
    progress_per_project = {}
    pages_per_project = {}

    for project in all_display_projects:
        updated_ts = (
            int(project.updated_at.timestamp())
            if getattr(project, "updated_at", None)
            else 0
        )
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
        "selected_folder": selected_folder,
        "selected_tag": selected_tag,
        "available_condition_tags": available_condition_tags,
        "available_folders": available_folders,
        "available_tags": available_project_tags,
        "user_organizations": user_organizations,
        "sort_field": sort_field,
        "sort_order": sort_order,
        "has_any_projects": has_any_projects,
        "has_any_folders": has_any_folders,
        "has_any_items": has_any_items,
        "folder_contents": folder_contents,
    }

    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.args.get("ajax") == "1"
    )
    if is_ajax:
        rendered = render_template("proofing/_projects_list.html", **template_kwargs)
        resp = make_response(rendered)
        resp.headers["X-Total-Projects"] = str(total_projects)
        return resp

    return render_template("proofing/index.html", **template_kwargs)


@bp.route("/folders/create", methods=["POST"])
def create_folder():
    """Create a new proofing folder."""
    data = request.get_json(silent=True) or request.form
    raw_name = (data.get("name") or data.get("folder_name") or "").strip()
    raw_parent = (data.get("parent_folder") or data.get("parent") or "").strip()

    if not raw_name:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Folder name is required."}), 400
        flash(_l("Folder name is required."), "danger")
        return redirect(url_for("proofing.index"))

    clean_name = raw_name.strip().strip("/")
    if raw_parent:
        full_path = project_utils.normalize_folder_path(f"{raw_parent}/{clean_name}")
    else:
        full_path = project_utils.normalize_folder_path(clean_name)

    if not full_path:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Invalid folder name."}), 400
        flash(_l("Invalid folder name."), "danger")
        return redirect(url_for("proofing.index"))

    org_id = _current_org_id()
    session = q.get_session()
    existing = session.query(db.ProofFolder).filter_by(
        path=full_path, organization_id=org_id
    ).first()
    if existing is not None:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": _l("A folder with this name already exists.")}), 400
        flash(_l("A folder with this name already exists."), "danger")
        return redirect(url_for("proofing.index", folder=full_path))

    creator_id = current_user.id if current_user.is_authenticated else None
    fingerprint_id = (
        request.cookies.get("device_fingerprint")
        if not current_user.is_authenticated
        else None
    )

    project_utils.ensure_proof_folder(
        session,
        full_path,
        creator_id=creator_id,
        fingerprint_id=fingerprint_id,
        organization_id=org_id,
    )
    session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({
            "success": True,
            "folder": full_path,
            "path": full_path,
            "name": full_path.split("/")[-1],
            "parent_folder": raw_parent,
        })

    flash(_l("Folder created successfully."), "success")
    return redirect(url_for("proofing.index", folder=full_path))


@bp.route("/folders/rename", methods=["POST"])
def rename_folder():
    """Rename an existing folder."""
    data = request.get_json(silent=True) or request.form
    old_path = project_utils.normalize_folder_path(data.get("old_path", ""))
    new_name = (data.get("new_name") or "").strip().strip("/")

    if not old_path or not new_name:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Old path and new name are required."}), 400
        flash(_l("Old path and new name are required."), "danger")
        return redirect(url_for("proofing.index"))

    # Reject slashes in new_name to prevent creating nested paths
    if "/" in new_name or "\\" in new_name:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Folder name cannot contain slashes."}), 400
        flash(_l("Folder name cannot contain slashes."), "danger")
        return redirect(url_for("proofing.index"))

    old_parts = old_path.split("/")
    parent_path = "/".join(old_parts[:-1])
    new_path = f"{parent_path}/{new_name}" if parent_path else new_name
    new_path = project_utils.normalize_folder_path(new_path)

    org_id = _current_org_id()
    session = q.get_session()

    # Check for collision with existing folder in same organization
    if new_path != old_path:
        existing = session.query(db.ProofFolder).filter_by(
            path=new_path, organization_id=org_id
        ).first()
        if existing is not None:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
                return jsonify({"success": False, "error": _l("A folder with this name already exists.")}), 400
            flash(_l("A folder with this name already exists."), "danger")
            return redirect(url_for("proofing.index"))

    # Update ProofFolder records (escape LIKE wildcards in old_path)
    escaped_old = _escape_like(old_path)
    folder_filter = or_(
        db.ProofFolder.path == old_path,
        db.ProofFolder.path.like(f"{escaped_old}/%", escape="\\"),
    )
    if org_id is not None:
        folder_filter = and_(folder_filter, db.ProofFolder.organization_id == org_id)
    elif not getattr(current_user, "is_super_admin", False):
        device_fp = request.cookies.get("device_fingerprint")
        if current_user.is_authenticated:
            folder_filter = and_(folder_filter, db.ProofFolder.creator_id == current_user.id)
        elif device_fp:
            folder_filter = and_(
                folder_filter,
                db.ProofFolder.fingerprint_id == device_fp,
                db.ProofFolder.organization_id.is_(None),
            )

    folders = session.query(db.ProofFolder).filter(folder_filter).all()
    for f in folders:
        if f.path == old_path:
            f.path = new_path
            f.name = new_name
            f.parent_path = parent_path
        elif f.path.startswith(old_path + "/"):
            remainder = f.path[len(old_path) + 1 :]
            f.path = f"{new_path}/{remainder}"
            f_parts = f.path.split("/")
            f.name = f_parts[-1]
            f.parent_path = "/".join(f_parts[:-1])

    # Update Project records (scoped to user's accessible projects)
    device_fp = request.cookies.get("device_fingerprint")
    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )
    projects = base_query.filter(
        or_(
            db.Project.folder == old_path,
            db.Project.folder.like(f"{escaped_old}/%", escape="\\"),
        )
    ).all()
    for p in projects:
        if p.folder == old_path:
            p.folder = new_path
        elif p.folder and p.folder.startswith(old_path + "/"):
            remainder = p.folder[len(old_path) + 1 :]
            p.folder = f"{new_path}/{remainder}"

    session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({
            "success": True,
            "old_path": old_path,
            "new_path": new_path,
            "parent_folder": parent_path,
        })

    flash(_l("Folder renamed successfully."), "success")
    return redirect(url_for("proofing.index", folder=new_path))


@bp.route("/folders/delete", methods=["POST"])
def delete_folder():
    """Delete a folder. Projects inside are safely moved to its parent (or root)."""
    data = request.get_json(silent=True) or request.form
    target_path = project_utils.normalize_folder_path(
        data.get("path") or data.get("folder_path") or ""
    )

    if not target_path:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"success": False, "error": "Folder path is required."}), 400
        flash(_l("Folder path is required."), "danger")
        return redirect(url_for("proofing.index"))

    parts = target_path.split("/")
    parent_path = "/".join(parts[:-1])

    org_id = _current_org_id()
    session = q.get_session()
    escaped_target = _escape_like(target_path)
    folder_filter = or_(
        db.ProofFolder.path == target_path,
        db.ProofFolder.path.like(f"{escaped_target}/%", escape="\\"),
    )
    if org_id is not None:
        folder_filter = and_(folder_filter, db.ProofFolder.organization_id == org_id)
    elif not getattr(current_user, "is_super_admin", False):
        device_fp = request.cookies.get("device_fingerprint")
        if current_user.is_authenticated:
            folder_filter = and_(folder_filter, db.ProofFolder.creator_id == current_user.id)
        elif device_fp:
            folder_filter = and_(
                folder_filter,
                db.ProofFolder.fingerprint_id == device_fp,
                db.ProofFolder.organization_id.is_(None),
            )

    session.query(db.ProofFolder).filter(folder_filter).delete(synchronize_session=False)

    # Safely move projects to parent_path (scoped to user's accessible projects)
    device_fp = request.cookies.get("device_fingerprint")
    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )
    projects = base_query.filter(
        or_(
            db.Project.folder == target_path,
            db.Project.folder.like(f"{escaped_target}/%", escape="\\"),
        )
    ).all()
    for p in projects:
        p.folder = parent_path

    session.commit()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({
            "success": True,
            "folder": target_path,
            "path": target_path,
            "parent_folder": parent_path,
        })

    flash(_l("Folder deleted successfully."), "success")
    return redirect(url_for("proofing.index", folder=parent_path))


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
    if (
        not current_app.config.get("ENABLE_GUEST_ACCESS", True)
        and not current_user.is_authenticated
    ):
        flash(
            _l(
                "Guest project creation is disabled. Please log in to create a project."
            ),
            "warning",
        )
        return redirect(url_for("auth.sign_in"))

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
            is_open_tenant = user_organization_id(current_user) == open_tenant.id
        except Exception:
            pass

    allowed = (
        not current_user.is_authenticated  # Guest
        or (
            current_user.is_authenticated and is_open_tenant
        )  # Registered in open-tenant
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
        getattr(system_settings, "default_translation_engine", "indictrans3")
        or "indictrans3"
    )
    rec_trans_engine = getattr(system_settings, "recommended_translation_engine", None)
    is_super_admin = getattr(current_user, "is_super_admin", False)

    from kalanjiyam.utils.translation_engine import (
        build_translation_choices,
        get_supported_languages_list,
        normalize_translation_engine,
    )

    engines = build_translation_choices(
        is_super_admin=is_super_admin,
        recommended_engine=rec_trans_engine,
        default_engine=default_trans_engine,
    )
    languages = get_supported_languages_list()

    form = CreateProjectForm()
    if request.method == "GET" and request.args.get("folder"):
        form.folder.data = request.args.get("folder").strip()

    device_fp = request.cookies.get("device_fingerprint")
    base_query = q.accessible_proofing_projects_query(
        current_user, session=session, device_fingerprint=device_fp
    )
    available_folders = project_utils.get_all_available_folders(
        session,
        base_query=base_query,
        organization_id=_current_org_id(),
        creator_id=current_user.id if current_user.is_authenticated else None,
        fingerprint_id=device_fp if not current_user.is_authenticated else None,
        is_super_admin=getattr(current_user, "is_super_admin", False),
    )

    def _render_create_project():
        return render_template(
            "proofing/create-project.html",
            form=form,
            guest_upload_limit=guest_upload_limit,
            engines=engines,
            languages=languages,
            user_organizations=user_organizations,
            available_folders=available_folders,
        )

    if request.method == "POST" and request.form.get("docx_workflow") == "direct":
        import json
        import os
        import uuid

        import redis

        from kalanjiyam.tasks.translation import run_docx_translation

        file = request.files.get("local_file")
        if not file or not file.filename:
            flash(_l("Please upload a file."), "error")
            return _render_create_project()

        filename = file.filename
        if Path(filename).suffix not in (".docx", ".doc"):
            flash(_l("Please upload a Word document (.docx)."), "error")
            return _render_create_project()

        source_lang = request.form.get("source_lang", "sa")
        target_lang = request.form.get("target_lang", "en")
        engine_val = request.form.get("engine", default_trans_engine)
        engine = normalize_translation_engine(engine_val)
        glossary = request.form.get("glossary") or None

        # Validate engine
        from kalanjiyam.utils.translation_engine import TranslationEngineFactory

        if not TranslationEngineFactory.is_supported(engine):
            flash(_l("Unsupported translation engine selected."), "error")
            return _render_create_project()

        docx_id = str(uuid.uuid4())
        from kalanjiyam.utils.storage import docx_upload_key, get_storage

        storage = get_storage()
        storage.save(docx_upload_key(docx_id), file.stream)

        # Store docx original filename and parameters in Redis
        r_client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0")
        )
        r_client.setex(
            f"docx_info:{docx_id}",
            86400,
            json.dumps(
                {
                    "original_filename": filename,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "engine": engine,
                    "glossary": glossary,
                }
            ),
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
                extra_info={"docx_id": docx_id, "glossary": glossary},
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
            if (
                current_app.config.get("DEFAULT_PROJECT_REQUIRES_ORG", True)
                and not getattr(current_user, "organization_id", None)
                and not user_organizations
            ):
                flash(_l("Your account is not assigned to an organization."), "error")
                return _render_create_project()
        selected_org_slug = request.form.get("selected_org_slug")
        org_slug = "open-tenant"
        if current_user.is_authenticated:
            user_org_map = (
                {g.slug: g for g in user_organizations} if user_organizations else {}
            )
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
            return _render_create_project()

        for f in uploaded_files:
            if not _is_allowed_document_file(f.filename):
                flash(
                    _l(
                        "Unsupported file type: %(filename)s. Please upload a PDF, DOCX, or JPG/PNG image(s).",
                        filename=f.filename,
                    ),
                    "error",
                )
                return _render_create_project()

        is_multiple = len(uploaded_files) > 1
        all_are_images = all(
            Path(f.filename).suffix.lower() in IMAGE_EXTENSIONS for f in uploaded_files
        )
        all_are_pdfs = all(
            Path(f.filename).suffix.lower() == ".pdf" for f in uploaded_files
        )

        if is_multiple and not all_are_images and not all_are_pdfs:
            flash(
                _l(
                    "When uploading multiple files, all files must be either all images (.jpg, .jpeg, .png, .webp) or all PDFs (.pdf)."
                ),
                "error",
            )
            return _render_create_project()

        is_image_upload = all_are_images
        is_batch_pdf_upload = is_multiple and all_are_pdfs
        first_filename = uploaded_files[0].filename
        is_uploaded_docx = (
            (not is_image_upload)
            and (not is_batch_pdf_upload)
            and Path(first_filename).suffix.lower() in (".docx", ".doc")
        )

        group_images = (
            is_group_images_enabled(request.form)
            if (is_multiple and is_image_upload)
            else True
        )
        is_batch_mode = (is_image_upload and not group_images) or is_batch_pdf_upload

        title = None
        slug = None
        batch_projects_preview = []

        if not is_batch_mode:
            title = form.local_title.data
            slug = slugify(title)
            # Check DB before writing files to storage to prevent overwriting existing project files
            existing_proj = session.query(db.Project).filter_by(slug=slug).first()
            if existing_proj:
                flash(
                    _l(
                        'Project "%(title)s" already exists. Please choose a different title.',
                        title=title,
                    ),
                    "error",
                )
                return _render_create_project()
        else:
            conflicts = []
            seen_slugs = set()
            for idx, f in enumerate(uploaded_files, start=1):
                p_title = _filename_to_project_title(f.filename, fallback_index=idx)
                p_slug = slugify(p_title) or f"project-{idx}"
                if p_slug in seen_slugs:
                    conflicts.append(f'"{p_title}" (duplicate in upload)')
                else:
                    seen_slugs.add(p_slug)
                    if session.query(db.Project).filter_by(slug=p_slug).first():
                        conflicts.append(f'"{p_title}" (already exists in database)')
                batch_projects_preview.append((p_title, p_slug, f))

            if conflicts:
                flash(
                    _l(
                        "Cannot create projects due to naming conflicts: %(conflicts)s. Please rename the files.",
                        conflicts=", ".join(conflicts),
                    ),
                    "error",
                )
                return _render_create_project()

            if not current_user.is_authenticated:
                from datetime import datetime, timedelta

                cutoff = datetime.utcnow() - timedelta(seconds=86400)
                existing_count = (
                    session.query(db.UsageLog)
                    .filter(
                        db.UsageLog.action == "create_project",
                        db.UsageLog.created_at >= cutoff,
                        (db.UsageLog.ip_address == request.remote_addr)
                        | (
                            db.UsageLog.fingerprint_id
                            == request.cookies.get("device_fingerprint")
                        ),
                    )
                    .count()
                )
                limit = settings.unregistered_user_project_limit
                if existing_count + len(uploaded_files) > limit:
                    flash(
                        _l(
                            "Creating %(count)s projects would exceed your limit (%(remaining)s remaining today).",
                            count=len(uploaded_files),
                            remaining=max(0, limit - existing_count),
                        ),
                        "error",
                    )
                    return render_template(
                        "proofing/create-project.html",
                        form=form,
                        guest_upload_limit=guest_upload_limit,
                        engines=engines,
                        languages=languages,
                        user_organizations=user_organizations,
                    )

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
                return render_template(
                    "proofing/create-project.html",
                    form=form,
                    guest_upload_limit=guest_upload_limit,
                    engines=engines,
                    languages=languages,
                    user_organizations=user_organizations,
                )

        # Save the original file so that it can be processed/downloaded later.
        from kalanjiyam.utils.storage import (
            get_storage,
            pdf_key,
            project_docx_key,
            project_raw_image_key,
        )

        source_pdf_key = None
        source_docx_key = None
        image_keys = None
        batch_projects_data = None
        batch_pdf_projects_data = None

        if is_uploaded_docx:
            source_docx_key = project_docx_key(slug, org_slug=org_slug)
            uploaded_files[0].stream.seek(0)
            get_storage().save(source_docx_key, uploaded_files[0].stream)
        elif is_image_upload:
            if group_images:
                sorted_images = sorted(
                    uploaded_files, key=lambda f: _natural_sort_key(f.filename)
                )
                image_keys = []
                for idx, img_file in enumerate(sorted_images, start=1):
                    ext = Path(img_file.filename).suffix.lower() or ".jpg"
                    staged_name = f"{idx}{ext}"
                    img_key = project_raw_image_key(
                        slug, staged_name, org_slug=org_slug
                    )
                    img_file.stream.seek(0)
                    get_storage().save(img_key, img_file.stream)
                    image_keys.append(img_key)
            else:
                batch_projects_data = []
                for p_title, p_slug, img_file in batch_projects_preview:
                    ext = Path(img_file.filename).suffix.lower() or ".jpg"
                    staged_name = f"1{ext}"
                    img_key = project_raw_image_key(
                        p_slug, staged_name, org_slug=org_slug
                    )
                    img_file.stream.seek(0)
                    get_storage().save(img_key, img_file.stream)
                    batch_projects_data.append(
                        {
                            "display_title": p_title,
                            "slug": p_slug,
                            "image_keys": [img_key],
                        }
                    )
        elif is_batch_pdf_upload:
            batch_pdf_projects_data = []
            for p_title, p_slug, pdf_file in batch_projects_preview:
                source_key = pdf_key(p_slug, org_slug=org_slug)
                pdf_file.stream.seek(0)
                get_storage().save(source_key, pdf_file.stream)
                batch_pdf_projects_data.append(
                    {
                        "display_title": p_title,
                        "slug": p_slug,
                        "pdf_key": source_key,
                    }
                )
        else:
            source_pdf_key = pdf_key(slug, org_slug=org_slug)
            uploaded_files[0].stream.seek(0)
            get_storage().save(source_pdf_key, uploaded_files[0].stream)

        # Log usage action for guests
        if not current_user.is_authenticated:
            from kalanjiyam.utils.rate_limit import log_usage_action

            if is_image_upload and not group_images:
                for item in batch_projects_data:
                    log_usage_action(
                        action="create_project",
                        ip_address=request.remote_addr,
                        fingerprint_id=request.cookies.get("device_fingerprint"),
                        project_slug=item["slug"],
                    )
            elif is_batch_pdf_upload:
                for item in batch_pdf_projects_data:
                    log_usage_action(
                        action="create_project",
                        ip_address=request.remote_addr,
                        fingerprint_id=request.cookies.get("device_fingerprint"),
                        project_slug=item["slug"],
                    )
            else:
                log_usage_action(
                    action="create_project",
                    ip_address=request.remote_addr,
                    fingerprint_id=request.cookies.get("device_fingerprint"),
                    project_slug=slug,
                )

        from kalanjiyam.utils.project_utils import normalize_folder_path, normalize_tags
        folder_val = normalize_folder_path(form.folder.data)
        tags_val = normalize_tags(form.tags.data)

        if folder_val:
            project_utils.ensure_proof_folder(
                session,
                folder_val,
                creator_id=current_user.id if current_user.is_authenticated else None,
                fingerprint_id=(
                    request.cookies.get("device_fingerprint")
                    if not current_user.is_authenticated
                    else None
                ),
                organization_id=_current_org_id(),
            )
            session.commit()

        if is_batch_pdf_upload:
            if not current_user.is_authenticated:
                task = project_tasks.create_batch_pdf_projects.apply_async(
                    kwargs={
                        "projects_data": batch_pdf_projects_data,
                        "app_environment": current_app.config["KALANJIYAM_ENVIRONMENT"],
                        "creator_id": None,
                        "fingerprint_id": request.cookies.get("device_fingerprint"),
                        "org_slug": org_slug,
                        "folder": folder_val,
                        "tags": tags_val,
                    },
                    queue="low_priority",
                    priority=PRIORITY_LOW,
                )
            else:
                task = project_tasks.create_batch_pdf_projects.apply_async(
                    kwargs={
                        "projects_data": batch_pdf_projects_data,
                        "app_environment": current_app.config["KALANJIYAM_ENVIRONMENT"],
                        "creator_id": current_user.id,
                        "org_slug": org_slug,
                        "folder": folder_val,
                        "tags": tags_val,
                    },
                    priority=PRIORITY_BATCH,
                )
        elif is_image_upload and not group_images:
            if not current_user.is_authenticated:
                task = project_tasks.create_batch_image_projects.apply_async(
                    kwargs={
                        "projects_data": batch_projects_data,
                        "app_environment": current_app.config["KALANJIYAM_ENVIRONMENT"],
                        "creator_id": None,
                        "fingerprint_id": request.cookies.get("device_fingerprint"),
                        "org_slug": org_slug,
                        "folder": folder_val,
                        "tags": tags_val,
                    },
                    queue="low_priority",
                    priority=PRIORITY_LOW,
                )
            else:
                task = project_tasks.create_batch_image_projects.apply_async(
                    kwargs={
                        "projects_data": batch_projects_data,
                        "app_environment": current_app.config["KALANJIYAM_ENVIRONMENT"],
                        "creator_id": current_user.id,
                        "org_slug": org_slug,
                        "folder": folder_val,
                        "tags": tags_val,
                    },
                    priority=PRIORITY_BATCH,
                )
        else:
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
                        "folder": folder_val,
                        "tags": tags_val,
                    },
                    queue="low_priority",
                    priority=PRIORITY_LOW,
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
                    folder=folder_val,
                    tags=tags_val,
                )

        from kalanjiyam.utils.user_tasks import add_user_task, get_user_identifier

        user_id = get_user_identifier(current_user, request)
        if user_id:
            if is_batch_pdf_upload:
                add_user_task(
                    user_identifier=user_id,
                    task_id=task.id,
                    task_type="create_project",
                    project_slug="",
                    project_title=f"{len(batch_pdf_projects_data)} PDF Projects",
                    extra_info={"total_projects": len(batch_pdf_projects_data)},
                )
            elif is_image_upload and not group_images:
                add_user_task(
                    user_identifier=user_id,
                    task_id=task.id,
                    task_type="create_project",
                    project_slug="",
                    project_title=f"{len(batch_projects_data)} Projects",
                    extra_info={"total_projects": len(batch_projects_data)},
                )
            else:
                add_user_task(
                    user_identifier=user_id,
                    task_id=task.id,
                    task_type="create_project",
                    project_slug=slug,
                    project_title=title,
                )

        if is_batch_pdf_upload:
            doc_type = "batch_pdfs"
            total_count = len(batch_pdf_projects_data)
        elif is_image_upload and not group_images:
            doc_type = "batch_images"
            total_count = len(batch_projects_data)
        elif is_image_upload:
            doc_type = "images"
            total_count = 0
        elif is_uploaded_docx:
            doc_type = "docx"
            total_count = 0
        else:
            doc_type = "pdf"
            total_count = 0

        return render_template(
            "proofing/create-project-post.html",
            status=task.status,
            current=0,
            total=total_count,
            percent=0,
            task_id=task.id,
            doc_type=doc_type,
        )

    return _render_create_project()


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
    elif r.status == "FAILURE":
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
    from datetime import datetime, timedelta

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
        session.query(db.Project).options(orm.selectinload(db.Project.groups)).all()
    )
    accessible_projects = [
        p for p in all_projects if q.user_can_view_proofing_project(current_user, p)
    ]
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
        from kalanjiyam.utils import proofing_utils
        from kalanjiyam.utils.diff import revision_diff

        for r in page_revisions:
            cur_text = proofing_utils.revision_plain_content(r)
            prev_r = (
                session.query(db.Revision)
                .filter(
                    db.Revision.page_id == r.page_id, db.Revision.created < r.created
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

    context = dict(
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

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        resp = make_response(
            render_template("proofing/_recent_changes_list.html", **context)
        )
        resp.headers["X-Total-Items"] = str(total_items)
        resp.headers["X-Total-Pages"] = str(total_pages)
        return resp

    return render_template("proofing/recent-changes.html", **context)


@bp.route("/talk")
def talk():
    """Show discussion across all projects."""
    projects = [
        p for p in q.projects() if q.user_can_view_proofing_project(current_user, p)
    ]

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

    num_contributors_30d = len(
        {x.author_id for x in revisions_30d if x.author_id is not None}
    )
    num_contributors_7d = len(
        {x.author_id for x in revisions_7d if x.author_id is not None}
    )
    num_contributors_1d = len(
        {x.author_id for x in revisions_1d if x.author_id is not None}
    )

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
    from kalanjiyam.utils.user_tasks import get_user_identifier, get_user_tasks

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
    import json
    import os
    import uuid

    import redis

    system_settings = q.get_system_settings()
    default_trans_engine = (
        getattr(system_settings, "default_translation_engine", "indictrans3")
        or "indictrans3"
    )
    rec_trans_engine = getattr(system_settings, "recommended_translation_engine", None)
    is_super_admin = getattr(current_user, "is_super_admin", False)

    from kalanjiyam.tasks.translation import run_docx_translation
    from kalanjiyam.utils.translation_engine import (
        build_translation_choices,
        get_supported_languages_list,
        normalize_translation_engine,
    )

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
            return render_template(
                "proofing/docx-translate.html", engines=engines, languages=languages
            )

        filename = file.filename
        if Path(filename).suffix not in (".docx", ".doc"):
            flash(_l("Please upload a Word document (.docx)."), "error")
            return render_template(
                "proofing/docx-translate.html", engines=engines, languages=languages
            )

        source_lang = request.form.get("source_lang", "sa")
        target_lang = request.form.get("target_lang", "en")
        engine_val = request.form.get("engine", default_trans_engine)
        engine = normalize_translation_engine(engine_val)
        glossary = request.form.get("glossary") or None

        # Validate engine
        from kalanjiyam.utils.translation_engine import TranslationEngineFactory

        if not TranslationEngineFactory.is_supported(engine):
            flash(_l("Unsupported translation engine selected."), "error")
            return render_template(
                "proofing/docx-translate.html", engines=engines, languages=languages
            )

        docx_id = str(uuid.uuid4())
        from kalanjiyam.utils.storage import docx_upload_key, get_storage

        storage = get_storage()
        storage.save(docx_upload_key(docx_id), file.stream)

        # Store docx original filename and parameters in Redis
        r_client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0")
        )
        r_client.setex(
            f"docx_info:{docx_id}",
            86400,
            json.dumps(
                {
                    "original_filename": filename,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "engine": engine,
                    "glossary": glossary,
                }
            ),
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
                extra_info={"docx_id": docx_id, "glossary": glossary},
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

    return render_template(
        "proofing/docx-translate.html", engines=engines, languages=languages
    )


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
    elif r.status == "FAILURE":
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
        "error": error,
    }


@bp.route("/translate/docx/download/<docx_id>")
def docx_translate_download(docx_id):
    import json
    import os

    import redis
    from flask import abort

    from kalanjiyam.utils.storage import docx_translation_key, get_storage

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
