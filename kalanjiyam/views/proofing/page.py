"""Routes related to project pages.

The main route here is `edit`, which defines the page editor and the edit flow.
"""

import logging
import uuid
from dataclasses import dataclass

from flask import (
    Blueprint,
    flash,
    jsonify,
    make_response,
    render_template,
    request,
    url_for,
)
from flask_babel import lazy_gettext as _l
from flask_login import current_user, login_required
from kalanjiyam.views.proofing.decorators import p2_required
from flask_wtf import FlaskForm
from werkzeug.exceptions import abort
from werkzeug.utils import secure_filename
from wtforms import HiddenField, RadioField, StringField
from wtforms.validators import DataRequired
from wtforms.widgets import TextArea

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.utils import project_utils
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.utils.diff import revision_diff
from kalanjiyam.utils.ocr_persist import apply_ocr_to_page, ocr_response_to_api_dict
from kalanjiyam.utils.page_document import PageDocument, document_for_revision
from kalanjiyam.utils.quotas import (
    add_storage_usage_for_project,
    consume_ocr_credit_for_project,
    ensure_ocr_quota_for_project,
    ensure_storage_quota_for_user,
)
from kalanjiyam.utils.revisions import EditError, add_revision, parse_document_field
from kalanjiyam.views.api import bp as api

bp = Blueprint("page", __name__)


@bp.before_request
def _enforce_project_access():
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description="Superadmins are not allowed to view project data.")
    project_slug = request.view_args.get("project_slug") if request.view_args else None
    if not project_slug:
        return None
    project_ = q.project(project_slug)
    if project_ is None:
        return None
    if not q.user_can_view_proofing_project(current_user, project_):
        abort(403)
    return None


@dataclass
class PageContext:
    """A page, its project, and some navigation data."""

    #: The current project.
    project: db.Project
    #: The current page.
    cur: db.Page
    #: The page before `cur`, if it exists.
    prev: db.Page | None
    #: The page after `cur`, if it exists.
    next: db.Page | None


class EditPageForm(FlaskForm):
    #: An optional summary that describes the revision.
    summary = StringField(_l("Edit summary (optional)"))
    #: The page version. Versions are monotonically increasing: if A < B, then
    #: A is older than B.
    version = HiddenField(_l("Page version"))
    #: The page content (derived plain text).
    content = StringField(
        _l("Page content"), widget=TextArea(), validators=[DataRequired()]
    )
    #: Canonical PageDocument JSON from the block editor.
    document = HiddenField(_l("Page document"))
    #: The page status.
    status = RadioField(
        _l("Status"),
        choices=[
            (SitePageStatus.R0.value, _l("Needs more work")),
            (SitePageStatus.R1.value, _l("Proofed once")),
            (SitePageStatus.R2.value, _l("Proofed twice")),
            (SitePageStatus.SKIP.value, _l("Not relevant")),
        ],
    )


def _get_page_context(project_slug: str, page_slug: str) -> PageContext | None:
    """Get the previous, current, and next pages for the given project.

    :param project_slug: slug for the current project
    :param page_slug: slug for a page within the current project.
    :return: a `PageContext` if the project and page can be found, else ``None``.
    """
    project_ = q.project(project_slug)
    if project_ is None:
        return None

    pages = project_.pages
    found = False
    i = 0
    for i, s in enumerate(pages):
        if s.slug == page_slug:
            found = True
            break

    if not found:
        return None

    prev = pages[i - 1] if i > 0 else None
    cur = pages[i]
    next = pages[i + 1] if i < len(pages) - 1 else None
    return PageContext(project=project_, cur=cur, prev=prev, next=next)


