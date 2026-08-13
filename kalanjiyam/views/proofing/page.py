"""Routes related to project pages.

The main route here is `edit`, which defines the page editor and the edit flow.
"""

import logging
import re
import uuid
from dataclasses import dataclass

from flask import (
    Blueprint,
    current_app,
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
    consume_translation_credit_for_project,
    ensure_translation_quota_for_project,
)
from kalanjiyam.utils.revisions import EditError, add_revision, parse_document_field
from kalanjiyam.utils.translation_engine import translate_text
from kalanjiyam.views.api import bp as api

bp = Blueprint("page", __name__)


SENTENCE_SPLIT_REGEX = re.compile(
    r'((?<!\bMs\.)(?<!\bMr\.)(?<!\bDr\.)(?<!\bProf\.)(?<!\bSr\.)(?<!\bJr\.)(?<=[.!?।॥])\s+|\n+)'
)


@bp.before_request
def _enforce_project_access():
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description=_l("Superadmins are not allowed to view project data."))
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

    In the Dual-Save Model, edits save to the shared Main Branch ('main') as primary target.
    If the user committed to 'main', 'main' is loaded by default.
    If the user has an unmerged private draft ('user:<id>') newer than 'main', their draft is loaded.
    :return: a tuple of (target_version_key, active_version_key)
    """
    target_key = "main"

    session = q.get_session()
    page_versions = session.query(db.PageVersion).filter_by(page_id=page.id).all()
    version_map = {v.version_key: v for v in page_versions}
    existing_keys = set(version_map.keys())

    # 1. If user has an unmerged personal draft with content different from main, load user track
    if getattr(user, "is_authenticated", False):
        user_key = f"user:{user.id}"
        if user_key in existing_keys:
            user_ver = version_map[user_key]
            main_ver = version_map.get("main")
            if not main_ver:
                return target_key, user_key
            else:
                user_rev = user_ver.revisions[-1] if user_ver.revisions else None
                main_rev = main_ver.revisions[-1] if main_ver.revisions else None
                user_content = (user_rev.content or "").strip() if user_rev else ""
                main_content = (main_rev.content or "").strip() if main_rev else ""
                if user_ver.updated_at > main_ver.updated_at and user_content != main_content:
                    return target_key, user_key

    # 2. If main branch exists, load main branch by default
    if "main" in existing_keys:
        return target_key, "main"

    if not page_versions:
        return target_key, target_key

    # Fetch users associated with existing user: version tracks for tie-breaking
    user_ids = []
    for v in page_versions:
        if v.version_key.startswith("user:"):
            try:
                user_ids.append(int(v.version_key.split(":", 1)[1]))
            except ValueError:
                pass

    from kalanjiyam.database import User
    users = session.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: u for u in users}

    def _track_tier(v):
        """Return tier rank (1 is highest priority) for tie-breaking when updated_at is identical."""
        if v.version_key == "main":
            return 1
        elif v.version_key.startswith("user:"):
            try:
                uid = int(v.version_key.split(":", 1)[1])
                u = user_map.get(uid)
                if u:
                    if u.is_moderator or u.is_org_admin or u.is_super_admin:
                        return 1
                    elif u.is_p2:
                        return 2
                    else:
                        return 3
            except ValueError:
                pass
            return 3
        elif v.version_key == "role:moderator":
            return 1
        elif v.version_key == "role:p2":
            return 2
        elif v.version_key == "role:p1":
            return 3
        elif v.version_key.startswith("translation:") or v.version_key.startswith("TR:"):
            return 4
        elif v.version_key.startswith("ocr:"):
            return 5
        return 6

    # Sort tracks by updated_at descending, then by tier rank ascending (tie-breaker)
    sorted_tracks = sorted(
        page_versions,
        key=lambda v: (v.updated_at, -_track_tier(v)),
        reverse=True
    )

    if sorted_tracks:
        return target_key, sorted_tracks[0].version_key

    return target_key, target_key


def get_version_display_name(version_key: str) -> str:
    if version_key == "main":
        return _l("Main Branch")
    elif version_key.startswith("user:"):
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
    elif version_key.startswith("translation:") or version_key.startswith("TR:"):
        parts = version_key.split(":", 2)
        engine_name = parts[1] if len(parts) > 1 else ""
        lang_str = parts[2] if len(parts) > 2 else ""
        if "->" in lang_str:
            src, target = lang_str.split("->", 1)
            lang_display = f"{src.upper()} → {target.upper()}"
        else:
            lang_display = lang_str.upper()
        label_map = {
            'indictrans2': 'IndicTrans v2',
            'indictrans3': 'IndicTrans v3',
            'google': 'Google',
            'openai': 'OpenAI',
        }
        engine_label = label_map.get(engine_name, engine_name.capitalize())
        return _l("Translation: %(engine)s (%(languages)s)", engine=engine_label, languages=lang_display)
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
        from kalanjiyam.utils.storage import project_docx_key, get_storage
        is_docx = False
        if cur.project:
            is_docx = get_storage().exists(project_docx_key(cur.project.slug))
            
        if is_docx:
            orig_version = session.query(db.PageVersion).filter_by(
                page_id=cur.id,
                version_key="original"
            ).first()
            orig_rev = orig_version.revisions[-1] if orig_version and orig_version.revisions else None
            if orig_rev:
                from kalanjiyam.utils.document_storage import load_revision_document

                orig_doc = load_revision_document(orig_rev)
                if orig_doc:
                    doc = PageDocument.from_dict(orig_doc)
            else:
                doc = PageDocument.empty()
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
    
    is_docx = False
    original_html = ""
    from kalanjiyam.utils.storage import project_docx_key, get_storage
    storage = get_storage()
    if ctx.project:
        is_docx = storage.exists(project_docx_key(ctx.project.slug))
        
    if is_docx:
        orig_version = session.query(db.PageVersion).filter_by(
            page_id=cur.id,
            version_key="original"
        ).first()
        orig_rev = orig_version.revisions[-1] if orig_version and orig_version.revisions else None
        if orig_rev:
            from kalanjiyam.utils.document_storage import load_revision_document as _load_rev_doc

            orig_doc_data = _load_rev_doc(orig_rev)
            if orig_doc_data:
                doc_data = orig_doc_data
            blocks = doc_data.get("blocks", [])
            if blocks and doc_data.get("content_format") == "html":
                original_html = blocks[0].get("content", "")
        page_plain_text = latest_revision.content if latest_revision else original_html

    # Prepend URL prefix to image paths in HTML to serve them correctly if APPLICATION_URL_PREFIX is set
    prefix = current_app.config.get("APPLICATION_URL_PREFIX") or ""
    if prefix:
        if original_html:
            original_html = original_html.replace(f'{prefix}/static/uploads/', '/static/uploads/').replace('/static/uploads/', f'{prefix}/static/uploads/')
        if page_plain_text:
            page_plain_text = page_plain_text.replace(f'{prefix}/static/uploads/', '/static/uploads/').replace('/static/uploads/', f'{prefix}/static/uploads/')
        if page_document and "blocks" in page_document:
            for block in page_document["blocks"]:
                if "content" in block and block["content"]:
                    block["content"] = block["content"].replace(f'{prefix}/static/uploads/', '/static/uploads/').replace('/static/uploads/', f'{prefix}/static/uploads/')

    from kalanjiyam.utils.document_storage import load_page_ocr as _load_ocr

    ocr_bounding_boxes = _load_ocr(cur) or ""
    has_ocr_content = bool(ocr_bounding_boxes) or bool(page_document.get("blocks"))

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
    main_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key="main"
    ).first()
    active_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=active_version_key
    ).first()

    # Detect if active track is user draft and main has newer edits from someone else
    if not conflict and active_version_key.startswith("user:") and main_version_record and active_version_record:
        if main_version_record.updated_at > active_version_record.updated_at:
            main_latest = main_version_record.revisions[-1] if main_version_record.revisions else None
            your_text = form.content.data or page_plain_text or ""
            if main_latest and main_latest.content and main_latest.content.strip() != your_text.strip():
                conflict = main_latest

    conflict_diff = ""
    conflict_author_name = ""
    conflict_time = ""
    your_content = form.content.data or page_plain_text or ""
    if conflict:
        from kalanjiyam.utils.diff import revision_diff
        conflict_diff = str(revision_diff(conflict.content or "", your_content))
        if conflict.author:
            conflict_author_name = conflict.author.username or conflict.author.email or ""
        if getattr(conflict, "created", None):
            conflict_time = conflict.created.strftime('%b %d, %Y at %H:%M UTC')

    target_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=target_version_key
    ).first()
    page_version = target_version_record.version if target_version_record else 0

    return {
        "page_version": page_version,
        "target_version_key": target_version_key,
        "active_version_key": active_version_key,
        "conflict": conflict,
        "conflict_diff": conflict_diff,
        "conflict_author_name": conflict_author_name,
        "conflict_time": conflict_time,
        "your_content": your_content,
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
        "is_docx": is_docx,
        "original_html": original_html,
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
    from kalanjiyam.utils.document_storage import load_revision_document as _load_doc

    has_blocks = any(
        p.revisions
        and getattr(p.revisions[-1], "content_format", "plain") == "blocks"
        and _load_doc(p.revisions[-1])
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

    # Format available versions list for the selector UI directly from DB
    available_versions = []
    session = q.get_session()
    page_versions = session.query(db.PageVersion).filter_by(page_id=cur.id).order_by(db.PageVersion.id.asc()).all()
    for pv in page_versions:
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
        from flask import current_app
        prefix = current_app.config.get("APPLICATION_URL_PREFIX") or ""
        if prefix:
            if form.content.data:
                form.content.data = form.content.data.replace(f'{prefix}/static/uploads/', '/static/uploads/')

        from kalanjiyam.utils.storage import project_docx_key, get_storage
        is_docx = get_storage().exists(project_docx_key(ctx.project.slug))
        doc = parse_document_field(form.document.data)

        if prefix and doc and "blocks" in doc:
            for block in doc["blocks"]:
                if "content" in block and block["content"]:
                    block["content"] = block["content"].replace(f'{prefix}/static/uploads/', '/static/uploads/')

        if is_docx:
            content_format = "html"
            if not doc:
                import uuid
                doc = {
                    "content_format": "html",
                    "blocks": [{
                        "id": f"b{uuid.uuid4().hex[:8]}",
                        "type": "paragraph",
                        "bbox": [0, 0, 0, 0],
                        "content": form.content.data,
                        "reading_order": 1
                    }]
                }
        else:
            content_format = "blocks" if doc else "plain"
        session = q.get_session()
        merge_with_main = request.form.get("merge_with_main") == "1" or not current_user.is_authenticated
        primary_key = "main" if merge_with_main else (f"user:{current_user.id}" if current_user.is_authenticated else "main")

        if merge_with_main:
            expected_version = int(form.version.data) if form.version.data is not None else 0
        else:
            primary_ver_rec = session.query(db.PageVersion).filter_by(
                page_id=cur.id,
                version_key=primary_key
            ).first()
            expected_version = primary_ver_rec.version if primary_ver_rec else 0

        try:
            # Save to primary_key (Main Branch 'main' or private user track)
            new_version = add_revision(
                cur,
                summary=form.summary.data,
                content=form.content.data,
                status=form.status.data,
                version=expected_version,
                author_id=current_user.id if current_user.is_authenticated else None,
                document=doc,
                content_format=content_format,
                version_key=primary_key,
            )
            form.version.data = new_version

            # Dual-Save Model: Also save personal user track snapshot if merge_with_main is active
            if merge_with_main and current_user.is_authenticated:
                user_key = f"user:{current_user.id}"
                # Re-fetch fresh session state after the main branch commit
                # to prevent stale version counters causing spurious EditError.
                session = q.get_session()
                session.expire_all()
                user_ver_rec = session.query(db.PageVersion).filter_by(
                    page_id=cur.id,
                    version_key=user_key
                ).first()
                user_ver_num = user_ver_rec.version if user_ver_rec else 0
                try:
                    add_revision(
                        cur,
                        summary=form.summary.data,
                        content=form.content.data,
                        status=form.status.data,
                        version=user_ver_num,
                        author_id=current_user.id,
                        document=doc,
                        content_format=content_format,
                        version_key=user_key,
                    )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Dual-save to personal track %s failed for page %s: %s",
                        user_key, cur.slug, exc, exc_info=True,
                    )
                    flash(_l("Warning: Your personal draft could not be updated. "
                             "Main branch was saved successfully."), "warning")

            flash(_l("Saved changes."), "success")
            active_key = primary_key
        except EditError:
            flash(_l("Edit conflict. Please incorporate the changes below:"), "error")
            # Get latest revision of primary_key to display as conflict
            session = q.get_session()
            target_version_record = session.query(db.PageVersion).filter_by(
                page_id=cur.id,
                version_key=primary_key
            ).first()
            conflict = target_version_record.revisions[-1] if target_version_record and target_version_record.revisions else None
            form.version.data = target_version_record.version if target_version_record else 0
    else:
        for field, errors in form.errors.items():
            flash(
                _l(
                    "Validation error in %(field)s: %(errors)s",
                    field=field,
                    errors=", ".join(errors),
                ),
                "error",
            )

    # Get target version counter
    session = q.get_session()
    target_version_record = session.query(db.PageVersion).filter_by(
        page_id=cur.id,
        version_key=target_key
    ).first()
    target_version_val = target_version_record.version if target_version_record else 0
    form.version.data = target_version_val

    # Format available versions list for the selector UI directly from DB (fetching fresh versions)
    available_versions = []
    page_versions = session.query(db.PageVersion).filter_by(page_id=cur.id).order_by(db.PageVersion.id.asc()).all()
    for pv in page_versions:
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
    import time
    start_time = time.time()
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description=_l("Superadmins are not allowed to access project data."))
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
            abort(
                429,
                description=_l(
                    "Rate limit exceeded. Guests can only run OCR %(limit)s times per 24 hours.",
                    limit=limit,
                ),
            )

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
        abort(400, description=_l("Unsupported OCR engine: %(engine)s", engine=engine))

    image_path = get_page_image_filepath(project_slug, page_slug)

    try:
        ensure_ocr_quota_for_project(project_)
        ocr_response = run_ocr(image_path, engine_name=engine, language=language)
        consume_ocr_credit_for_project(project_)

        # Extract visual elements if blocks are returned
        if ocr_response.blocks:
            from kalanjiyam.utils.ocr_cropper import crop_ocr_response_elements
            try:
                crop_ocr_response_elements(
                    doc_path=str(image_path),
                    ocr_response=ocr_response,
                    project_slug=project_slug,
                    output_dir=str(image_path.parent)
                )
            except Exception as e:
                logging.exception(f"Failed to crop visual elements: {e}")

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
            contract_version=ocr_response.contract_version,
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

        # Record metrics for SINGLE_PAGE_PROOFING_OCR
        try:
            page_ocr_latency_ms = (time.time() - start_time) * 1000.0
            from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
            from kalanjiyam.utils.storage import get_storage, page_image_key
            import json

            # Find or create a dedicated SINGLE_PAGE_PROOFING_OCR batch job for this project/book
            batch_job = session.query(BatchJob).filter_by(
                target_uri=f"single_page_proofing://ocr/{project_slug}",
                job_type='SINGLE_PAGE_PROOFING_OCR'
            ).order_by(BatchJob.id.desc()).first()

            if not batch_job:
                batch_job = BatchJob(
                    target_uri=f"single_page_proofing://ocr/{project_slug}",
                    status='IN_PROGRESS',
                    job_type='SINGLE_PAGE_PROOFING_OCR'
                )
                session.add(batch_job)
                session.flush()

            project_title = getattr(project_, 'display_title', None) or project_slug
            batch_item = session.query(BatchItem).filter_by(job_id=batch_job.id, project_id=project_.id).first()
            if not batch_item:
                batch_item = BatchItem(
                    job_id=batch_job.id,
                    file_path=f"{project_title} ({project_slug})",
                    project_id=project_.id,
                    status='IN_PROGRESS',
                    total_pages=len(project_.pages),
                )
                session.add(batch_item)
                session.flush()

            # Ensure source_size_bytes is set on batch_item if missing
            if not batch_item.source_size_bytes:
                try:
                    storage = get_storage()
                    page_key = page_image_key(project_slug, page_slug)
                    if storage.exists(page_key):
                        batch_item.source_size_bytes = storage.size(page_key)
                except Exception:
                    pass

            p_num = int(page_slug) if page_slug.isdigit() else page_.order
            ocr_page = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, page_number=p_num).first()
            if not ocr_page:
                ocr_page = BatchOcrPage(
                    batch_item_id=batch_item.id,
                    chunk_id=None,
                    page_number=p_num,
                    status='PENDING'
                )

            ocr_page.ocr_latency_ms = page_ocr_latency_ms
            ocr_page.status = 'COMPLETED'
            ocr_page.completed_at = datetime.utcnow()
            ocr_page.engine = engine
            ocr_page.confidence = getattr(ocr_response, "page_confidence", None)
            ocr_page.p05 = getattr(ocr_response, "p05", None)
            ocr_page.blocks = getattr(ocr_response, "blocks_count", None) or (len(doc.blocks) if doc else None)
            ocr_page.chars = getattr(ocr_response, "chars_count", None) or (len(plain_text) if plain_text else None)
            ocr_page.engine_latency_ms = getattr(ocr_response, "engine_latency_ms", None)

            storage = get_storage()
            page_key = page_image_key(project_slug, page_slug)
            try:
                if storage.exists(page_key):
                    ocr_page.extracted_image_size_bytes = storage.size(page_key)
            except Exception:
                pass

            plain_text = doc.to_plain_text() or ocr_response.text_content or ""
            doc_json_str = json.dumps(doc.to_dict()) if doc else ""
            ocr_page.ocr_data_size_bytes = len(plain_text.encode('utf-8')) + len(doc_json_str.encode('utf-8'))

            page_crop_bytes = 0
            if ocr_response.blocks:
                for block in ocr_response.blocks:
                    blk_id = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
                    if blk_id:
                        c_key = f"{project_slug}/images/extracted_{blk_id}.png"
                        try:
                            if storage.exists(c_key):
                                page_crop_bytes += storage.size(c_key)
                        except Exception:
                            pass
            ocr_page.cropped_image_size_bytes = page_crop_bytes
            session.add(ocr_page)

            # Combine and aggregate all completed single-page metrics for the PDF / book
            item_pages = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, status='COMPLETED').all()
            batch_item.engine = engine
            batch_item.total_ocr_latency_ms = sum(p.ocr_latency_ms or 0 for p in item_pages)
            batch_item.extracted_images_size_bytes = sum(p.extracted_image_size_bytes or 0 for p in item_pages)
            batch_item.cropped_images_size_bytes = sum(p.cropped_image_size_bytes or 0 for p in item_pages)
            batch_item.ocr_data_size_bytes = sum(p.ocr_data_size_bytes or 0 for p in item_pages)
            
            conf_list = [p.confidence for p in item_pages if p.confidence is not None]
            batch_item.avg_confidence = (sum(conf_list) / len(conf_list)) if conf_list else None
            p05_list = [p.p05 for p in item_pages if p.p05 is not None]
            batch_item.avg_p05 = (sum(p05_list) / len(p05_list)) if p05_list else None
            batch_item.total_blocks = sum(p.blocks or 0 for p in item_pages)
            batch_item.total_chars = sum(p.chars or 0 for p in item_pages)
            batch_item.total_engine_latency_ms = sum(p.engine_latency_ms or 0 for p in item_pages)

            # Single page operations are immediately COMPLETED upon output generation
            batch_item.status = 'COMPLETED'
            batch_item.completed_at = datetime.utcnow()
            batch_job.status = 'COMPLETED'
            batch_job.completed_at = datetime.utcnow()

            session.commit()
        except Exception as metric_err:
            logging.warning(f"Error recording single page proofing OCR metrics: {metric_err}")

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
        
        # Prepend APPLICATION_URL_PREFIX to image paths in the returned JSON blocks
        prefix = current_app.config.get("APPLICATION_URL_PREFIX") or ""
        if prefix and payload.get("blocks"):
            for block in payload["blocks"]:
                if "content" in block and block["content"]:
                    block["content"] = block["content"].replace(f'{prefix}/static/uploads/', '/static/uploads/').replace('/static/uploads/', f'{prefix}/static/uploads/')

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
        abort(500, description=_l("OCR failed: %(error)s", error=str(e)))


def _is_matching_language(text: str, lang: str) -> bool:
    """Check if the text segment matches the selected source language."""
    import re

    # Clean text to get only letters/alphabetic content.
    # If no alphabetic chars, return False.
    if not any(c.isalpha() for c in text):
        return False

    lang = lang.lower()

    # 1. English
    if lang == 'en':
        # Must contain Latin characters
        if not re.search(r'[a-zA-Z]', text):
            return False
        # Must not contain Indic script characters (Devanagari, Tamil, etc.) or Arabic/Urdu script.
        if re.search(r'[\u0900-\u0D7F\u0600-\u06FF]', text):
            return False
        return True

    # Script ranges maps for other supported source languages
    script_ranges = {
        'ta': r'[\u0B80-\u0BFF]', # Tamil
        'te': r'[\u0C00-\u0C7F]', # Telugu
        'kn': r'[\u0C80-\u0CFF]', # Kannada
        'ml': r'[\u0D00-\u0D7F]', # Malayalam
        'hi': r'[\u0900-\u097F]', # Hindi
        'sa': r'[\u0900-\u097F]', # Sanskrit
        'bn': r'[\u0980-\u09FF]', # Bengali
        'gu': r'[\u0A80-\u0AFF]', # Gujarati
        'or': r'[\u0B00-\u0B7F]', # Odia
        'pa': r'[\u0A00-\u0A7F]', # Punjabi / Gurmukhi
        'ur': r'[\u0600-\u06FF]', # Urdu / Arabic
        'mr': r'[\u0900-\u097F]', # Marathi
    }

    if lang in script_ranges:
        return bool(re.search(script_ranges[lang], text))

    return True


def _clean_translation_input(text: str) -> str:
    """Clean translation input text by replacing &nbsp; and \xa0 with normal spaces and stripping."""
    if not text:
        return ""
    # Standardize &nbsp; (including variants with spaces) and unicode non-breaking space
    cleaned = re.sub(r'&\s*nbsp\s*;*', ' ', text)
    cleaned = cleaned.replace('\xa0', ' ')
    # Normalize multiple consecutive spaces to a single space
    cleaned = re.sub(r' +', ' ', cleaned)
    return cleaned.strip()


def _clean_translation_output(text: str) -> str:
    """Clean translation output text to remove corrupted &nbsp; entities or trailing &nbsp;."""
    if not text:
        return ""
    # Remove any stray &nbsp; or &nbsp;; or & nbsp;; or \xa0 resulting from translation
    cleaned = re.sub(r'&\s*nbsp\s*;*', ' ', text)
    cleaned = cleaned.replace('\xa0', ' ')
    # Normalize multiple consecutive spaces to a single space
    cleaned = re.sub(r' +', ' ', cleaned)
    return cleaned.strip()


def _translate_html_content(html: str, source_lang: str, target_lang: str, engine: str, glossary: str = None) -> str:
    """Helper to translate plain text sections within HTML content, preserving HTML tags."""

    from kalanjiyam.utils.translation_engine import protect_dnt_and_math, restore_dnt_and_math
    protected_html, dnt_map = protect_dnt_and_math(html)

    # Split by HTML tags
    parts = re.split(r'(<[^>]+>)', protected_html)
    for i in range(len(parts)):
        # Even indices are text content, odd indices are tags
        if i % 2 == 0:
            text = parts[i]
            if text and text.strip():
                try:
                    # Segment text into sentences/lines to handle multi-language documents selectively.
                    # We split by sentence endings (.!? । ॥) followed by whitespace, or newlines.
                    # We wrap in capturing group to keep delimiters and maintain exact layout.
                    subparts = SENTENCE_SPLIT_REGEX.split(text)
                    
                    indices_to_translate = []
                    texts_to_translate = []
                    
                    for j in range(len(subparts)):
                        # Even indices are text segments, odd indices are delimiters
                        if j % 2 == 0:
                            sub_text = subparts[j]
                            if sub_text and sub_text.strip() and _is_matching_language(sub_text, source_lang):
                                indices_to_translate.append(j)
                                texts_to_translate.append(_clean_translation_input(sub_text))
                                
                    if texts_to_translate:
                        try:
                            # Batch translate all matching segments by joining them with double newlines
                            joined_text = "\n\n".join(texts_to_translate)
                            trans_kwargs = {}
                            if glossary:
                                trans_kwargs["glossary"] = glossary
                            translation_response = translate_text(
                                joined_text,
                                source_lang,
                                target_lang,
                                engine,
                                **trans_kwargs
                            )
                            # Split back the translated segments
                            translated_segments = [
                                _clean_translation_output(seg)
                                for seg in translation_response.translated_text.split("\n\n")
                            ]
                            
                            # If count doesn't match, try splitting by single/consecutive newlines
                            if len(translated_segments) != len(texts_to_translate):
                                translated_segments = [
                                    _clean_translation_output(s)
                                    for s in re.split(r'\n+', translation_response.translated_text)
                                    if s.strip()
                                ]
                                
                            if len(translated_segments) == len(texts_to_translate):
                                for idx, translated_segment in zip(indices_to_translate, translated_segments):
                                    sub_text = subparts[idx]
                                    leading_ws = sub_text[:len(sub_text) - len(sub_text.lstrip())]
                                    trailing_ws = sub_text[len(sub_text.rstrip()):]
                                    subparts[idx] = f"{leading_ws}{translated_segment}{trailing_ws}"
                            else:
                                raise ValueError("Mismatched translated segments count")
                        except Exception as batch_err:
                            logging.warning(f"Batched translation failed ({batch_err}), falling back to sequential.")
                            # Fallback: sequential translation
                            for idx in indices_to_translate:
                                sub_text = subparts[idx]
                                cleaned_input = _clean_translation_input(sub_text)
                                leading_ws = sub_text[:len(sub_text) - len(sub_text.lstrip())]
                                trailing_ws = sub_text[len(sub_text.rstrip()):]
                                
                                trans_kwargs = {}
                                if glossary:
                                    trans_kwargs["glossary"] = glossary
                                translation_response = translate_text(
                                    cleaned_input,
                                    source_lang,
                                    target_lang,
                                    engine,
                                    **trans_kwargs
                                )
                                translated_text = _clean_translation_output(translation_response.translated_text)
                                subparts[idx] = f"{leading_ws}{translated_text}{trailing_ws}"
                                
                    parts[i] = "".join(subparts)
                except Exception as e:
                    logging.error(f"Failed to translate segment '{text}': {e}")
                    raise
    result = "".join(parts)
    return restore_dnt_and_math(result, dnt_map)


def _translate_blocks(blocks: list, source_lang: str, target_lang: str, engine: str, glossary: str = None) -> None:
    """Translate multiple blocks in a single batched translation request."""
    # We will gather info for all texts we want to translate across all blocks
    translation_targets = []
    
    # Store parsed structures for all blocks
    blocks_parsed = []

    for block_idx, block in enumerate(blocks):
        content = block.get("content", "")
        if not content or not content.strip():
            blocks_parsed.append(None)
            continue

        parts = re.split(r'(<[^>]+>)', content)
        block_parts_structure = []
        
        for part_idx in range(len(parts)):
            part = parts[part_idx]
            # Even indices are text content, odd indices are tags
            if part_idx % 2 == 0:
                if part and part.strip():
                    subparts = SENTENCE_SPLIT_REGEX.split(part)
                    for subpart_idx in range(len(subparts)):
                        # Even indices of subparts are text segments
                        if subpart_idx % 2 == 0:
                            sub_text = subparts[subpart_idx]
                            if sub_text and sub_text.strip() and _is_matching_language(sub_text, source_lang):
                                stripped = _clean_translation_input(sub_text)
                                leading_ws = sub_text[:len(sub_text) - len(sub_text.lstrip())]
                                trailing_ws = sub_text[len(sub_text.rstrip()):]
                                translation_targets.append({
                                    "block_idx": block_idx,
                                    "part_idx": part_idx,
                                    "subpart_idx": subpart_idx,
                                    "original_text": stripped,
                                    "leading_ws": leading_ws,
                                    "trailing_ws": trailing_ws
                                })
                    block_parts_structure.append(subparts)
                else:
                    block_parts_structure.append(part)
            else:
                block_parts_structure.append(part)
        blocks_parsed.append(block_parts_structure)

    if not translation_targets:
        return

    texts_to_translate = [t["original_text"] for t in translation_targets]
    
    # Attempt batched translation in a single call
    try:
        joined_text = "\n\n".join(texts_to_translate)
        trans_kwargs = {}
        if glossary:
            trans_kwargs["glossary"] = glossary
        translation_response = translate_text(
            joined_text,
            source_lang,
            target_lang,
            engine,
            **trans_kwargs
        )
        translated_segments = [
            _clean_translation_output(seg)
            for seg in translation_response.translated_text.split("\n\n")
        ]

        # Fallback to single newlines if count doesn't match
        if len(translated_segments) != len(texts_to_translate):
            translated_segments = [
                _clean_translation_output(s)
                for s in re.split(r'\n+', translation_response.translated_text)
                if s.strip()
            ]

        if len(translated_segments) != len(texts_to_translate):
            raise ValueError("Mismatched translated segments count in batch translation")

        # Apply translations back to parsed structures
        for target, translated in zip(translation_targets, translated_segments):
            b_idx = target["block_idx"]
            p_idx = target["part_idx"]
            s_idx = target["subpart_idx"]
            leading = target["leading_ws"]
            trailing = target["trailing_ws"]
            blocks_parsed[b_idx][p_idx][s_idx] = f"{leading}{translated}{trailing}"

    except Exception as batch_err:
        logging.warning(f"Batched block translation failed ({batch_err}), falling back to sequential block-by-block translation.")
        # Fallback: Translate block by block sequentially
        for block in blocks:
            content = block.get("content", "")
            if content and content.strip():
                block["content"] = _translate_html_content(
                    content,
                    source_lang,
                    target_lang,
                    engine,
                    glossary=glossary
                )
        return

    # Reconstruct blocks content from blocks_parsed
    for block_idx, block in enumerate(blocks):
        parsed_structure = blocks_parsed[block_idx]
        if parsed_structure is None:
            continue
        
        parts_reconstructed = []
        for part_idx, part in enumerate(parsed_structure):
            if part_idx % 2 == 0:
                # part is a list of subparts (strings)
                if isinstance(part, list):
                    parts_reconstructed.append("".join(part))
                else:
                    parts_reconstructed.append(part)
            else:
                parts_reconstructed.append(part)
        block["content"] = "".join(parts_reconstructed)


@api.route("/translate/<project_slug>/<page_slug>/", methods=["GET", "POST"], strict_slashes=False)
@login_required
def translate(project_slug, page_slug):
    """Apply translation to the given page using the specified engine."""
    import time
    start_time = time.time()
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description=_l("Superadmins are not allowed to access project data."))
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
            abort(400, description=_l("Expected JSON payload"))
        doc_data = request.get_json() or {}

    source_lang = request.args.get('source_lang') or doc_data.get('source_lang') or 'sa'
    target_lang = request.args.get('target_lang') or doc_data.get('target_lang') or 'en'
    engine = request.args.get('engine') or doc_data.get('engine') or 'indictrans2'
    revision_id = request.args.get('revision_id', type=int)
    glossary = request.args.get('glossary') or doc_data.get('glossary') or None
    
    # Validate engine
    from kalanjiyam.utils.translation_engine import TranslationEngineFactory
    if not TranslationEngineFactory.is_supported(engine):
        abort(400, description=_l("Unsupported translation engine: %(engine)s", engine=engine))

    if source_lang == target_lang:
        abort(400, description=_l("Source and Target languages must be different."))

    if request.method == "POST":
        try:
            blocks = doc_data.get("blocks", [])
            has_content = bool(blocks or ("content" in doc_data and doc_data["content"] and doc_data["content"].strip()))
            
            if has_content:
                ensure_translation_quota_for_project(project_)
                
            if blocks:
                _translate_blocks(
                    blocks,
                    source_lang,
                    target_lang,
                    engine,
                    glossary=glossary
                )
            elif "content" in doc_data:
                content = doc_data["content"]
                if content and content.strip():
                    doc_data["content"] = _translate_html_content(
                        content,
                        source_lang,
                        target_lang,
                        engine,
                        glossary=glossary
                    )
                    
            if has_content:
                consume_translation_credit_for_project(project_)
                
                # Create PageVersion and Revision
                version_key = f"translation:{engine}:{source_lang}->{target_lang}"
                
                session = q.get_session()
                pv = session.query(db.PageVersion).filter_by(
                    page_id=page_.id,
                    version_key=version_key
                ).first()
                current_ver = pv.version if pv else 0
                
                if blocks:
                    from kalanjiyam.utils.page_document import PageDocument
                    try:
                        translated_text = PageDocument.from_dict(doc_data).to_plain_text()
                    except Exception:
                        translated_text = ""
                    content_format = "html" if doc_data.get("content_format") == "html" else "blocks"
                else:
                    translated_text = doc_data.get("content", "")
                    content_format = "plain"
                
                from kalanjiyam import consts
                from kalanjiyam.enums import SitePageStatus
                bot_user = q.user(consts.BOT_USERNAME)
                author_id = bot_user.id if bot_user else (current_user.id if current_user.is_authenticated else None)
                
                summary = f"Translation: {engine} {source_lang}->{target_lang}"
                add_revision(
                    page=page_,
                    summary=summary,
                    content=translated_text,
                    status=SitePageStatus.R0,
                    version=current_ver,
                    author_id=author_id,
                    document=doc_data if content_format in ("blocks", "html") else None,
                    content_format=content_format,
                    version_key=version_key,
                )

                # Record metrics for SINGLE_PAGE_PROOFING_TRANSLATION
                try:
                    trans_latency_ms = (time.time() - start_time) * 1000.0
                    trans_data_bytes = len(translated_text.encode('utf-8'))
                    from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage

                    batch_job = session.query(BatchJob).filter_by(
                        target_uri=f"single_page_proofing://translation/{project_slug}",
                        job_type='SINGLE_PAGE_PROOFING_TRANSLATION'
                    ).order_by(BatchJob.id.desc()).first()

                    if not batch_job:
                        batch_job = BatchJob(
                            target_uri=f"single_page_proofing://translation/{project_slug}",
                            status='IN_PROGRESS',
                            job_type='SINGLE_PAGE_PROOFING_TRANSLATION'
                        )
                        session.add(batch_job)
                        session.flush()

                    project_title = getattr(project_, 'display_title', None) or project_slug
                    batch_item = session.query(BatchItem).filter_by(job_id=batch_job.id, project_id=project_.id).first()
                    if not batch_item:
                        batch_item = BatchItem(
                            job_id=batch_job.id,
                            file_path=f"{project_title} ({project_slug})",
                            project_id=project_.id,
                            status='IN_PROGRESS',
                            total_pages=len(project_.pages),
                            source_lang=source_lang,
                            target_lang=target_lang,
                        )
                        session.add(batch_item)
                        session.flush()

                    # Ensure source_size_bytes is set on batch_item if missing
                    if not batch_item.source_size_bytes:
                        try:
                            from kalanjiyam.utils.storage import get_storage, page_image_key
                            storage = get_storage()
                            page_key = page_image_key(project_slug, page_slug)
                            if storage.exists(page_key):
                                batch_item.source_size_bytes = storage.size(page_key)
                        except Exception:
                            pass

                    p_num = int(page_slug) if page_slug.isdigit() else page_.order
                    ocr_page = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, page_number=p_num).first()
                    if not ocr_page:
                        ocr_page = BatchOcrPage(
                            batch_item_id=batch_item.id,
                            chunk_id=None,
                            page_number=p_num,
                            status='PENDING'
                        )

                    ocr_page.translation_latency_ms = trans_latency_ms
                    ocr_page.translation_data_size_bytes = trans_data_bytes
                    ocr_page.source_lang = source_lang
                    ocr_page.target_lang = target_lang
                    ocr_page.status = 'COMPLETED'
                    ocr_page.completed_at = datetime.utcnow()
                    session.add(ocr_page)

                    # Recalculate combined totals for the single PDF / project translation
                    item_pages = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, status='COMPLETED').all()
                    batch_item.total_translation_latency_ms = sum(p.translation_latency_ms or 0 for p in item_pages)
                    batch_item.translation_data_size_bytes = sum(p.translation_data_size_bytes or 0 for p in item_pages)
                    batch_item.source_lang = source_lang
                    batch_item.target_lang = target_lang

                    # Single page translation operations are immediately COMPLETED upon output generation
                    batch_item.status = 'COMPLETED'
                    batch_item.completed_at = datetime.utcnow()
                    batch_job.status = 'COMPLETED'
                    batch_job.completed_at = datetime.utcnow()

                    session.commit()
                except Exception as metric_err:
                    logging.warning(f"Error recording single page proofing translation metrics: {metric_err}")
                
            return jsonify(doc_data)
        except Exception as e:
            logging.error(f"Translation failed for {project_slug}/{page_slug} with engine {engine}: {e}")
            abort(500, description=_l("Translation failed: %(error)s", error=str(e)))

    # Get the revision to translate
    if revision_id is None:
        # Use the latest revision
        if not page_.revisions:
            abort(400, description=_l("No revisions found for this page"))
        revision = page_.revisions[-1]  # Latest revision
    else:
        revision = q.get_session().query(db.Revision).filter_by(id=revision_id).first()
        if not revision or revision.page_id != page_.id:
            abort(400, description=_l("Revision %(revision_id)s not found for this page", revision_id=revision_id))
    
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
        ensure_translation_quota_for_project(project_)
        translated_text = _translate_html_content(
            revision.content,
            source_lang,
            target_lang,
            engine,
            glossary=glossary
        )
        consume_translation_credit_for_project(project_)

        # Save translation to database
        from kalanjiyam import consts
        bot_user = q.user(consts.BOT_USERNAME)
        if bot_user is None:
            abort(500, description=_l("Bot user not found"))

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

        # Create page version and revision following the OCR version track system
        version_key = f"translation:{engine}:{source_lang}->{target_lang}"
        pv = session.query(db.PageVersion).filter_by(
            page_id=page_.id,
            version_key=version_key
        ).first()
        current_ver = pv.version if pv else 0

        from kalanjiyam.enums import SitePageStatus
        summary = f"Translation: {engine} {source_lang}->{target_lang}"
        add_revision(
            page=page_,
            summary=summary,
            content=translated_text,
            status=SitePageStatus.R0,
            version=current_ver,
            author_id=bot_user.id,
            version_key=version_key,
        )

        return translated_text
    except Exception as e:
        logging.error(f"Translation failed for {project_slug}/{page_slug} with engine {engine}: {e}")
        abort(500, description=_l("Translation failed: %(error)s", error=str(e)))


@api.route("/upload-image/<project_slug>/<page_slug>/", methods=["POST"])
@login_required
def upload_image(project_slug, page_slug):
    """Upload an image for the rich text editor."""
    if current_user.is_authenticated and current_user.is_super_admin:
        abort(403, description=_l("Superadmins are not allowed to access project data."))
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
        abort(400, description=_l("No image file provided"))
    
    file = request.files['image']
    if file.filename == '':
        abort(400, description=_l("No image file selected"))
    
    # Validate file type
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
    filename = secure_filename(file.filename)
    if '.' not in filename:
        abort(400, description=_l("File must have an extension"))
    
    file_ext = filename.rsplit('.', 1)[1].lower()
    if file_ext not in allowed_extensions:
        abort(
            400,
            description=_l(
                "File type '%(file_ext)s' not allowed. Allowed types: %(allowed_types)s",
                file_ext=file_ext,
                allowed_types=", ".join(sorted(allowed_extensions)),
            ),
        )
    
    # Validate file size (max 10MB)
    file.seek(0, 2)  # Seek to end
    file_size = file.tell()
    file.seek(0)  # Reset to beginning
    max_size = 10 * 1024 * 1024  # 10MB
    if file_size > max_size:
        abort(
            400,
            description=_l(
                "File size (%(size).2fMB) exceeds maximum allowed size (10MB)",
                size=file_size / 1024 / 1024,
            ),
        )
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
        abort(500, description=_l("Image upload failed: %(error)s", error=str(e)))


@api.route("/glossaries", methods=["GET"])
def get_glossaries():
    """Proxy available glossaries from the external translation service."""
    import httpx
    base_url = current_app.config.get("TRANSLATION_SERVICE_URL", "").rstrip("/")
    if not base_url:
        return jsonify([])
    api_key = current_app.config.get("TRANSLATION_SERVICE_API_KEY", "")
    headers = {"X-API-Key": api_key} if api_key else {}
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(f"{base_url}/glossaries", headers=headers)
        if resp.status_code == 200:
            return jsonify(resp.json())
        else:
            current_app.logger.warning(f"Translation service glossaries returned status {resp.status_code}: {resp.text}")
            return jsonify([])
    except Exception as e:
        current_app.logger.error(f"Failed to fetch glossaries from translation service: {e}")
        return jsonify([])

