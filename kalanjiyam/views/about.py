"""General information about Kalanjiyam."""

from flask import Blueprint, redirect, render_template, url_for

from kalanjiyam import queries as q

bp = Blueprint("about", __name__)

people = Blueprint("people", __name__)
bp.register_blueprint(people, url_prefix="/people")


@bp.route("/")
def index():
    return render_template("about/index.html")


@bp.route("/mission")
def mission():
    return render_template("about/mission.html")


@bp.route("/values")
def values():
    return render_template("about/values.html")


@people.route("/", endpoint="index")
def people_index():
    return redirect(url_for("about.people.core"))


@people.route("/core")
def core():
    return render_template("about/people/core.html")


@people.route("/proofing")
def proofing():
    contributors = q.contributor_info()
    return render_template("about/people/proofing.html", contributors=contributors)


@bp.route("/code-and-data")
def code_and_data():
    return render_template("about/code-and-data.html")


@bp.route("/our-name")
def name():
    return render_template("about/our-name.html")


ISSUE_CATEGORIES = [
    "pdf_proofing_editor",
    "docx_proofing_editor",
    "ocr",
    "translation",
    "dictionary_glossaries",
    "authentication",
    "other",
]


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    from flask import flash, request
    from flask_login import current_user
    from flask_babel import gettext as _
    import kalanjiyam.database as db

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        category = request.form.get("category", "").strip()
        message = request.form.get("message", "").strip()

        if not name or not email or not category or not message:
            flash(_("Please fill in all required fields."), "error")
            return render_template("about/contact.html")

        if category not in ISSUE_CATEGORIES:
            flash(_("Please select a valid issue category."), "error")
            return render_template("about/contact.html")

        session = q.get_session()
        issue = db.ReportedIssue(
            name=name,
            email=email,
            category=category,
            message=message,
            user_id=current_user.id if current_user.is_authenticated else None,
        )
        session.add(issue)
        session.commit()

        flash(_("Thank you! Your issue report has been submitted successfully."), "success")
        return redirect(url_for("about.contact"))

    return render_template("about/contact.html")


@bp.route("/terms")
def terms():
    return render_template("about/terms.html")


@bp.route("/privacy-policy")
def privacy():
    return render_template("about/privacy.html")