def resolve_version_keys(user, page) -> tuple:
    """Resolve the target version key to save to and the actual version key to load.

    :return: a tuple of (target_version_key, active_version_key)
    """
    if getattr(user, "is_authenticated", False):
        target_key = f"user:{user.id}"
    else:
        target_key = "role:p1"

    # Fetch users associated with existing user: version tracks
    user_ids = []
    for v in page.versions:
        if v.version_key.startswith("user:"):
            try:
                user_ids.append(int(v.version_key.split(":", 1)[1]))
            except ValueError:
                pass

    from kalanjiyam.database import User
    from kalanjiyam.queries import get_session
    session = get_session()
    users = session.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: u for u in users}

    # Group existing version tracks
    moderator_tracks = []
    p2_tracks = []
    p1_tracks = []
    ocr_tracks = []

    for v in page.versions:
        if v.version_key.startswith("user:"):
            try:
                uid = int(v.version_key.split(":", 1)[1])
                u = user_map.get(uid)
                if u:
                    if u.is_moderator or u.is_org_admin or u.is_super_admin:
                        moderator_tracks.append(v)
                    elif u.is_p2:
                        p2_tracks.append(v)
                    elif u.is_p1:
                        p1_tracks.append(v)
                    else:
                        p1_tracks.append(v)
                else:
                    p1_tracks.append(v)
            except ValueError:
                p1_tracks.append(v)
        elif v.version_key.startswith("ocr:"):
            ocr_tracks.append(v)

    # Sort tracks in each tier by updated_at descending
    moderator_tracks.sort(key=lambda x: x.updated_at, reverse=True)
    p2_tracks.sort(key=lambda x: x.updated_at, reverse=True)
    p1_tracks.sort(key=lambda x: x.updated_at, reverse=True)
    ocr_tracks.sort(key=lambda x: 0 if x.version_key == "ocr:chandra" else 1)

    existing_keys = {v.version_key for v in page.versions}

    # Determine fallback list based on logged-in user's roles
    if getattr(user, "is_authenticated", False):
        # 1. Always prefer own changes
        if target_key in existing_keys:
            return target_key, target_key

        if user.is_moderator or user.is_org_admin or user.is_super_admin:
            # Moderator fallback order: Moderator -> P2 -> P1 -> OCR
            if moderator_tracks:
                return target_key, moderator_tracks[0].version_key
            if p2_tracks:
                return target_key, p2_tracks[0].version_key
            if p1_tracks:
                return target_key, p1_tracks[0].version_key
        elif user.is_p2:
            # P2 fallback order: P2 -> P1 -> OCR
            if p2_tracks:
                return target_key, p2_tracks[0].version_key
            if p1_tracks:
                return target_key, p1_tracks[0].version_key
        elif user.is_p1:
            # P1 fallback order: P1 -> OCR
            if p1_tracks:
                return target_key, p1_tracks[0].version_key
    else:
        # Anonymous user fallback order: Moderator -> P2 -> P1 -> OCR
        if moderator_tracks:
            return target_key, moderator_tracks[0].version_key
        if p2_tracks:
            return target_key, p2_tracks[0].version_key
        if p1_tracks:
            return target_key, p1_tracks[0].version_key

    # OCR Fallback
    if ocr_tracks:
        return target_key, ocr_tracks[0].version_key

    # Hard default
    return target_key, target_key


def get_version_display_name(version_key: str) -> str:
    if version_key.startswith("user:"):
        try:
            user_id = int(version_key.split(":", 1)[1])
            from kalanjiyam.database import User
            from kalanjiyam.queries import get_session
            session = get_session()
            u = session.query(User).filter_by(id=user_id).first()
            if u:
                if u.is_moderator or u.is_org_admin or u.is_super_admin:
                    role_str = "Moderator"
                elif u.is_p2:
                    role_str = "P2"
                elif u.is_p1:
                    role_str = "P1"
                else:
                    role_str = "Editor"
                return f"{u.username} ({role_str})"
            else:
                return _l("Unknown (P1)")
        except (ValueError, IndexError):
            return _l("Unknown (P1)")
    elif version_key == "role:p1":
        return _l("Legacy Consolidated P1")
    elif version_key == "role:p2":
        return _l("Legacy Consolidated P2")
    elif version_key == "role:moderator":
        return _l("Legacy Consolidated Moderator")
    elif version_key.startswith("ocr:"):
        engine_name = version_key.split(":", 1)[1]
        from kalanjiyam.utils.ocr_types import REVERSE_ENGINE_MAP
        num = REVERSE_ENGINE_MAP.get(engine_name, engine_name)
        if num.isdigit():
            return _l("OCR %(number)s", number=num)
        return _l("%(engine)s OCR", engine=num.capitalize())
    return version_key


def _translation_context_for_revision(cur: db.Page, latest_revision: db.Revision | None) -> tuple:
    translation_content = None
    translation_metadata = None
    available_translations = []
    if not latest_revision:
        return translation_content, translation_metadata, available_translations
    session = q.get_session()
    translations = session.query(db.Translation).filter_by(
        page_id=cur.id,
        revision_id=latest_revision.id,
    ).all()
    available_translations = [
        {
            "id": t.id,
            "content": t.content,
            "source_language": t.source_language,
            "target_language": t.target_language,
            "engine": t.translation_engine,
            "created_at": t.created_at,
        }
        for t in translations
    ]
    if available_translations:
        first = available_translations[0]
        translation_content = first["content"]
        translation_metadata = {
            "source_language": first["source_language"],
            "target_language": first["target_language"],
            "engine": first["engine"],
        }
    return translation_content, translation_metadata, available_translations


