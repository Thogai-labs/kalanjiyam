import logging
import re
import os
import json
from datetime import datetime

from celery.result import GroupResult
from flask import (
    Blueprint,
    abort as flask_abort,
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
from flask_wtf import FlaskForm
from markupsafe import Markup, escape
from sqlalchemy import orm
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.exceptions import abort
from werkzeug.utils import redirect
from wtforms import (
    BooleanField,
    FieldList,
    Form,
    FormField,
    HiddenField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, ValidationError
from wtforms.widgets import TextArea
from wtforms_sqlalchemy.fields import QuerySelectField
import redis

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.utils.translation_engine import (
    get_available_translation_engines,
    get_supported_languages_list,
)
from kalanjiyam.models.proofing import OCRComparison
from kalanjiyam.tasks import app as celery_app
from kalanjiyam.tasks import ocr as ocr_tasks
from kalanjiyam.tasks.comparison import run_ocr_comparison_task
from kalanjiyam.tasks import translation as translation_tasks
from kalanjiyam.utils.ocr_types import SUPPORTED_ENGINES
from kalanjiyam.tasks import archival_extract as archival_tasks
from kalanjiyam.tasks import metadata as metadata_tasks
from kalanjiyam.utils import archival_description as ad
from kalanjiyam.utils import archival_taxonomy as at
from kalanjiyam.utils import project_metadata as pm
from kalanjiyam.utils import project_utils, proofing_utils
from kalanjiyam.utils.revisions import add_revision
from kalanjiyam.views.proofing.decorators import moderator_required, p2_required
from kalanjiyam.views.proofing.stats import calculate_stats

bp = Blueprint("project", __name__)
LOG = logging.getLogger(__name__)

# Initialize Redis client
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


@bp.before_request
def _enforce_project_access():
    if current_user.is_authenticated and current_user.is_super_admin:
        restricted_endpoints = {
            "proofing.project.download",
            "proofing.project.download_as_text",
            "proofing.project.download_as_xml",
            "proofing.project.download_as_json",
            "proofing.project.download_as_html",
            "proofing.project.search",
            "proofing.project.replace",
            "proofing.project.submit_changes",
            "proofing.project.confirm_changes",
        }
        if request.endpoint in restricted_endpoints:
            flask_abort(
                403, description=_l("Superadmins are not allowed to view project data.")
            )

    slug = request.view_args.get("slug") if request.view_args else None
    if not slug:
        return None
    project_ = q.project(slug)
    if project_ is None:
        return None
    if not q.user_can_view_proofing_project(current_user, project_):
        flask_abort(403)
    return None


def _is_valid_page_number_spec(_, field):
    try:
        _ = project_utils.parse_page_number_spec(field.data)
    except Exception as e:
        raise ValidationError(_l("The page number spec isn't valid.")) from e


class EditMetadataForm(FlaskForm):
    display_title = StringField(
        _l("Display title"),
        render_kw={
            "placeholder": _l("e.g. Avantisundarīkathā"),
        },
        validators=[DataRequired()],
    )
    description = StringField(
        _l("Description (optional)"),
        widget=TextArea(),
        render_kw={
            "placeholder": _l(
                "What is this book about? Why is this project interesting?"
            ),
        },
    )
    is_publicly_viewable = BooleanField(
        _l("Make public (visible to everyone, including guests)")
    )
    page_numbers = StringField(
        _l("Page numbers (optional)"),
        widget=TextArea(),
        validators=[_is_valid_page_number_spec],
        render_kw={
            "placeholder": _l("Coming soon."),
        },
    )
    genre = QuerySelectField(
        query_factory=q.genres, allow_blank=True, blank_text=_l("(none)")
    )

    print_title = StringField(
        _l("Print title"),
        render_kw={
            "placeholder": _l(
                "e.g. Śrīdaṇḍimahākaviviracitam avantisundarīkathā nāma gadyakāvyam"
            ),
        },
    )
    author = StringField(
        _l("Author"),
        render_kw={
            "placeholder": _l("The author of the original work, e.g. Kalidasa."),
        },
    )
    editor = StringField(
        _l("Editor"),
        render_kw={
            "placeholder": _l(
                "The person or organization that created this edition, e.g. M.R. Kale."
            ),
        },
    )
    publisher = StringField(
        _l("Publisher"),
        render_kw={
            "placeholder": _l(
                "The original publisher of this book, e.g. Nirnayasagar."
            ),
        },
    )
    worldcat_link = StringField(
        _l("Worldcat link"),
        render_kw={
            "placeholder": _l("A link to this book's entry on worldcat.org."),
        },
    )
    publication_year = StringField(
        _l("Publication year"),
        render_kw={
            "placeholder": _l("The year in which this specific edition was published."),
        },
    )

    notes = StringField(
        _l("Notes (optional)"),
        widget=TextArea(),
        render_kw={
            "placeholder": _l("Internal notes for scholars and other proofreaders."),
        },
    )


class ProjectMetadataForm(FlaskForm):
    """The Metadata tab. Every field is directly editable.

    Extraction only ever *seeds* these values -- what a moderator saves here
    is canonical, and a later extraction is staged rather than applied.
    """

    print_title = StringField(_l("Print title"))
    subtitle = StringField(_l("Subtitle"))
    author = StringField(_l("Author"))
    editor = StringField(_l("Editor / translator"))
    publisher = StringField(_l("Publisher"))
    place_of_publication = StringField(_l("Place of publication"))
    publication_year = StringField(_l("Publication year"))
    edition = StringField(_l("Edition"))
    series = StringField(_l("Series"))
    subject = StringField(_l("Subject"))
    worldcat_link = StringField(_l("Worldcat link"))
    genre = QuerySelectField(
        query_factory=q.genres, allow_blank=True, blank_text=_l("(none)")
    )

    languages = StringField(
        _l("Languages"),
        widget=TextArea(),
        render_kw={
            "rows": 4,
            "placeholder": _l(
                "One per line: code, script, role\ne.g. sa, Deva, primary"
            ),
        },
    )
    summary = StringField(
        _l("Summary"),
        widget=TextArea(),
        render_kw={"rows": 6},
    )
    keywords = StringField(
        _l("Keywords"),
        widget=TextArea(),
        render_kw={"rows": 2, "placeholder": _l("Comma-separated.")},
    )
    toc = StringField(
        _l("Table of contents"),
        widget=TextArea(),
        render_kw={
            "rows": 8,
            "placeholder": _l("One entry per line: label | page"),
        },
    )


class MetadataAcceptForm(FlaskForm):
    """Promote a staged extraction over the current values.

    Kept after the sampling extractor was retired so that a run staged before
    the changeover can still be applied instead of being stranded.
    """


class DescriptionExtractForm(FlaskForm):
    """Trigger a full-text archival extraction."""

    force = BooleanField(_l("Re-read every window"))


class DescriptionCurateForm(FlaskForm):
    """Archivist entry for one tag of the description.

    One tag per submit rather than one big form: the write-locked tags are prose
    an archivist writes deliberately, and a single form over twenty-two fields
    turns every small correction into a whole-description save.
    """

    tag_code = HiddenField(validators=[DataRequired()])
    value = StringField(
        _l("Value"),
        widget=TextArea(),
        render_kw={"rows": 6},
    )

    def validate_tag_code(self, field):
        if field.data not in at.BY_CODE:
            raise ValidationError(_l("Unknown tag."))
        tag = at.BY_CODE[field.data]
        if tag.kind not in (at.KIND_TEXT, at.KIND_PROSE):
            # Entity and relation tags are lists of structured access points.
            # A textarea cannot express one, and accepting free text here would
            # quietly store a string where every reader expects a list.
            raise ValidationError(_l("This tag cannot be edited as text."))


class DescriptionSaveForm(FlaskForm):
    """CSRF for the whole-description form.

    The editable tags vary with the taxonomy, so the fields are read off the
    request by prefix rather than declared here. `DescriptionCurateForm` still
    handles a single tag, which is what the per-tag validation lives on.
    """


class MatchForm(Form):
    selected = BooleanField()
    replace = HiddenField(validators=[DataRequired()])


class SearchForm(FlaskForm):
    class Meta:
        csrf = False

    query = StringField(_l("Query"), validators=[DataRequired()])


class DeleteProjectForm(FlaskForm):
    slug = StringField(_l("Slug"), validators=[DataRequired()])


class ReplaceForm(SearchForm):
    class Meta:
        csrf = False

    replace = StringField(_l("Replace"), validators=[DataRequired()])


def validate_matches(form, field):
    for match_form in field:
        if match_form.errors:
            raise ValidationError(_l("Invalid match form values."))


class PreviewChangesForm(ReplaceForm):
    class Meta:
        csrf = False

    matches = FieldList(FormField(MatchForm), validators=[validate_matches])
    submit = SubmitField(_l("Preview changes"))


class ConfirmChangesForm(ReplaceForm):
    class Meta:
        csrf = False

    confirm = SubmitField(_l("Confirm"))
    cancel = SubmitField(_l("Cancel"))


@bp.route("/<slug>/")
def summary(slug):
    """Show basic information about the project."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    session = q.get_session()
    recent_revisions = (
        session.query(db.Revision)
        .filter_by(project_id=project_.id)
        .order_by(db.Revision.created.desc())
        .limit(10)
        .all()
    )

    page_rules = project_utils.parse_page_number_spec(project_.page_numbers)
    page_titles = project_utils.apply_rules(len(project_.pages), page_rules)
    return render_template(
        "proofing/projects/summary.html",
        project=project_,
        pages=zip(page_titles, project_.pages),
        recent_revisions=recent_revisions,
    )


@bp.route("/<slug>/activity")
def activity(slug):
    """Show recent activity on this project."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    session = q.get_session()
    recent_revisions = (
        session.query(db.Revision)
        .filter_by(project_id=project_.id)
        .order_by(db.Revision.created.desc())
        .limit(100)
        .all()
    )

    page_ids = {r.page_id for r in recent_revisions}
    if page_ids:
        all_page_revisions = (
            session.query(db.Revision)
            .filter(db.Revision.page_id.in_(page_ids))
            .order_by(db.Revision.page_id, db.Revision.created.desc())
            .all()
        )
    else:
        all_page_revisions = []

    revisions_by_page = {}
    for r in all_page_revisions:
        revisions_by_page.setdefault(r.page_id, []).append(r)

    from kalanjiyam.utils.diff import revision_diff
    from kalanjiyam.utils import proofing_utils

    for r in recent_revisions:
        page_revs = revisions_by_page.get(r.page_id, [])
        try:
            idx = page_revs.index(r)
        except ValueError:
            idx = -1

        cur_text = proofing_utils.revision_plain_content(r)
        if idx != -1 and idx + 1 < len(page_revs):
            prev_r = page_revs[idx + 1]
            prev_text = proofing_utils.revision_plain_content(prev_r)
            r.prev_revision_id = prev_r.id
            r.diff = revision_diff(prev_text, cur_text)
        else:
            r.prev_revision_id = None
            if cur_text:
                r.diff = revision_diff("", cur_text)
            else:
                r.diff = None

    recent_activity = [("revision", r.created, r) for r in recent_revisions]
    recent_activity.append(("project", project_.created_at, project_))

    return render_template(
        "proofing/projects/activity.html",
        project=project_,
        recent_activity=recent_activity,
    )


@bp.route("/<slug>/edit", methods=["GET", "POST"])
def edit(slug):
    """Edit the project's metadata."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    # Restrict guests to editing only their own created projects
    if not current_user.is_authenticated:
        fingerprint_id = request.cookies.get("device_fingerprint")
        if project_.creator_id is not None or project_.fingerprint_id != fingerprint_id:
            abort(403)

    form = EditMetadataForm(obj=project_)
    if form.validate_on_submit():
        session = q.get_session()
        form.populate_obj(project_)
        session.commit()

        # Title, author, and visibility are all indexed on every page
        # document, so a metadata edit means the whole project is stale.
        from kalanjiyam.tasks.search_index import enqueue_project

        enqueue_project(project_.id)

        flash(_l("Saved changes."), "success")
        return redirect(url_for("proofing.project.summary", slug=slug))

    delete_form = DeleteProjectForm()
    return render_template(
        "proofing/projects/edit.html",
        project=project_,
        form=form,
        delete_form=delete_form,
        supported_engines=SUPPORTED_ENGINES,
    )


@bp.route("/<slug>/metadata", methods=["GET", "POST"])
@moderator_required
def metadata(slug):
    """View and edit the project's extracted metadata."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    data = project_.extracted_metadata or {}
    content = data.get("content") or {}
    form = ProjectMetadataForm(obj=project_)

    if form.validate_on_submit():
        session = q.get_session()
        for field in (
            "print_title",
            "subtitle",
            "author",
            "editor",
            "publisher",
            "place_of_publication",
            "publication_year",
            "edition",
            "series",
            "subject",
            "worldcat_link",
        ):
            setattr(project_, field, (getattr(form, field).data or "").strip())
        project_.genre = form.genre.data

        merged = dict(data)
        merged["schema_version"] = metadata_tasks.SCHEMA_VERSION
        merged["languages"] = pm.parse_languages(form.languages.data)
        merged["content"] = {
            **content,
            "summary": (form.summary.data or "").strip() or None,
            "keywords": pm.parse_list(form.keywords.data),
            "toc": pm.parse_toc(form.toc.data),
        }
        project_.extracted_metadata = merged
        # SQLAlchemy does not track in-place mutation of a JSON column.
        flag_modified(project_, "extracted_metadata")
        session.commit()

        # Title and author are indexed on every page document.
        from kalanjiyam.tasks.search_index import enqueue_project

        enqueue_project(project_.id)

        flash(_l("Saved metadata."), "success")
        return redirect(url_for("proofing.project.metadata", slug=slug))

    if request.method == "GET":
        form.languages.data = pm.format_languages(data.get("languages"))
        form.summary.data = content.get("summary") or ""
        form.keywords.data = pm.format_list(content.get("keywords"))
        form.toc.data = pm.format_toc(content.get("toc"))

    return render_template(
        "proofing/projects/metadata.html",
        project=project_,
        form=form,
        metadata=data,
        derived=data.get("derived") or {},
        provenance=data.get("provenance") or {},
        staged=data.get("staged"),
        entities=(content.get("entities") or {}),
        colophon=(content.get("colophon") or {}),
        accept_form=MetadataAcceptForm(),
    )


# The sampling extractor that used to fill this tab is retired. It read the front
# matter and a handful of body pages; the description tab reads every page and
# cites its evidence, and writes the columns below through
# `archival_description.write_down`. Running both would mean two passes over one
# PDF and two competing answers to "what is this called".


@bp.route("/<slug>/metadata/accept", methods=["POST"])
@moderator_required
def metadata_accept(slug):
    """Promote a staged extraction over the current values."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    form = MetadataAcceptForm()
    if not form.validate_on_submit():
        flash(_l("Could not load the new extraction."), "error")
        return redirect(url_for("proofing.project.metadata", slug=slug))

    session = q.get_session()
    if metadata_tasks.accept_staged(session, project_):
        flash(_l("Loaded the new extraction."), "success")
    else:
        flash(_l("There is no pending extraction to load."), "error")
    return redirect(url_for("proofing.project.metadata", slug=slug))


# Archival description
# --------------------
#
# The catalogue record itself: twenty-two tags to ISAD(G)/ISAAR(CPF)/RiC, filled
# by a full-text extraction run and corrected by an archivist.
#
# Three of the tags -- REFERENCE, CUSTODIAL HISTORY, ACCESS -- are never sent to
# the extractor and can only be typed here. They come from the accession record,
# not the page text, and a model asked for a custodial history will invent one.


@bp.route("/<slug>/description")
@moderator_required
def description(slug):
    """The archival description, generated values merged with curated ones."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    session = q.get_session()
    view = ad.describe(session, project_.id)
    progress = archival_tasks.get_progress(project_.id)
    return render_template(
        "proofing/projects/description.html",
        project=project_,
        extract_form=DescriptionExtractForm(),
        save_form=DescriptionSaveForm(formdata=None),
        field_prefix=_CURATE_PREFIX,
        progress=progress,
        running=bool(
            progress and progress.get("status") == archival_tasks.STATUS_RUNNING
        ),
        taxonomy=at,
        **view,
    )


@bp.route("/<slug>/description/log")
@bp.route("/<slug>/description/log/<int:run_id>")
@moderator_required
def description_log(slug, run_id=None):
    """The window-by-window record of an extraction run.

    Separate from the description tab because it answers a different question.
    The tab says what the catalogue record is; this says how it was arrived at --
    which pages each call covered, what it cost, and what the service said about
    the windows that failed. Without it a partial run reports a number of missing
    pages and no way to find out why.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    session = q.get_session()
    log = ad.run_log(session, project_.id, run_id)
    if run_id is not None and log["run"] is None:
        abort(404)

    return render_template(
        "proofing/projects/description_log.html", project=project_, **log
    )


@bp.route("/<slug>/description/extract", methods=["POST"])
@moderator_required
def description_extract(slug):
    """Enqueue a full-text extraction run."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    form = DescriptionExtractForm()
    if not form.validate_on_submit():
        flash(_l("Could not start the extraction."), "error")
        return redirect(url_for("proofing.project.description", slug=slug))

    try:
        archival_tasks.extract_archival_metadata.delay(
            project_.id,
            force=bool(form.force.data),
            enqueued_at=datetime.utcnow().isoformat(),
        )
        flash(
            _l("Extraction started. It reads every page, so it takes a while."),
            "success",
        )
    except Exception:
        LOG.exception("could not enqueue archival extraction for %s", slug)
        flash(_l("Could not start the extraction."), "error")

    return redirect(url_for("proofing.project.description", slug=slug))


@bp.route("/<slug>/description/status")
@moderator_required
def description_status(slug):
    """Poll a running extraction."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)
    return jsonify(archival_tasks.get_progress(project_.id) or {"status": "idle"})


@bp.route("/<slug>/description/curate", methods=["POST"])
@moderator_required
def description_curate(slug):
    """Save an archivist's value for one tag.

    Curated values live in their own rows and are never touched by an extraction
    run, so this cannot be undone by a later regenerate. Submitting an empty box
    clears the curation and restores whatever the extractor found.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    form = DescriptionCurateForm()
    if not form.validate_on_submit():
        flash(_l("Could not save that value."), "error")
        return redirect(url_for("proofing.project.description", slug=slug))

    session = q.get_session()
    value = (form.value.data or "").strip()
    user_id = current_user.id if current_user.is_authenticated else None

    if value:
        ad.set_curated(session, project_.id, form.tag_code.data, value, user_id)
        flash(_l("Saved %(tag)s.", tag=form.tag_code.data), "success")
    elif ad.clear_curated(session, project_.id, form.tag_code.data):
        flash(_l("Cleared %(tag)s.", tag=form.tag_code.data), "success")
    else:
        flash(_l("There was nothing to clear."), "error")

    session.commit()
    return redirect(url_for("proofing.project.description", slug=slug))


#: Prefix for the per-tag inputs of the whole-description form. Tag codes contain
#: spaces, so the code is carried in the field name rather than a parallel list.
_CURATE_PREFIX = "tag__"


@bp.route("/<slug>/description/save", methods=["POST"])
@moderator_required
def description_save(slug):
    """Save every edited tag of the description in one submit.

    Only tags whose box actually changed are written. Restamping all twenty-two
    rows on each save would make `curated_by` and `curated_at` meaningless -- they
    are meant to say who wrote *this* value, not who last pressed Save.

    Unknown codes and non-text tags are ignored rather than rejected: the form is
    generated from the taxonomy, so anything else in the payload did not come from
    this page, and failing the whole save would lose the archivist's other edits.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    if not DescriptionSaveForm().validate_on_submit():
        flash(_l("Could not save the description."), "error")
        return redirect(url_for("proofing.project.description", slug=slug))

    session = q.get_session()
    view = ad.describe(session, project_.id)["views"]
    user_id = current_user.id if current_user.is_authenticated else None
    saved = cleared = 0

    for name, raw in request.form.items():
        if not name.startswith(_CURATE_PREFIX):
            continue
        code = name[len(_CURATE_PREFIX) :]
        tag = at.BY_CODE.get(code)
        if tag is None or tag.kind not in (at.KIND_TEXT, at.KIND_PROSE):
            continue

        # Compared against what the box was *rendered* with, which for an
        # untouched tag is the extractor's own value. Comparing against the
        # curated value instead would turn every generated value into a curated
        # one the first time anybody pressed Save.
        value = (raw or "").strip()
        current = view[code].value
        if value == (current or "").strip():
            continue

        if value:
            ad.set_curated(session, project_.id, code, value, user_id)
            saved += 1
        elif ad.clear_curated(session, project_.id, code):
            cleared += 1

    session.commit()

    if saved or cleared:
        flash(
            _l("Saved %(saved)s, cleared %(cleared)s.", saved=saved, cleared=cleared),
            "success",
        )
    else:
        flash(_l("Nothing changed."), "success")
    return redirect(url_for("proofing.project.description", slug=slug))


@bp.route("/<slug>/batch")
def batch_processes(slug):
    """View listing batch processes like OCR and translation."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    # Restrict guests to editing only their own created projects
    if not current_user.is_authenticated:
        fingerprint_id = request.cookies.get("device_fingerprint")
        if project_.creator_id is not None or project_.fingerprint_id != fingerprint_id:
            abort(403)

    return render_template(
        "proofing/projects/batch.html",
        project=project_,
    )


@bp.route("/<slug>/search-operations")
def search_operations(slug):
    """View listing search-related operations."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    # Restrict guests to editing only their own created projects
    if not current_user.is_authenticated:
        fingerprint_id = request.cookies.get("device_fingerprint")
        if project_.creator_id is not None or project_.fingerprint_id != fingerprint_id:
            abort(403)

    return render_template(
        "proofing/projects/search-operations.html",
        project=project_,
    )


@bp.route("/<slug>/delete", methods=["POST"])
def delete_project(slug):
    """Delete a project and its associated files."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    if current_user.is_authenticated:
        is_super_admin = getattr(current_user, "is_super_admin", False)
        if not is_super_admin:
            is_open_tenant = any(g.slug == "open-tenant" for g in project_.groups)
            if is_open_tenant:
                # Registered user must be the creator of the open-tenant project
                if project_.creator_id != current_user.id:
                    abort(403)
            else:
                # Organization project: must be an org admin of this project's organization
                is_org_admin = getattr(current_user, "is_org_admin", False) and any(
                    g.id == getattr(current_user, "organization_id", None)
                    for g in project_.groups
                )
                if not is_org_admin:
                    abort(403)
    else:
        # Restrict guests to deleting only their own created projects
        fingerprint_id = request.cookies.get("device_fingerprint")
        if project_.creator_id is not None or project_.fingerprint_id != fingerprint_id:
            abort(403)

    form = DeleteProjectForm()
    if form.validate_on_submit():
        if form.slug.data == slug:
            session = q.get_session()
            from kalanjiyam.models.batch import BatchItem

            session.query(BatchItem).filter_by(project_id=project_.id).update(
                {"project_id": None}
            )
            deleted_project_id = project_.id
            session.delete(project_)
            session.commit()

            # Drop the project's documents from the search index too.
            from kalanjiyam.tasks.search_index import enqueue_project_removal

            enqueue_project_removal(deleted_project_id)

            from kalanjiyam.utils.storage import get_storage, project_prefix

            get_storage().delete_prefix(project_prefix(slug))

            flash(_l("Deleted project %(slug)s", slug=slug), "success")
            return redirect(url_for("proofing.index"))
        else:
            flash(_l("Deletion failed (incorrect slug typed)."), "error")
    else:
        flash(_l("Deletion failed (validation error)."), "error")

    return redirect(url_for("proofing.project.edit", slug=slug))


@bp.route("/<slug>/download/")
def download(slug):
    """Download the project in various output formats."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    from kalanjiyam.utils.storage import project_docx_key, get_storage

    is_docx = get_storage().exists(project_docx_key(slug))

    return render_template(
        "proofing/projects/download.html", project=project_, is_docx=is_docx
    )


@bp.route("/<slug>/download/text")
def download_as_text(slug):
    """Download the project as plain text."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    from kalanjiyam.utils.proofing_utils import get_main_revision

    content_blobs = []
    for p in project_.pages:
        rev = get_main_revision(p)
        content_blobs.append(proofing_utils.revision_plain_content(rev) if rev else "")

    raw_text = proofing_utils.to_plain_text(content_blobs)

    response = make_response(raw_text, 200)
    response.mimetype = "text/plain"
    return response


@bp.route("/<slug>/download/xml")
def download_as_xml(slug):
    """Download the project as TEI XML.

    This XML will likely have various errors, but it is correct enough that it
    still saves a lot of manual work.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    project_meta = {
        "title": project_.display_title,
        "author": project_.author,
        "publication_year": project_.publication_year,
        "publisher": project_.publisher,
        "editor": project_.editor,
    }
    project_meta = {k: v or "TODO" for k, v in project_meta.items()}
    from kalanjiyam.utils.document_storage import load_revision_document as _load_doc

    from kalanjiyam.utils.proofing_utils import get_main_revision

    has_blocks = any(
        (
            lambda rev: rev
            and getattr(rev, "content_format", "plain") == "blocks"
            and _load_doc(rev)
        )(get_main_revision(p))
        for p in project_.pages
    )
    if has_blocks:
        xml_blob = proofing_utils.documents_to_tei_xml(project_meta, project_.pages)
    else:
        content_blobs = []
        for p in project_.pages:
            rev = get_main_revision(p)
            content_blobs.append(rev.content if rev else "")
        xml_blob = proofing_utils.to_tei_xml(project_meta, content_blobs)

    response = make_response(xml_blob, 200)
    response.mimetype = "text/xml"
    return response


@bp.route("/<slug>/download/json")
def download_as_json(slug):
    """Download the project as a PageDocument JSON bundle."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    blob = proofing_utils.documents_to_json_bundle(project_, project_.pages)
    response = make_response(blob, 200)
    response.mimetype = "application/json"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{slug}-documents.json"'
    )
    return response


@bp.route("/<slug>/download/html")
def download_as_html(slug):
    """Download layout-faithful HTML export as a ZIP archive with images."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    blob = proofing_utils.documents_to_html_zip(project_, project_.pages, replica=True)
    response = make_response(blob, 200)
    response.mimetype = "application/zip"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{slug}-replica.zip"'
    )
    return response


@bp.route("/<slug>/download/docx")
def download_as_docx(slug):
    """Download project pages compiled into a single DOCX document."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    blob = proofing_utils.documents_to_docx(project_.pages)
    response = make_response(blob, 200)
    response.mimetype = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{slug}.docx"'
    return response


@bp.route("/<slug>/download/pdf")
def download_as_pdf(slug):
    """Download project pages compiled into a single PDF document in replica layout."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    blob = proofing_utils.documents_to_pdf(project_, project_.pages)
    response = make_response(blob, 200)
    response.mimetype = "application/pdf"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{slug}-replica.pdf"'
    )
    return response


@bp.route("/<slug>/stats")
def stats(slug):
    """Show basic statistics about this project.

    Currently, these stats don't show any sensitive information.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    stats_ = calculate_stats(project_)
    comparisons = (
        q.get_session()
        .query(OCRComparison)
        .filter_by(project_id=project_.id)
        .order_by(OCRComparison.created_at.desc())
        .all()
    )
    return render_template(
        "proofing/projects/stats.html",
        project=project_,
        stats=stats_,
        comparisons=comparisons,
        supported_engines=SUPPORTED_ENGINES,
    )


@bp.route("/<slug>/stats/compare", methods=["POST"])
def run_comparison(slug):
    """Run an OCR comparison against ground truth."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    engine = request.form.get("engine")
    if not engine:
        flash(_l("Please select an engine."), "danger")
        return redirect(url_for("proofing.project.stats", slug=slug))

    if engine not in SUPPORTED_ENGINES:
        flash(_l("Unsupported OCR engine: %(engine)s", engine=engine), "danger")
        return redirect(url_for("proofing.project.stats", slug=slug))

    session = q.get_session()
    comparison = OCRComparison(project_id=project_.id, engine=engine, status="pending")
    session.add(comparison)
    session.commit()

    run_ocr_comparison_task.delay(
        comparison.id, current_app.config["KALANJIYAM_ENVIRONMENT"]
    )

    flash(_l("Comparison started with %(engine)s.", engine=engine), "success")
    return redirect(url_for("proofing.project.stats", slug=slug))


@bp.route("/<slug>/stats/compare/<int:comparison_id>")
def comparison_details(slug, comparison_id):
    """Show detailed results for a comparison."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    session = q.get_session()
    comparison = session.query(OCRComparison).get(comparison_id)
    if not comparison or comparison.project_id != project_.id:
        abort(404)

    # Ensure JSON columns are deserialized if they are returned as string (SQLite fallback)
    import json

    if isinstance(comparison.page_results, str):
        try:
            comparison.page_results = json.loads(comparison.page_results)
        except Exception as e:
            LOG.warning(f"Failed to deserialize page_results: {e}")
    if isinstance(comparison.summary_metrics, str):
        try:
            comparison.summary_metrics = json.loads(comparison.summary_metrics)
        except Exception as e:
            LOG.warning(f"Failed to deserialize summary_metrics: {e}")

    return render_template(
        "proofing/projects/comparison_details.html",
        project=project_,
        comparison=comparison,
    )


@bp.route("/<slug>/search")
@login_required
def search(slug):
    """Search across all of the project's pages.

    This is useful for finding typos that repeat across the project.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    form = SearchForm(request.args)
    if not form.validate():
        return render_template(
            "proofing/projects/search.html", project=project_, form=form
        )

    query = form.query.data
    results = []
    for page_ in project_.pages:
        if not page_.revisions:
            continue

        matches = []

        latest = page_.revisions[-1]
        for line in latest.content.splitlines():
            if query in line:
                matches.append(
                    {
                        "text": escape(line).replace(
                            query, Markup(f"<mark>{escape(query)}</mark>")
                        ),
                    }
                )
        if matches:
            results.append(
                {
                    "slug": page_.slug,
                    "matches": matches,
                }
            )
    return render_template(
        "proofing/projects/search.html",
        project=project_,
        form=form,
        query=query,
        results=results,
    )


def _replace_text(project_, replace_form: ReplaceForm, query: str, replace: str):
    """
    Gather all matches for the "query" string and pair them the "replace" string.
    """

    results = []

    query_pattern = re.compile(
        query, re.UNICODE
    )  # Compile the regex pattern with Unicode support

    LOG.debug(f"Search/Replace text with {query} and {replace}")
    for page_ in project_.pages:
        if not page_.revisions:
            continue
        matches = []
        latest = page_.revisions[-1]
        LOG.debug(f"{__name__}: {page_.slug}")
        for line_num, line in enumerate(latest.content.splitlines()):
            if query_pattern.search(line):
                try:
                    marked_query = query_pattern.sub(
                        lambda m: Markup(f"<mark>{escape(m.group(0))}</mark>"), line
                    )
                    marked_replace = query_pattern.sub(
                        Markup(f"<mark>{escape(replace)}</mark>"), line
                    )
                    LOG.debug(f"Search/Replace > marked query: {marked_query}")
                    LOG.debug(f"Search/Replace > marked replace: {marked_replace}")
                    matches.append(
                        {
                            "query": marked_query,
                            "replace": marked_replace,
                            "checked": False,
                            "line_num": line_num,
                        }
                    )
                except TimeoutError:
                    # Handle the timeout for regex operation, e.g., log a warning or show an error message
                    LOG.warning(
                        f"Regex operation timed out for line {line_num}: {line}"
                    )

        if matches:
            results.append(
                {
                    "slug": page_.slug,
                    "matches": matches,
                }
            )

    return results


@bp.route("/<slug>/replace", methods=["GET", "POST"])
@p2_required
def replace(slug):
    """Search and replace a string across all of the project's pages.

    This is useful to replace a string across the project in one shot.
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    form = ReplaceForm(request.form)
    if not form.validate():
        invalid_keys = list(form.errors.keys())
        LOG.debug(f"Invalid form - {request.method}, invalid keys: {invalid_keys}")
        return render_template(
            "proofing/projects/replace.html", project=project_, form=ReplaceForm()
        )

    # search for "query" string and replace with "update" string
    query = form.query.data
    replace = form.replace.data
    results = _replace_text(project_, replace_form=form, query=query, replace=replace)
    num_matches = sum(len(r["matches"]) for r in results)

    return render_template(
        "proofing/projects/replace.html",
        project=project_,
        form=form,
        submit_changes_form=PreviewChangesForm(),
        query=query,
        replace=replace,
        num_matches=num_matches,
        results=results,
    )


def _select_changes(project_, selected_keys, query: str, replace: str):
    """
    Mark "query" strings
    """
    results = []
    LOG.debug(f"{__name__}: Mark changes with {query} and {replace}")
    query_pattern = re.compile(
        query, re.UNICODE
    )  # Compile the regex pattern with Unicode support
    for page_ in project_.pages:
        if not page_.revisions:
            continue

        latest = page_.revisions[-1]
        matches = []
        for line_num, line in enumerate(latest.content.splitlines()):

            form_key = f"match{page_.slug}-{line_num}"
            replace_form_key = f"match{page_.slug}-{line_num}-replace"

            if selected_keys.get(form_key) == "selected":
                LOG.debug(f"{__name__}: {form_key}: {selected_keys.get(form_key)}")
                LOG.debug(
                    f"{__name__}: {replace_form_key}: {request.form.get(replace_form_key)}"
                )
                LOG.debug(f"{__name__}: {form_key}: Appended")
                replaced_line = query_pattern.sub(replace, line)
                matches.append(
                    {
                        "query": line,
                        "replace": replaced_line,
                        "line_num": line_num,
                    }
                )

        results.append({"page": page_, "matches": matches})
        LOG.debug(f"{__name__}: Total matches appended: {len(matches)}")

    selected_count = sum(value == "selected" for value in selected_keys.values())
    LOG.debug(f"{__name__} > Number of selected changes = {selected_count}")

    return render_template(
        "proofing/projects/confirm_changes.html",
        project=project_,
        form=ConfirmChangesForm(),
        query=query,
        replace=replace,
        results=results,
    )


@bp.route("/<slug>/submit-changes", methods=["GET", "POST"])
@p2_required
def submit_changes(slug):
    """Submit selected changes across all of the project's pages.

    This is useful to replace a string across the project in one shot.
    """

    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    LOG.debug(
        f"{__name__}: SUBMIT_CHANGES --- {request.method} > {list(request.form.keys())}"
    )

    # FIXME: find a way to validate this form. Current `matches` are coming in the way of validators.
    form = PreviewChangesForm(request.form)
    # if not form.validate():
    #     # elif request.form.get("form_submitted") is None:
    #     invalid_keys = list(form.errors.keys())
    #     LOG.debug(f'{__name__}: Invalid form values - {request.method}, invalid keys: {invalid_keys}')
    #     return redirect(url_for("proofing.project.replace", slug=slug))

    render = None
    # search for "query" string and replace with "update" string
    query = form.query.data
    replace = form.replace.data

    LOG.debug(
        f"{__name__}: ({request.method})>  Got to submit method with {query}->{replace} "
    )
    LOG.debug(f"{__name__}: {request.method} > {list(request.form.keys())}")
    selected_keys = {
        key: value
        for key, value in request.form.items()
        if key.startswith("match") and not key.endswith("replace")
    }
    render = _select_changes(project_, selected_keys, query=query, replace=replace)

    return render


@bp.route("/<slug>/confirm_changes", methods=["GET", "POST"])
@p2_required
def confirm_changes(slug):
    """Confirm changes to replace a string across all of the project's pages."""
    project_ = q.project(slug)
    if project_ is None:
        abort(404)
    LOG.debug(
        f"{__name__}: confirm_changes {request.method} > Keys: {list(request.form.keys())}, Items: {list(request.form.items())}"
    )
    form = ConfirmChangesForm(request.form)
    if not form.validate():
        flash(_l("Invalid input."), "danger")
        invalid_keys = list(form.errors.keys())
        LOG.error(
            f"{__name__}: Invalid form - {request.method}, invalid keys: {invalid_keys}"
        )
        return redirect(url_for("proofing.project.replace", slug=slug))

    if form.confirm.data:
        LOG.debug(f"{__name__}: {request.method} > Confirmed!")
        query = form.query.data
        replace = form.replace.data

        # Get the changes from the form and store them in a list
        pages = {}

        # Iterate over the dictionary `request.form`
        for key, value in request.form.items():
            # Check if key matches the pattern
            match = re.match(r"match(\d+)-(\d+)-replace", key)
            if match:
                # Extract page_slug and line_num from the key
                page_slug = match.group(1)
                line_num = int(match.group(2))
                if page_slug not in pages:
                    pages[page_slug] = {}
                pages[page_slug][line_num] = value

        for page_slug, changed_lines in pages.items():
            # Get the corresponding `Page` object
            LOG.debug(f"{__name__}: Project - {project_.slug}, Page : {page_slug}")

            # Page query needs id for project and slug for page
            page = q.page(project_.id, page_slug)
            if not page:
                LOG.error(
                    f"{__name__}: Page not found for project - {project_.slug}, page : {page_slug}"
                )
                return render_template(url_for("proofing.project.replace", slug=slug))

            latest = page.revisions[-1]
            current_lines = latest.content.splitlines()
            # Iterate over the `lines` dictionary
            for line_num, replace_value in changed_lines.items():
                # Check if the line_num exists in the dictionary for this page
                LOG.debug(
                    f"{__name__}: Current - {current_lines[line_num]}, Length of lines = {len(current_lines)}"
                )
                if line_num < len(current_lines):
                    # Replace the line with the replacement value
                    current_lines[line_num] = replace_value
                else:
                    LOG.error(
                        f"{__name__}: Invalid line number {line_num} in {page_slug} which has only {len(current_lines)}"
                    )
                    continue
            # Join the lines into a single string
            new_content = "\n".join(current_lines)
            # Check if the page content has changed
            if new_content != latest.content:
                # Add a new revision to the page
                new_summary = f'Replaced "{query}" with "{replace}"'
                new_revision = add_revision(
                    page=page,
                    summary=new_summary,
                    content=new_content,
                    status=page.status.name,
                    version=page.version,
                    author_id=current_user.id,
                )
                LOG.debug(f"{__name__}: New reviion > {page_slug}: {new_revision}")

        flash(_l("Changes applied."), "success")
        return redirect(url_for("proofing.project.activity", slug=slug))
    elif form.cancel.data:
        LOG.debug(f"{__name__}: confirm_changes Cancelled")
        return redirect(url_for("proofing.project.edit", slug=slug))

    return render_template(url_for("proofing.project.edit", slug=slug))


@bp.route("/<slug>/batch-ocr", methods=["GET", "POST"])
@p2_required
def batch_ocr(slug):
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    # Check if there's an ongoing OCR task using Redis
    task_key = f"ocr_task:{slug}"
    task_info = redis_client.get(task_key)

    if task_info:
        try:
            task_data = json.loads(task_info)
            task_id = task_data.get("task_id")
            engine = task_data.get("engine", "google")
            language = task_data.get("language", "sa")

            # Try to restore the task to check if it's still active
            r = GroupResult.restore(task_id, app=celery_app)
            if r and r.results:
                current = r.completed_count()
                total = len(r.results)
                # Check if task is still in progress (not all tasks completed)
                if current < total:
                    percent = (current / total * 100) if total > 0 else 0

                    # Calculate task status variables
                    active_tasks = sum(
                        1 for result in r.results if result.state == "STARTED"
                    )
                    pending_tasks = sum(
                        1 for result in r.results if result.state == "PENDING"
                    )
                    failed_tasks = sum(1 for result in r.results if result.failed())

                    from kalanjiyam.utils.ocr_types import REVERSE_ENGINE_MAP

                    numeric_value = REVERSE_ENGINE_MAP.get(engine, "1")
                    engine_label = f"OCR {numeric_value}"

                    return render_template(
                        "proofing/projects/batch-ocr-post.html",
                        project=project_,
                        status="PROGRESS",
                        current=current,
                        total=total,
                        percent=percent,
                        task_id=task_id,
                        active_tasks=active_tasks,
                        pending_tasks=pending_tasks,
                        failed_tasks=failed_tasks,
                        engine=engine,
                        engine_label=engine_label,
                        language=language,
                    )
                else:
                    # Task is complete, remove from Redis
                    redis_client.delete(task_key)
            else:
                # Task not found or no results, remove from Redis
                redis_client.delete(task_key)
        except Exception as e:
            LOG.warning(f"Error checking OCR task for {slug}: {e}")
            # Task not found or error, remove from Redis
            redis_client.delete(task_key)

    from kalanjiyam.utils.ocr_client import get_available_engines
    from kalanjiyam.utils.ocr_types import (
        ENGINE_MAP,
        build_engine_choices,
        REVERSE_ENGINE_MAP,
    )

    system_settings = q.get_system_settings()
    default_ocr_engine = system_settings.default_ocr_engine or "google"
    default_engine_value = REVERSE_ENGINE_MAP.get(default_ocr_engine, "1")
    from kalanjiyam.utils.org_access import is_restricted_ocr_user

    is_restricted_ocr = is_restricted_ocr_user(current_user)

    if request.method == "POST":
        # Rate limit check for guest users
        if not current_user.is_authenticated:
            from kalanjiyam.utils.rate_limit import is_rate_limited

            ip_address = request.remote_addr
            fingerprint_id = request.cookies.get("device_fingerprint")
            limit = system_settings.unregistered_user_ocr_limit
            if is_rate_limited("run_ocr", ip_address, fingerprint_id, limit=limit):
                flash(
                    _l(
                        f"Rate limit exceeded. Guests can only run OCR {limit} times per 24 hours."
                    ),
                    "error",
                )
                return redirect(url_for("proofing.project.batch_ocr", slug=slug))

        engine_num = request.form.get("engine", "")
        language = request.form.get("language", "sa")
        if is_restricted_ocr:
            engine = default_ocr_engine
        else:
            engine = ENGINE_MAP.get(engine_num)

        if not engine or engine not in SUPPORTED_ENGINES:
            flash(_l("Unsupported OCR engine selected."), "error")
        else:
            queue_name = "low_priority" if not current_user.is_authenticated else None
            task = ocr_tasks.run_ocr_for_project(
                app_env=current_app.config["KALANJIYAM_ENVIRONMENT"],
                project=project_,
                engine=engine,
                language=language,
                queue=queue_name,
            )
            if task:
                # Log usage action for guests
                if not current_user.is_authenticated:
                    from kalanjiyam.utils.rate_limit import log_usage_action

                    log_usage_action(
                        action="run_ocr",
                        ip_address=request.remote_addr,
                        fingerprint_id=request.cookies.get("device_fingerprint"),
                        project_slug=slug,
                    )
                task_info = {
                    "task_id": task.id,
                    "engine": engine,
                    "language": language,
                    "started_at": datetime.utcnow().isoformat(),
                    "project_slug": slug,
                }
                redis_client.setex(task_key, 86400, json.dumps(task_info))

                from kalanjiyam.utils.user_tasks import (
                    add_user_task,
                    get_user_identifier,
                )

                user_id = get_user_identifier(current_user, request)
                if user_id:
                    add_user_task(
                        user_identifier=user_id,
                        task_id=task.id,
                        task_type="ocr",
                        project_slug=slug,
                        project_title=project_.display_title,
                        extra_info={"engine": engine, "language": language},
                    )
                from kalanjiyam.utils.ocr_types import REVERSE_ENGINE_MAP

                numeric_value = REVERSE_ENGINE_MAP.get(engine, "1")
                engine_label = f"OCR {numeric_value}"

                return render_template(
                    "proofing/projects/batch-ocr-post.html",
                    project=project_,
                    status="PENDING",
                    current=0,
                    total=0,
                    percent=0,
                    task_id=task.id,
                    active_tasks=0,
                    pending_tasks=0,
                    failed_tasks=0,
                    engine=engine,
                    language=language,
                    engine_label=engine_label,
                    is_restricted_ocr=is_restricted_ocr,
                    default_engine_value=default_engine_value,
                )
            else:
                flash(
                    _l("All pages in this project have at least one edit already."),
                    "error",
                )

    ocr_status = get_available_engines()
    engine_choices = build_engine_choices(
        ocr_status["engines"],
        is_super_admin=current_user.is_super_admin,
        recommended_engine=system_settings.recommended_ocr_engine,
    )
    return render_template(
        "proofing/projects/batch-ocr.html",
        project=project_,
        ocr_status=ocr_status["status"],
        engine_choices=engine_choices,
        is_restricted_ocr=is_restricted_ocr,
        default_engine_value=default_engine_value,
    )


def _clear_ocr_task_from_redis(task_id):
    """Clear OCR task from Redis when it completes or fails."""
    try:
        # Find the task key by scanning Redis keys
        for key in redis_client.scan_iter(match="ocr_task:*"):
            task_info = redis_client.get(key)
            if task_info:
                task_data = json.loads(task_info)
                if task_data.get("task_id") == task_id:
                    redis_client.delete(key)
                    LOG.debug(f"Cleared OCR task {task_id} from Redis key {key}")
                    break
    except Exception as e:
        LOG.warning(f"Error clearing OCR task from Redis: {e}")


@bp.route("/batch-ocr-status/<task_id>")
def batch_ocr_status(task_id):
    r = GroupResult.restore(task_id, app=celery_app)
    if not r or not r.results:
        return render_template(
            "include/ocr-progress.html",
            status="PENDING",
            current=0,
            total=0,
            percent=0,
            active_tasks=0,
            pending_tasks=0,
            failed_tasks=0,
            engine="google",
            language="sa",
        )

    # Get task info from Redis to include engine and language
    engine = "google"
    language = "sa"
    try:
        for key in redis_client.scan_iter(match="ocr_task:*"):
            task_info = redis_client.get(key)
            if task_info:
                task_data = json.loads(task_info)
                if task_data.get("task_id") == task_id:
                    engine = task_data.get("engine", "google")
                    language = task_data.get("language", "sa")
                    break
    except Exception as e:
        LOG.warning(f"Error getting OCR task info from Redis: {e}")

    from kalanjiyam.utils.ocr_types import REVERSE_ENGINE_MAP

    numeric_value = REVERSE_ENGINE_MAP.get(engine, "1")
    engine_label = f"OCR {numeric_value}"

    if r.results:
        current = r.completed_count()
        total = len(r.results)
        percent = (current / total * 100) if total > 0 else 0

        # Check if any tasks are actively being processed
        active_tasks = sum(1 for result in r.results if result.state == "STARTED")
        pending_tasks = sum(1 for result in r.results if result.state == "PENDING")
        failed_tasks = sum(1 for result in r.results if result.failed())
        revoked_tasks = sum(1 for result in r.results if result.state == "REVOKED")

        status = None
        if total:
            if current == total:
                status = "SUCCESS"
                # Clear the task from Redis when complete
                _clear_ocr_task_from_redis(task_id)
            elif failed_tasks > 0:
                status = "FAILURE"
                # Clear the task from Redis when failed
                _clear_ocr_task_from_redis(task_id)
            elif revoked_tasks > 0:
                status = "CANCELLED"
                _clear_ocr_task_from_redis(task_id)
            else:
                status = "PROGRESS"

        data = {
            "status": status,
            "current": current,
            "total": total,
            "percent": percent,
            "active_tasks": active_tasks,
            "pending_tasks": pending_tasks,
            "failed_tasks": failed_tasks,
            "engine": engine,
            "engine_label": engine_label,
            "language": language,
        }
    else:
        data = {
            "status": "PENDING",
            "current": 0,
            "total": 0,
            "percent": 0,
            "active_tasks": 0,
            "pending_tasks": 0,
            "failed_tasks": 0,
            "engine": engine,
            "engine_label": engine_label,
            "language": language,
        }

    return render_template(
        "include/ocr-progress.html",
        **data,
    )


@bp.route("/<slug>/batch-translate", methods=["GET", "POST"])
@p2_required
def batch_translate(slug):
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    engines = get_available_translation_engines()
    languages = get_supported_languages_list()

    # Check if there's an ongoing translation task using Redis
    task_key = f"translation_task:{slug}"
    try:
        task_info = redis_client.get(task_key)
    except Exception as e:
        LOG.warning(f"Error accessing Redis for task {slug}: {e}")
        task_info = None

    if task_info:
        try:
            task_data = json.loads(task_info)
            task_id = task_data.get("task_id")

            # Try to restore the task to check if it's still active
            r = GroupResult.restore(task_id, app=celery_app)
            if r and r.results:
                current = r.completed_count()
                total = len(r.results)
                # Check if task is still in progress (not all tasks completed)
                if current < total:
                    percent = (current / total * 100) if total > 0 else 0

                    # Calculate task status variables
                    active_tasks = sum(
                        1 for result in r.results if result.state == "STARTED"
                    )
                    pending_tasks = sum(
                        1 for result in r.results if result.state == "PENDING"
                    )
                    failed_tasks = sum(1 for result in r.results if result.failed())

                    return render_template(
                        "proofing/projects/batch-translate.html",
                        project=project_,
                        status="PROGRESS",
                        current=current,
                        total=total,
                        percent=percent,
                        task_id=task_id,
                        active_tasks=active_tasks,
                        pending_tasks=pending_tasks,
                        failed_tasks=failed_tasks,
                        engines=engines,
                        languages=languages,
                    )
                else:
                    # Task is complete, remove from Redis
                    redis_client.delete(task_key)
            else:
                # Task not found or no results, remove from Redis
                redis_client.delete(task_key)
        except Exception as e:
            LOG.warning(f"Error checking translation task for {slug}: {e}")
            # Task not found or error, remove from Redis
            redis_client.delete(task_key)

    if request.method == "POST":
        # Get translation parameters from form
        source_lang = request.form.get("source_lang", "sa")
        target_lang = request.form.get("target_lang", "en")
        engine = request.form.get("engine", "google")
        glossary = request.form.get("glossary") or None

        if source_lang == target_lang:
            flash(_l("Source and Target languages must be different."), "error")
            return render_template(
                "proofing/projects/batch-translate.html",
                project=project_,
                engines=engines,
                languages=languages,
            )

        # Validate engine
        from kalanjiyam.utils.translation_engine import TranslationEngineFactory

        if engine not in TranslationEngineFactory.get_supported_engines():
            flash(_l("Unsupported translation engine selected."), "error")
            return render_template(
                "proofing/projects/batch-translate.html",
                project=project_,
                engines=engines,
                languages=languages,
            )

        queue_name = "low_priority" if not current_user.is_authenticated else None
        task = translation_tasks.run_translation_for_project(
            app_env=current_app.config["KALANJIYAM_ENVIRONMENT"],
            project=project_,
            source_lang=source_lang,
            target_lang=target_lang,
            engine=engine,
            glossary=glossary,
            queue=queue_name,
        )
        if task:
            # Store task info in Redis with expiration (24 hours)
            task_info = {
                "task_id": task.id,
                "engine": engine,
                "source_lang": source_lang,
                "target_lang": target_lang,
                "started_at": datetime.utcnow().isoformat(),
                "project_slug": slug,
            }
            try:
                redis_client.setex(task_key, 86400, json.dumps(task_info))
            except Exception as redis_err:
                LOG.warning(f"Error setting Redis key for task {slug}: {redis_err}")

            from kalanjiyam.utils.user_tasks import add_user_task, get_user_identifier

            user_id = get_user_identifier(current_user, request)
            if user_id:
                add_user_task(
                    user_identifier=user_id,
                    task_id=task.id,
                    task_type="translation",
                    project_slug=slug,
                    project_title=project_.display_title,
                    extra_info={
                        "engine": engine,
                        "source_lang": source_lang,
                        "target_lang": target_lang,
                        "glossary": glossary,
                    },
                )

            return render_template(
                "proofing/projects/batch-translate.html",
                project=project_,
                status="PENDING",
                current=0,
                total=0,
                percent=0,
                task_id=task.id,
                active_tasks=0,
                pending_tasks=0,
                failed_tasks=0,
                engines=engines,
                languages=languages,
            )
        else:
            flash(_l("No pages with revisions found in this project."), "error")

    return render_template(
        "proofing/projects/batch-translate.html",
        project=project_,
        engines=engines,
        languages=languages,
    )


@bp.route("/batch-translate-status/<task_id>")
def batch_translate_status(task_id):
    r = GroupResult.restore(task_id, app=celery_app)
    if not r or not r.results:
        return render_template(
            "include/translation-progress.html",
            status="PENDING",
            current=0,
            total=0,
            percent=0,
            active_tasks=0,
            pending_tasks=0,
            failed_tasks=0,
        )

    if r.results:
        current = r.completed_count()
        total = len(r.results)
        percent = (current / total * 100) if total > 0 else 0

        # Check if any tasks are actively being processed
        active_tasks = sum(1 for result in r.results if result.state == "STARTED")
        pending_tasks = sum(1 for result in r.results if result.state == "PENDING")
        failed_tasks = sum(1 for result in r.results if result.failed())
        revoked_tasks = sum(1 for result in r.results if result.state == "REVOKED")

        status = None
        if total:
            if current == total:
                status = "SUCCESS"
                # Clear the task from Redis when complete
                from kalanjiyam.tasks.translation import (
                    _clear_translation_task_from_redis,
                )

                _clear_translation_task_from_redis(task_id)
            elif failed_tasks > 0:
                status = "FAILURE"
                # Clear the task from Redis when failed
                from kalanjiyam.tasks.translation import (
                    _clear_translation_task_from_redis,
                )

                _clear_translation_task_from_redis(task_id)
            elif revoked_tasks > 0:
                status = "CANCELLED"
                from kalanjiyam.tasks.translation import (
                    _clear_translation_task_from_redis,
                )

                _clear_translation_task_from_redis(task_id)
            else:
                status = "PROGRESS"

        data = {
            "status": status,
            "current": current,
            "total": total,
            "percent": percent,
            "active_tasks": active_tasks,
            "pending_tasks": pending_tasks,
            "failed_tasks": failed_tasks,
        }
    else:
        data = {
            "status": "PENDING",
            "current": 0,
            "total": 0,
            "percent": 0,
            "active_tasks": 0,
            "pending_tasks": 0,
            "failed_tasks": 0,
        }

    return render_template(
        "include/translation-progress.html",
        **data,
    )


@bp.route("/<slug>/admin", methods=["GET", "POST"])
@moderator_required
def admin(slug):
    """View admin controls for the project.

    We restrict these operations to admins because they are destructive in the
    wrong hands. Current list of admin operations:

    - delete project
    """
    project_ = q.project(slug)
    if project_ is None:
        abort(404)

    form = DeleteProjectForm()
    if form.validate_on_submit():
        if form.slug.data == slug:
            session = q.get_session()
            from kalanjiyam.models.batch import BatchItem

            session.query(BatchItem).filter_by(project_id=project_.id).update(
                {"project_id": None}
            )
            deleted_project_id = project_.id
            session.delete(project_)
            session.commit()

            # Drop the project's documents from the search index too.
            from kalanjiyam.tasks.search_index import enqueue_project_removal

            enqueue_project_removal(deleted_project_id)

            # Delete the project's files (PDF, page images, editor images)
            # so they don't count against the organization's storage quota.
            from kalanjiyam.utils.storage import get_storage, project_prefix

            get_storage().delete_prefix(project_prefix(slug))

            flash(_l("Deleted project %(slug)s", slug=slug), "success")
            return redirect(url_for("proofing.index"))
        else:
            form.slug.errors.append(_l("Deletion failed (incorrect field value)."))

    return render_template(
        "proofing/projects/admin.html",
        project=project_,
        form=form,
    )
