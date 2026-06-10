"""Views for basic site pages."""

from flask import Blueprint, abort, redirect, render_template, session, url_for

from kalanjiyam import queries as q
from kalanjiyam.consts import LOCALES
from kalanjiyam.utils.storage import editor_image_key, get_storage, page_image_key
from flask_login import current_user

bp = Blueprint("site", __name__)


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/contact")
def contact():
    return redirect(url_for("about.contact"))


@bp.route("/donate")
def donate():
    return render_template("site/donate.html")


@bp.route("/donate/<title>/<cost>")
def donate_for_project(title, cost):
    return render_template("site/donate-for-project.html", title=title, cost=cost)


@bp.route("/sponsor")
def sponsor():
    sponsorships = q.project_sponsorships()
    return render_template("site/sponsor.html", sponsorships=sponsorships)


@bp.route("/support")
def support():
    return render_template("site/support.html")


@bp.route("/test-sentry-500")
def sentry_500():
    """Sentry integration test. Should trigger a 500 error in prod."""
    _ = 1 / 0


@bp.route("/static/uploads/<project_slug>/pages/<page_slug>.jpg")
def page_image(project_slug, page_slug):
    """Serve a page image from the storage backend."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)
    if not q.user_can_view_project(current_user, project_):
        abort(403)

    storage = get_storage()
    key = page_image_key(project_slug, page_slug)
    if not storage.exists(key):
        abort(404)

    return storage.serve(key)


@bp.route("/static/uploads/<project_slug>/images/<filename>")
def editor_image(project_slug, filename):
    """Serve an image uploaded to the rich text editor."""
    project_ = q.project(project_slug)
    if project_ is None:
        abort(404)
    if not q.user_can_view_project(current_user, project_):
        abort(403)

    storage = get_storage()
    key = editor_image_key(project_slug, filename)
    if not storage.exists(key):
        abort(404)

    return storage.serve(key)


@bp.app_errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


@bp.app_errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@bp.app_errorhandler(413)
def request_too_large(e):
    return render_template("413.html"), 413


@bp.app_errorhandler(500)
def internal_server_error(e):
    return render_template("500.html"), 500


@bp.route("/language/<slug>")
def set_language(slug=None):
    locale = [L for L in LOCALES if slug == L.slug]
    if locale:
        locale = locale[0]
        session["locale"] = locale.code
    return redirect(url_for("site.index"))