def _page_document_dict_for_version(cur: db.Page, version_key: str) -> dict:
    session = q.get_session()
    page_version = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=version_key
    ).first()
    
    latest_revision = page_version.revisions[-1] if page_version and page_version.revisions else None

    if latest_revision:
        doc = document_for_revision(latest_revision, cur)
    else:
        doc = PageDocument.empty()
        if cur.page_width:
            doc.page_width = cur.page_width
        if cur.page_height:
            doc.page_height = cur.page_height
        from kalanjiyam.utils.page_document import enrich_document_from_page_ocr
        doc = enrich_document_from_page_ocr(doc, cur)

    if (not doc.page_width or not doc.page_height) and cur.project:
        try:
            from PIL import Image
            image_path = get_page_image_filepath(cur.project.slug, cur.slug)
            with Image.open(image_path) as img:
                if not doc.page_width:
                    doc.page_width = int(img.size[0])
                if not doc.page_height:
                    doc.page_height = int(img.size[1])
        except Exception:
            pass
    return doc.to_dict()


def _editor_template_kwargs(
    ctx: PageContext,
    form: EditPageForm,
    *,
    conflict=None,
    has_edits: bool,
    ocr_status: str,
    engine_choices: list,
    active_version_key: str,
    target_version_key: str,
    available_versions: list,
) -> dict:
    cur = ctx.cur
    is_r0 = cur.status.name == SitePageStatus.R0
    image_number = cur.slug
    page_number = _get_page_number(ctx.project, cur)

    session = q.get_session()
    active_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=active_version_key
    ).first()
    latest_revision = active_version_record.revisions[-1] if active_version_record and active_version_record.revisions else None

    translation_content, translation_metadata, available_translations = _translation_context_for_revision(
        cur, latest_revision
    )
    page_document = _page_document_dict_for_version(cur, active_version_key)
    doc_obj = PageDocument.from_dict(page_document)
    page_plain_text = doc_obj.to_plain_text()
    
    ocr_bounding_boxes = cur.ocr_bounding_boxes or ""
    has_ocr_content = bool(cur.ocr_bounding_boxes) or bool(page_document.get("blocks"))

    # Fetch default OCR engine configuration for restricted users
    system_settings = q.get_system_settings()
    default_ocr_engine = system_settings.default_ocr_engine or "google"
    from kalanjiyam.utils.ocr_types import REVERSE_ENGINE_MAP
    default_engine_value = REVERSE_ENGINE_MAP.get(default_ocr_engine, "1")
    from kalanjiyam.utils.org_access import is_restricted_ocr_user
    is_restricted_ocr = is_restricted_ocr_user(current_user)
    from kalanjiyam.utils.translation_engine import get_available_translation_engines

    page_rules = project_utils.parse_page_number_spec(ctx.project.page_numbers)
    page_titles = project_utils.apply_rules(len(ctx.project.pages), page_rules)
    pages = list(zip(page_titles, ctx.project.pages))

    return {
        "conflict": conflict,
        "cur": cur,
        "form": form,
        "has_edits": has_edits,
        "image_number": image_number,
        "is_r0": is_r0,
        "page_context": ctx,
        "page_number": page_number,
        "project": ctx.project,
        "pages": pages,
        "translation_content": translation_content,
        "translation_metadata": translation_metadata,
        "available_translations": available_translations,
        "ocr_status": ocr_status,
        "engine_choices": engine_choices,
        "page_document": page_document,
        "page_plain_text": page_plain_text,
        "has_ocr_content": has_ocr_content,
        "ocr_bounding_boxes": ocr_bounding_boxes,
        "page_width": cur.page_width or page_document.get("page_width"),
        "page_height": cur.page_height or page_document.get("page_height"),
        "is_restricted_ocr": is_restricted_ocr,
        "default_engine_value": default_engine_value,
        "active_version_key": active_version_key,
        "target_version_key": target_version_key,
        "available_versions": available_versions,
        "translation_engines": get_available_translation_engines(),
    }


