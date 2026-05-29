"""Unicode normalization for OCR and editor text."""

from __future__ import annotations

import codecs
import unicodedata


def normalize_unicode_text(text: str | None) -> str:
    """Normalize OCR/editor text for consistent Indic script display."""
    if not text:
        return ""
    value = str(text)
    if "\\u" in value or "\\U" in value:
        try:
            value = codecs.decode(value, "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            pass
    return unicodedata.normalize("NFC", value)
