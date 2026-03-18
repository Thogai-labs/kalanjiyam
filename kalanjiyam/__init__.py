"""Main entrypoint for the Kalanjiyam application.

For a high-level overview of the application and how to operate it, see:

https://kalanjiyam.readthedocs.io/en/latest/
"""

import logging
import sys

import sentry_sdk
from dotenv import load_dotenv
from flask import Flask, session
from flask_babel import Babel, pgettext
from sentry_sdk.integrations.flask import FlaskIntegration
from sqlalchemy import exc

import config
from kalanjiyam import admin as admin_manager
from kalanjiyam import auth as auth_manager
from kalanjiyam import checks, filters, queries
from kalanjiyam.consts import LOCALES
from kalanjiyam.mail import mailer
from kalanjiyam.utils import assets
from kalanjiyam.utils.json_serde import KalanjiyamJSONEncoder
from kalanjiyam.utils.url_converters import ListConverter
from kalanjiyam.views.about import bp as about
from kalanjiyam.views.api import bp as api
from kalanjiyam.views.auth import bp as auth
from kalanjiyam.views.blog import bp as blog
from kalanjiyam.views.dictionaries import bp as dictionaries
from kalanjiyam.views.proofing import bp as proofing
from kalanjiyam.views.public import bp as public
from kalanjiyam.views.reader.parses import bp as parses
from kalanjiyam.views.site import bp as site


def _initialize_sentry(sentry_dsn: str):
    """Initialize basic monitoring through the third-party Sentry service."""
    sentry_sdk.init(
        dsn=sentry_dsn, integrations=[FlaskIntegration()], traces_sample_rate=0
    )


def _initialize_db_session(app, config_name: str):
    """Ensure that our SQLAlchemy session behaves well.

    The Flask-SQLAlchemy library manages all of this boilerplate for us
    automatically, but Flask-SQLAlchemy has relatively poor support for using
    our models outside of the application context, e.g. when running seed
    scripts or other batch jobs. So instead of using that extension, we manage
    the boilerplate ourselves.
    """

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        """Reset session state to prevent caching and memory leaks."""
        queries.get_session_class().remove()

    if config_name == config.PRODUCTION:
        # The hook below hides database errors. So, install the hook only if
        # we're in production.

        @app.errorhandler(exc.SQLAlchemyError)
        def handle_db_exceptions(error):
            """Rollback errors so that the db can handle future requests."""
            session = queries.get_session()
            session.rollback()


def _initialize_logger(log_level: int) -> None:
    """Initialize a simple logger for all requests."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
    )
    logging.getLogger().setLevel(log_level)
    logging.getLogger().addHandler(handler)


def create_app(config_env: str):
    """Initialize the Kalanjiyam application."""

    # We store all env variables in a `.env` file so that it's easier to manage
    # different configurations.
    load_dotenv(".env")
    config_spec = config.load_config_object(config_env)

    # Initialize Sentry monitoring only in production so that our Sentry page
    # contains only production warnings (as opposed to dev warnings).
    #
    # "Configuration should happen as early as possible in your application's
    # lifecycle." -- Sentry docs
    if config_env == config.PRODUCTION:
        _initialize_sentry(config_spec.SENTRY_DSN)

    app = Flask(__name__)

    # Config
    app.config.from_object(config_spec)

    # Sanity checks
    assert config_env == config_spec.KALANJIYAM_ENVIRONMENT
    if config_env != config.TESTING:
        with app.app_context():
            checks.check_database_uri(config_spec.SQLALCHEMY_DATABASE_URI)

    # Logger
    _initialize_logger(config_spec.LOG_LEVEL)

    # Database
    _initialize_db_session(app, config_env)

    # A custom Babel locale_selector.
    def get_locale():
        return session.get("locale", config_spec.BABEL_DEFAULT_LOCALE)

    # Extensions
    Babel(app, locale_selector=get_locale)

    login_manager = auth_manager.create_login_manager()
    login_manager.init_app(app)

    mailer.init_app(app)

    with app.app_context():
        _ = admin_manager.create_admin_manager(app)

    # Route extensions
    app.url_map.converters["list"] = ListConverter

    # Blueprints
    url_prefix = config_spec.APPLICATION_URL_PREFIX
    app.register_blueprint(about, url_prefix=f"{url_prefix}/about")
    app.register_blueprint(api, url_prefix=f"{url_prefix}/api")
    app.register_blueprint(auth, url_prefix=url_prefix)
    app.register_blueprint(blog, url_prefix=f"{url_prefix}/blog")
    app.register_blueprint(dictionaries, url_prefix=f"{url_prefix}/tools/dictionaries")
    app.register_blueprint(parses, url_prefix=f"{url_prefix}/parses")
    app.register_blueprint(proofing, url_prefix=f"{url_prefix}/proofing")
    app.register_blueprint(public, url_prefix=f"{url_prefix}/books")
    app.register_blueprint(site, url_prefix=url_prefix)
    
    # Admin functionality is now integrated into the main Flask-Admin interface

    # Debug-only routes for local development.
    if app.debug or config.TESTING:
        from kalanjiyam.views.debug import bp as debug_bp

        app.register_blueprint(debug_bp, url_prefix="/debug")

    # i18n string trimming
    app.jinja_env.policies["ext.i18n.trimmed"] = True
    # Template functions and filters
    app.jinja_env.filters.update(
        {
            "d": filters.devanagari,
            "slp2dev": filters.slp_to_devanagari,
            "devanagari": filters.devanagari,
            "roman": filters.roman,
            "markdown": filters.markdown,
            "time_ago": filters.time_ago,
        }
    )
    app.jinja_env.globals.update(
        {
            "asset": assets.hashed_static,
            "pgettext": pgettext,
            "kalanjiyam_locales": LOCALES,
            "get_locale": get_locale,
        }
    )

    app.json_encoder = KalanjiyamJSONEncoder
    return app