def _get_page_number(project_: db.Project, page_: db.Page) -> str:
    """Get the page number for the given page.

    We define page numbers through a page spec. For now, just interpret the
    full page spec. In the future, we might store this in its own column.
    """
    if not project_.page_numbers:
        return page_.slug

    page_rules = project_utils.parse_page_number_spec(project_.page_numbers)
    page_titles = project_utils.apply_rules(len(project_.pages), page_rules)
    for title, cur in zip(page_titles, project_.pages):
        if cur.id == page_.id:
            return title

    # We shouldn't reach this case, but if we do, reuse the page's slug.
    return page_.slug


@bp.route("/<project_slug>/<page_slug>/download/docx")
def download_as_docx(project_slug, page_slug):
    """Download a single page compiled into a DOCX document."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if page_ is None:
        abort(404)

    from kalanjiyam.utils import proofing_utils
    blob = proofing_utils.documents_to_docx([page_])

    response = make_response(blob, 200)
    response.mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{project_slug}-{page_slug}.docx"'
    )
    return response


@bp.route("/<project_slug>/<page_slug>/download/pdf")
def download_as_pdf(project_slug, page_slug):
    """Download a single page compiled into a PDF document in replica layout."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if page_ is None:
        abort(404)

    from kalanjiyam.utils import proofing_utils
    blob = proofing_utils.documents_to_pdf(project_, [page_])

    response = make_response(blob, 200)
    response.mimetype = "application/pdf"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{project_slug}-{page_slug}-replica.pdf"'
    )
    return response



@bp.route("/<project_slug>/<page_slug>/download/html")
def download_as_html(project_slug, page_slug):
    """Download a single page compiled into HTML ZIP."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if page_ is None:
        abort(404)

    from kalanjiyam.utils import proofing_utils
    blob = proofing_utils.documents_to_html_zip(project_, [page_], replica=True)

    response = make_response(blob, 200)
    response.mimetype = "application/zip"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{project_slug}-{page_slug}-replica.zip"'
    )
    return response


@bp.route("/<project_slug>/<page_slug>/download/txt")
def download_as_text(project_slug, page_slug):
    """Download a single page compiled into plain text."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if page_ is None:
        abort(404)

    from kalanjiyam.utils import proofing_utils
    content_blobs = [page_.revisions[-1].content if page_.revisions else ""]
    raw_text = proofing_utils.to_plain_text(content_blobs)

    response = make_response(raw_text, 200)
    response.mimetype = "text/plain"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{project_slug}-{page_slug}.txt"'
    )
    return response


@bp.route("/<project_slug>/<page_slug>/download/xml")
def download_as_xml(project_slug, page_slug):
    """Download a single page compiled into TEI XML."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if page_ is None:
        abort(404)

    project_meta = {
        "title": project_.display_title,
        "author": project_.author,
        "publication_year": project_.publication_year,
        "publisher": project_.publisher,
        "editor": project_.editor,
    }
    project_meta = {k: v or "TODO" for k, v in project_meta.items()}

    from kalanjiyam.utils import proofing_utils
    has_blocks = any(
        p.revisions
        and getattr(p.revisions[-1], "content_format", "plain") == "blocks"
        and getattr(p.revisions[-1], "document", None)
        for p in [page_]
    )
    if has_blocks:
        xml_blob = proofing_utils.documents_to_tei_xml(project_meta, [page_])
    else:
        content_blobs = [page_.revisions[-1].content if page_.revisions else ""]
        xml_blob = proofing_utils.to_tei_xml(project_meta, content_blobs)

    response = make_response(xml_blob, 200)
    response.mimetype = "application/xml"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{project_slug}-{page_slug}.xml"'
    )
    return response


@bp.route("/<project_slug>/<page_slug>/download/json")
def download_as_json(project_slug, page_slug):
    """Download a single page compiled into PageDocument JSON."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if page_ is None:
        abort(404)

    from kalanjiyam.utils import proofing_utils
    blob = proofing_utils.documents_to_json_bundle(project_, [page_])

    response = make_response(blob, 200)
    response.mimetype = "application/json"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{project_slug}-{page_slug}.json"'
    )
    return response


