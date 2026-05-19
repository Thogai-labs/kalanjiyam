"""Public views for viewing OCR'd and translated books."""

from flask import Blueprint

bp = Blueprint("public", __name__)

from . import books  # noqa

# Register the books blueprint as a sub-blueprint
bp.register_blueprint(books.bp)
