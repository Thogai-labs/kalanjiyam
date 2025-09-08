"""Routes related to project pages.

The main route here is `edit`, which defines the page editor and the edit flow.
"""

from dataclasses import dataclass

from flask import Blueprint, current_app, flash, render_template, send_file, request
from flask_babel import lazy_gettext as _l
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from werkzeug.exceptions import abort
from wtforms import HiddenField, RadioField, StringField
from wtforms.validators import DataRequired
from wtforms.widgets import TextArea
import logging

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.utils import google_ocr, project_utils
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.utils.diff import revision_diff
from kalanjiyam.utils.revisions import EditError, add_revision
from kalanjiyam.views.api import bp as api

bp = Blueprint("page", __name__)


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
    #: The page content.
    content = StringField(
        _l("Page content"), widget=TextArea(), validators=[DataRequired()]
    )
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


@bp.route("/<project_slug>/<page_slug>/")
def edit(project_slug, page_slug):
    """Display the page editor."""
    ctx = _get_page_context(project_slug, page_slug)
    if ctx is None:
        abort(404)

    cur = ctx.cur
    form = EditPageForm()
    form.version.data = cur.version

    # FIXME: less hacky approach?
    status_names = {s.id: s.name for s in q.page_statuses()}
    form.status.data = status_names[cur.status_id]

    has_edits = bool(cur.revisions)
    translation_content = None
    translation_metadata = None
    available_translations = []
    if has_edits:
        latest_revision = cur.revisions[-1]
        form.content.data = latest_revision.content

        # Get all available translations for the latest revision
        session = q.get_session()
        translations = session.query(db.Translation).filter_by(
            page_id=cur.id,
            revision_id=latest_revision.id
        ).all()

        available_translations = [
            {
                'id': t.id,
                'content': t.content,
                'source_language': t.source_language,
                'target_language': t.target_language,
                'engine': t.translation_engine,
                'created_at': t.created_at
            }
            for t in translations
        ]

        # Use the first translation as default (if any exist)
        if available_translations:
            first_translation = available_translations[0]
            translation_content = first_translation['content']
            translation_metadata = {
                'source_language': first_translation['source_language'],
                'target_language': first_translation['target_language'],
                'engine': first_translation['engine']
            }
        else:
            translation_metadata = None

    is_r0 = cur.status.name == SitePageStatus.R0
    image_number = cur.slug
    page_number = _get_page_number(ctx.project, cur)

    return render_template(
        "proofing/pages/edit.html",
        conflict=None,
        cur=ctx.cur,
        form=form,
        has_edits=has_edits,
        image_number=image_number,
        is_r0=is_r0,
        page_context=ctx,
        page_number=page_number,
        project=ctx.project,
        translation_content=translation_content,
        translation_metadata=translation_metadata,
        available_translations=available_translations,
    )