@bp.route("/<project_slug>/<page_slug>/")
def edit(project_slug, page_slug):
    """Display the page editor."""
    ctx = _get_page_context(project_slug, page_slug)
    if ctx is None:
        abort(404)

    cur = ctx.cur

    # Resolve target and active version keys
    target_key, active_key = resolve_version_keys(current_user, cur)
    requested_version = request.args.get("version")
    if requested_version:
        active_key = requested_version

    # Get target version counter
    session = q.get_session()
    target_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=target_key
    ).first()
    target_version_val = target_version_record.version if target_version_record else 0

    form = EditPageForm()
    form.version.data = target_version_val

    # Get active version's latest revision
    active_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=active_key
    ).first()
    
    latest_revision = active_version_record.revisions[-1] if active_version_record and active_version_record.revisions else None
    
    has_edits = latest_revision is not None
    if has_edits:
        form.content.data = latest_revision.content

    status_names = {s.id: s.name for s in q.page_statuses()}
    form.status.data = status_names.get(latest_revision.status_id if latest_revision else cur.status_id)

    # Format available versions list for the selector UI
    available_versions = []
    for pv in cur.versions:
        available_versions.append({
            "version_key": pv.version_key,
            "display_name": get_version_display_name(pv.version_key),
            "updated_at": pv.updated_at.isoformat() + "Z" if pv.updated_at else "",
        })

    from kalanjiyam.utils.ocr_client import get_available_engines
    from kalanjiyam.utils.ocr_types import build_engine_choices

    ocr_ping = get_available_engines()
    system_settings = q.get_system_settings()
    engine_choices = build_engine_choices(
        ocr_ping["engines"],
        is_super_admin=current_user.is_super_admin,
        recommended_engine=system_settings.recommended_ocr_engine,
    )

    return render_template(
        "proofing/pages/edit.html",
        **_editor_template_kwargs(
            ctx,
            form,
            has_edits=has_edits,
            ocr_status=ocr_ping["status"],
            engine_choices=engine_choices,
            active_version_key=active_key,
            target_version_key=target_key,
            available_versions=available_versions,
        ),
    )


@bp.route("/<project_slug>/<page_slug>/", methods=["POST"])
@p2_required
def edit_post(project_slug, page_slug):
    """Submit changes through the page editor."""
    ctx = _get_page_context(project_slug, page_slug)
    if ctx is None:
        abort(404)

    cur = ctx.cur

    # Resolve target and active version keys
    target_key, active_key = resolve_version_keys(current_user, cur)
    requested_version = request.args.get("version")
    if requested_version:
        active_key = requested_version

    form = EditPageForm()
    conflict = None

    if form.validate_on_submit():
        doc = parse_document_field(form.document.data)
        content_format = "blocks" if doc else "plain"
        try:
            # We save to target_key
            new_version = add_revision(
                cur,
                summary=form.summary.data,
                content=form.content.data,
                status=form.status.data,
                version=int(form.version.data),
                author_id=current_user.id if current_user.is_authenticated else None,
                document=doc,
                content_format=content_format,
                version_key=target_key,
            )
            form.version.data = new_version
            flash("Saved changes.", "success")
            # Since changes saved successfully, our active key can now become target_key
            active_key = target_key
        except EditError:
            flash("Edit conflict. Please incorporate the changes below:", "error")
            # Get latest revision of target_key to display as conflict
            session = q.get_session()
            target_version_record = session.query(db.PageVersion).filter_by(
                page_id=cur.id,
                version_key=target_key
            ).first()
            conflict = target_version_record.revisions[-1] if target_version_record and target_version_record.revisions else None
            form.version.data = target_version_record.version if target_version_record else 0
    else:
        for field, errors in form.errors.items():
            flash(f"Validation error in {field}: {', '.join(errors)}", "error")

    # Get target version counter
    session = q.get_session()
    target_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=target_key
    ).first()
    target_version_val = target_version_record.version if target_version_record else 0
    form.version.data = target_version_val

    # Format available versions list for the selector UI
    available_versions = []
    for pv in cur.versions:
        available_versions.append({
            "version_key": pv.version_key,
            "display_name": get_version_display_name(pv.version_key),
            "updated_at": pv.updated_at.isoformat() + "Z" if pv.updated_at else "",
        })

    from kalanjiyam.utils.ocr_client import get_available_engines
    from kalanjiyam.utils.ocr_types import build_engine_choices

    ocr_ping = get_available_engines()
    system_settings = q.get_system_settings()
    engine_choices = build_engine_choices(
        ocr_ping["engines"],
        is_super_admin=current_user.is_super_admin,
        recommended_engine=system_settings.recommended_ocr_engine,
    )

    return render_template(
        "proofing/pages/edit.html",
        **_editor_template_kwargs(
            ctx,
            form,
            conflict=conflict,
            has_edits=True,
            ocr_status=ocr_ping["status"],
            engine_choices=engine_choices,
            active_version_key=active_key,
            target_version_key=target_key,
            available_versions=available_versions,
        ),
    )


