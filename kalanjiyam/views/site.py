"""Views for basic site pages."""

from flask import Blueprint, abort, current_app, redirect, render_template, send_file, session, url_for
from pathlib import Path

from kalanjiyam import queries as q
from kalanjiyam.consts import LOCALES
from kalanjiyam.utils.assets import get_page_image_filepath

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
    """Serve an image from the filesystem.
    
    In production, this is typically handled by nginx, but we allow
    Flask to serve it if the file exists.
    """
    image_path = get_page_image_filepath(project_slug, page_slug)
    
    # Check if the image file exists
    if not image_path.exists():
        abort(404)
    
    return send_file(image_path)


@bp.route("/static/uploads/<project_slug>/images/<filename>")
def editor_image(project_slug, filename):
    """Serve an image uploaded to the rich text editor.
    
    In production, this is typically handled by nginx, but we allow
    Flask to serve it if the file exists.
    """
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    image_path = upload_folder / "projects" / project_slug / "images" / filename
    
    # Check if the image file exists
    if not image_path.exists():
        abort(404)
    
    return send_file(image_path)


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