@bp.route("/<project_slug>/<page_slug>/", methods=["POST"])
@login_required
def edit_post(project_slug, page_slug):
    """Submit changes through the page editor.

    Since `edit` is public on GET and needs auth on `POST`, it's cleaner to
    separate the logic here into two views.
    """
    ctx = _get_page_context(project_slug, page_slug)
    if ctx is None:
        abort(404)

    cur = ctx.cur
    form = EditPageForm()
    conflict = None

    if form.validate_on_submit():
        try:
            new_version = add_revision(
                cur,
                summary=form.summary.data,
                content=form.content.data,
                status=form.status.data,
                version=int(form.version.data),
                author_id=current_user.id,
            )
            form.version.data = new_version
            flash("Saved changes.", "success")
        except EditError:
            # FIXME: in the future, use a proper edit conflict view.
            flash("Edit conflict. Please incorporate the changes below:")
            conflict = cur.revisions[-1]
            form.version.data = cur.version

    is_r0 = cur.status.name == SitePageStatus.R0
    image_number = cur.slug
    page_number = _get_page_number(ctx.project, cur)

    # Get all available translations for the latest revision
    translation_content = None
    translation_metadata = None
    available_translations = []
    if cur.revisions:
        latest_revision = cur.revisions[-1]
        session = q.get_session()
        translations = session.query(db.Translation).filter_by(
            page_id=cur.id,
            revision_id=latest_revision.id
        ).all()

        available_translations = [
            {
                'id': t.id,
                'content': t.content,
                'source_language': t.source_language,
                'target_language': t.target_language,
                'engine': t.translation_engine,
                'created_at': t.created_at
            }
            for t in translations
        ]

        # Use the first translation as default (if any exist)
        if available_translations:
            first_translation = available_translations[0]
            translation_content = first_translation['content']
            translation_metadata = {
                'source_language': first_translation['source_language'],
                'target_language': first_translation['target_language'],
                'engine': first_translation['engine']
            }
        else:
            translation_metadata = None

    # Keep args in sync with `edit`. (We can't unify these functions easily
    # because one function requires login but the other doesn't. Helper
    # functions don't have any obvious cutting points.
    return render_template(
        "proofing/pages/edit.html",
        conflict=conflict,
        cur=ctx.cur,
        form=form,
        has_edits=True,
        image_number=image_number,
        is_r0=is_r0,
        page_context=ctx,
        page_number=page_number,
        project=ctx.project,
        translation_content=translation_content,
        translation_metadata=translation_metadata,
        available_translations=available_translations,
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
@login_required
def ocr(project_slug, page_slug):
    """Apply OCR to the given page using the specified engine."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if not page_:
        abort(404)

    # Get OCR parameters from query parameters
    engine = request.args.get('engine', 'google')
    language = request.args.get('language', 'sa')

    # Decode numeric engine values to actual engine names
    engine_map = {
        '1': 'google',
        '2': 'tesseract',
        '3': 'surya',
        '4': 'docling'
    }
    if engine in engine_map:
        engine = engine_map[engine]

    # Validate engine
    from kalanjiyam.utils.ocr_engine import OcrEngineFactory
    if engine not in OcrEngineFactory.get_supported_engines():
        abort(400, description=f"Unsupported OCR engine: {engine}")

    image_path = get_page_image_filepath(project_slug, page_slug)

    try:
        from kalanjiyam.utils.ocr_engine import run_ocr
        ocr_response = run_ocr(
            image_path, engine_name=engine, language=language)
        return ocr_response.text_content
    except Exception as e:
        logging.error(
            f"OCR failed for {project_slug}/{page_slug} with engine {engine} and language {language}: {e}")
        abort(500, description=f"OCR failed: {str(e)}")


@api.route("/translate/<project_slug>/<page_slug>/")
@login_required
def translate(project_slug, page_slug):
    """Apply translation to the given page using the specified engine."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)

    page_ = q.page(project_.id, page_slug)
    if not page_:
        abort(404)

    # Get translation parameters from query parameters
    source_lang = request.args.get('source_lang', 'sa')
    target_lang = request.args.get('target_lang', 'en')
    engine = request.args.get('engine', 'google')
    revision_id = request.args.get('revision_id', type=int)

    # Validate engine
    from kalanjiyam.utils.translation_engine import TranslationEngineFactory
    if engine not in TranslationEngineFactory.get_supported_engines():
        abort(400, description=f"Unsupported translation engine: {engine}")

    # Get the revision to translate
    if revision_id is None:
        # Use the latest revision
        if not page_.revisions:
            abort(400, description="No revisions found for this page")
        revision = page_.revisions[-1]  # Latest revision
    else:
        revision = q.get_session().query(db.Revision).filter_by(id=revision_id).first()
        if not revision or revision.page_id != page_.id:
            abort(
                400, description=f"Revision {revision_id} not found for this page")

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

        # Perform translation
        from kalanjiyam.utils.translation_engine import translate_text
        translation_response = translate_text(
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
            content=translation_response.translated_text,
            source_language=source_lang,
            target_language=target_lang,
            translation_engine=engine,
            status='completed'
        )

        session.add(new_translation)
        session.commit()

        return translation_response.translated_text
    except Exception as e:
        logging.error(
            f"Translation failed for {project_slug}/{page_slug} with engine {engine}: {e}")
        abort(500, description=f"Translation failed: {str(e)}")