@bp.route("/<project_slug>/<page_slug>/history")
def history(project_slug, page_slug):
    """View the full revision history for the given page."""
    ctx = _get_page_context(project_slug, page_slug)
    if ctx is None:
        abort(404)

    return render_template(
        "proofing/pages/history.html",
        project=ctx.project,
        cur=ctx.cur,
        prev=ctx.prev,
        next=ctx.next,
    )


@bp.route("/<project_slug>/<page_slug>/revision/<revision_id>")
def revision(project_slug, page_slug, revision_id):
    """View a specific revision for some page."""
    ctx = _get_page_context(project_slug, page_slug)
    if ctx is None:
        abort(404)

    cur = ctx.cur
    prev_revision = None
    cur_revision = None
    for r in cur.revisions:
        if str(r.id) == revision_id:
            cur_revision = r
            break
        else:
            prev_revision = r

    if not cur_revision:
        abort(404)

    if prev_revision:
        diff = revision_diff(prev_revision.content, cur_revision.content)
    else:
        diff = revision_diff("", cur_revision.content)

    return render_template(
        "proofing/pages/revision.html",
        project=ctx.project,
        cur=cur,
        prev=ctx.prev,
        next=ctx.next,
        revision=cur_revision,
        diff=diff,
    )


# FIXME: added trailing slash as a quick hack to support OCR routes on
# frontend, which just concatenate the window URL onto "/api/ocr".
@api.route("/ocr/<project_slug>/<page_slug>/")
@p2_required
def ocr(project_slug, page_slug):
    """Apply OCR to the given page using the specified engine."""
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description="Superadmins are not allowed to access project data.")
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)
    if not q.user_can_view_proofing_project(current_user, project_):
        abort(403)

    page_ = q.page(project_.id, page_slug)
    if not page_:
        abort(404)

    # Enforce guest daily OCR limit
    if not current_user.is_authenticated:
        from kalanjiyam.utils.rate_limit import is_rate_limited
        ip_address = request.remote_addr
        fingerprint_id = request.cookies.get("device_fingerprint")
        settings = q.get_system_settings()
        limit = settings.unregistered_user_ocr_limit
        if is_rate_limited("run_ocr", ip_address, fingerprint_id, limit=limit):
            abort(429, description=f"Rate limit exceeded. Guests can only run OCR {limit} times per 24 hours.")

    engine = request.args.get('engine', 'google')
    language = request.args.get('language', 'sa')

    # Override for restricted users
    from kalanjiyam.utils.org_access import is_restricted_ocr_user
    if is_restricted_ocr_user(current_user):
        settings = q.get_system_settings()
        engine = settings.default_ocr_engine or "google"

    from kalanjiyam.utils.ocr_runner import normalize_engine, run_ocr
    from kalanjiyam.utils.ocr_types import SUPPORTED_ENGINES

    original_engine = engine
    engine = normalize_engine(engine)

    logging.info(
        "OCR API called with engine='%s' -> mapped to '%s', language='%s', backend='%s'",
        original_engine,
        engine,
        language,
        'remote',
    )

    if engine not in SUPPORTED_ENGINES:
        abort(400, description=f"Unsupported OCR engine: {engine}")

    image_path = get_page_image_filepath(project_slug, page_slug)

    try:
        ensure_ocr_quota_for_project(project_)
        ocr_response = run_ocr(image_path, engine_name=engine, language=language)
        consume_ocr_credit_for_project(project_)

        # Ensure we have the target PageVersion and current version val
        version_key = f"ocr:{engine}"
        session = q.get_session()
        pv = session.query(db.PageVersion).filter_by(
            page_id=page_.id,
            version_key=version_key
        ).first()
        current_ver = pv.version if pv else 0

        # Build PageDocument from OCR response
        from kalanjiyam.utils.ocr_persist import image_size, _stamp_provenance
        from kalanjiyam.utils.page_document import normalize_geometry
        from kalanjiyam.utils.ocr_types import OcrResponse as OcrResponseObj
        
        image_w = image_h = None
        if image_path:
            size = image_size(image_path)
            if size:
                image_w, image_h = size

        boxes, blocks_data, pw, ph = normalize_geometry(
            ocr_response.bounding_boxes,
            ocr_response.blocks,
            ocr_width=ocr_response.page_width,
            ocr_height=ocr_response.page_height,
            image_width=image_w or page_.page_width,
            image_height=image_h or page_.page_height,
            coordinate_space=ocr_response.coordinate_space,
        )
        
        if pw:
            page_.page_width = int(pw)
        elif image_w:
            page_.page_width = image_w
        if ph:
            page_.page_height = int(ph)
        elif image_h:
            page_.page_height = image_h
            
        normalized = OcrResponseObj(
            text_content=ocr_response.text_content,
            bounding_boxes=boxes,
            layout_html=ocr_response.layout_html,
            blocks=blocks_data if blocks_data is not None else ocr_response.blocks,
            content_format=ocr_response.content_format,
            page_width=pw or ocr_response.page_width or image_w,
            page_height=ph or ocr_response.page_height or image_h,
            pipeline=ocr_response.pipeline,
            source_type=ocr_response.source_type,
            coordinate_space="pixel",
            model=ocr_response.model,
        )
        doc = PageDocument.from_ocr_response(
            normalized,
            image_width=pw or image_w,
            image_height=ph or image_h,
        )
        _stamp_provenance(doc, engine, ocr_response.model)
        
        # Save a new revision to the ocr:{engine} version track
        add_revision(
            page_,
            summary="OCR run",
            content=doc.to_plain_text(),
            status=SitePageStatus.R0.value,
            version=current_ver,
            author_id=current_user.id if current_user.is_authenticated else None,
            document=doc.to_dict(),
            content_format="blocks",
            version_key=version_key,
        )

        session.add(page_)
        session.commit()

        # Log usage action for guests
        if not current_user.is_authenticated:
            from kalanjiyam.utils.rate_limit import log_usage_action
            log_usage_action(
                action="run_ocr",
                ip_address=request.remote_addr,
                fingerprint_id=request.cookies.get("device_fingerprint"),
                project_slug=project_slug
            )

        payload = ocr_response_to_api_dict(
            ocr_response,
            engine,
            image_width=page_.page_width,
            image_height=page_.page_height,
        )
        logging.info(
            "OCR completed successfully, returning %s blocks",
            len(payload.get("blocks") or []),
        )
        return jsonify(payload)
    except Exception as e:
        logging.error(
            "OCR failed for %s/%s with engine %s and language %s: %s",
            project_slug,
            page_slug,
            engine,
            language,
            e,
            exc_info=True,
        )
        abort(500, description=f"OCR failed: {str(e)}")


