"""WSGI entry point for Gunicorn and other production servers."""

import os

from kalanjiyam import create_app

# FLASK_ENV must match a config name: development, staging, production, testing, build
_config = os.getenv("FLASK_ENV", "production")
app = create_app(_config)
