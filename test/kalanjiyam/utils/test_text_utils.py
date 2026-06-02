"""Tests for Unicode text normalization."""

from kalanjiyam.utils.text_utils import normalize_unicode_text


def test_normalize_unicode_text_nfc():
    # Tamil: composed form
    assert normalize_unicode_text("க") == "க"


def test_normalize_unicode_text_escapes():
    # Literal \\u escape sequence in string
    raw = "\\u0b95\\u0bbe\\u0bb0\\u0bcd"
    assert normalize_unicode_text(raw) == "கார்"


def test_normalize_unicode_text_empty():
    assert normalize_unicode_text(None) == ""
    assert normalize_unicode_text("") == ""