def _translate_html_content(html: str, source_lang: str, target_lang: str, engine: str) -> str:
    """Helper to translate plain text sections within HTML content, preserving HTML tags."""
    import re
    from kalanjiyam.utils.translation_engine import translate_text

    # Split by HTML tags
    parts = re.split(r'(<[^>]+>)', html)
    for i in range(len(parts)):
        # Even indices are text content, odd indices are tags
        if i % 2 == 0:
            text = parts[i]
            if text and text.strip():
                try:
                    stripped = text.strip()
                    leading_ws = text[:len(text) - len(text.lstrip())]
                    trailing_ws = text[len(text.rstrip()):]
                    
                    translation_response = translate_text(
                        stripped,
                        source_lang,
                        target_lang,
                        engine
                    )
                    translated_text = translation_response.translated_text
                    parts[i] = f"{leading_ws}{translated_text}{trailing_ws}"
                except Exception as e:
                    logging.error(f"Failed to translate segment '{text}': {e}")
    return "".join(parts)


@api.route("/translate/<project_slug>/<page_slug>/", methods=["GET", "POST"])
@login_required
def translate(project_slug, page_slug):
    """Apply translation to the given page using the specified engine."""
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description="Superadmins are not allowed to access project data.")
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)
    if not q.user_can_view_proofing_project(current_user, project_):
        abort(403)

    page_ = q.page(project_.id, page_slug)
    if not page_:
        abort(404)

    # Get translation parameters from query or body
    doc_data = {}
    if request.method == "POST":
        if not request.is_json:
            abort(400, description="Expected JSON payload")
        doc_data = request.get_json() or {}

    source_lang = request.args.get('source_lang') or doc_data.get('source_lang') or 'sa'
    target_lang = request.args.get('target_lang') or doc_data.get('target_lang') or 'en'
    engine = request.args.get('engine') or doc_data.get('engine') or 'indictrans2'
    revision_id = request.args.get('revision_id', type=int)
    
    # Validate engine
    from kalanjiyam.utils.translation_engine import TranslationEngineFactory
    if not TranslationEngineFactory.is_supported(engine):
        abort(400, description=f"Unsupported translation engine: {engine}")

    if request.method == "POST":
        blocks = doc_data.get("blocks", [])
        if blocks:
            for block in blocks:
                content = block.get("content", "")
                if content and content.strip():
                    block["content"] = _translate_html_content(
                        content,
                        source_lang,
                        target_lang,
                        engine
                    )
        elif "content" in doc_data:
            content = doc_data["content"]
            if content and content.strip():
                doc_data["content"] = _translate_html_content(
                    content,
                    source_lang,
                    target_lang,
                    engine
                )
        return jsonify(doc_data)

    # Get the revision to translate
    if revision_id is None:
        # Use the latest revision
        if not page_.revisions:
            abort(400, description="No revisions found for this page")
        revision = page_.revisions[-1]  # Latest revision
    else:
        revision = q.get_session().query(db.Revision).filter_by(id=revision_id).first()
        if not revision or revision.page_id != page_.id:
            abort(400, description=f"Revision {revision_id} not found for this page")
    
    try:
        # Check if translation already exists
        session = q.get_session()
        existing_translation = session.query(db.Translation).filter_by(
            page_id=page_.id,
            revision_id=revision.id,
            source_language=source_lang,
            target_language=target_lang,
            translation_engine=engine
        ).first()

        if existing_translation:
            # Return existing translation
            return existing_translation.content

        # Perform translation preserving HTML tags
        translated_text = _translate_html_content(
            revision.content,
            source_lang,
            target_lang,
            engine
        )

        # Save translation to database
        from kalanjiyam import consts
        bot_user = q.user(consts.BOT_USERNAME)
        if bot_user is None:
            abort(500, description="Bot user not found")

        new_translation = db.Translation(
            page_id=page_.id,
            revision_id=revision.id,
            author_id=bot_user.id,
            content=translated_text,
            source_language=source_lang,
            target_language=target_lang,
            translation_engine=engine,
            status='completed'
        )
        
        session.add(new_translation)
        session.commit()

        return translated_text
    except Exception as e:
        logging.error(f"Translation failed for {project_slug}/{page_slug} with engine {engine}: {e}")
        abort(500, description=f"Translation failed: {str(e)}")


