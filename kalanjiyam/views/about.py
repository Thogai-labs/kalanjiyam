"""General information about Kalanjiyam."""

from flask import Blueprint, redirect, render_template, url_for
from flask_babel import lazy_gettext as _l
from flask_wtf import FlaskForm
from wtforms import EmailField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length

from kalanjiyam import queries as q

bp = Blueprint("about", __name__)


class ContactForm(FlaskForm):
    """Form for contact page."""
    email = EmailField(_l("Email address"), validators=[DataRequired(), Email()])
    message = StringField(_l("Message"), validators=[DataRequired(), Length(min=1, max=10000)])

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


@bp.route("/contact")
def contact():
    form = ContactForm()
    return render_template("about/contact.html", form=form)


@bp.route("/terms")
def terms():
    return render_template("about/terms.html")


@bp.route("/privacy-policy")
def privacy():
    return render_template("about/privacy.html")