@api.route("/upload-image/<project_slug>/<page_slug>/", methods=["POST"])
@login_required
def upload_image(project_slug, page_slug):
    """Upload an image for the rich text editor."""
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description="Superadmins are not allowed to access project data.")
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)
    if not q.user_can_view_proofing_project(current_user, project_):
        abort(403)

    page_ = q.page(project_.id, page_slug)
    if not page_:
        abort(404)

    # Check if file was uploaded
    if 'image' not in request.files:
        abort(400, description="No image file provided")
    
    file = request.files['image']
    if file.filename == '':
        abort(400, description="No image file selected")
    
    # Validate file type
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
    filename = secure_filename(file.filename)
    if '.' not in filename:
        abort(400, description="File must have an extension")
    
    file_ext = filename.rsplit('.', 1)[1].lower()
    if file_ext not in allowed_extensions:
        abort(400, description=f"File type '{file_ext}' not allowed. Allowed types: {', '.join(allowed_extensions)}")
    
    # Validate file size (max 10MB)
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    max_size = 10 * 1024 * 1024  # 10MB
    if file_size > max_size:
        abort(400, description=f"File size ({file_size / 1024 / 1024:.2f}MB) exceeds maximum allowed size (10MB)")
    ensure_storage_quota_for_user(current_user, file_size)
    
    try:
        # Generate unique filename to avoid conflicts
        unique_id = uuid.uuid4().hex[:8]
        safe_filename = secure_filename(filename)
        name_without_ext = safe_filename.rsplit('.', 1)[0] if '.' in safe_filename else safe_filename
        unique_filename = f"{name_without_ext}_{unique_id}.{file_ext}"

        # Save file
        from kalanjiyam.utils.storage import editor_image_key, get_storage

        get_storage().save(editor_image_key(project_slug, unique_filename), file.stream)
        add_storage_usage_for_project(project_slug)
        
        # Generate URL for the image
        # Use the site blueprint to serve images
        image_url = url_for("site.editor_image", project_slug=project_slug, filename=unique_filename)
        
        return jsonify({
            'success': True,
            'url': image_url,
            'filename': unique_filename
        })
    except Exception as e:
        logging.error(f"Image upload failed for {project_slug}/{page_slug}: {e}")
        abort(500, description=f"Image upload failed: {str(e)}")
