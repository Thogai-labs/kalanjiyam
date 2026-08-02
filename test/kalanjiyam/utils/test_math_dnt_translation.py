import pytest
from unittest.mock import patch
from kalanjiyam.utils.translation_engine import (
    protect_dnt_and_math,
    restore_dnt_and_math,
    translate_text,
    TranslationResponse,
)

def test_protect_and_restore_explicit_dnt():
    text = "Translate this text. <dnt>Do not translate this block</dnt> Keep this."
    protected, dnt_map = protect_dnt_and_math(text)
    
    assert "DNTBLOCK0DNT" in protected
    assert "<dnt>Do not translate this block</dnt>" not in protected
    assert dnt_map["DNTBLOCK0DNT"] == "<dnt>Do not translate this block</dnt>"
    
    restored = restore_dnt_and_math(protected, dnt_map)
    assert restored == text


def test_protect_and_restore_latex_math():
    text = r"The equation is $$ E = mc^2 $$ and \( a^2 + b^2 = c^2 \) where x is 5."
    protected, dnt_map = protect_dnt_and_math(text)
    
    assert "DNTBLOCK0DNT" in protected
    assert "DNTBLOCK1DNT" in protected
    assert dnt_map["DNTBLOCK0DNT"] == r"<dnt>$$ E = mc^2 $$</dnt>"
    assert dnt_map["DNTBLOCK1DNT"] == r"<dnt>\( a^2 + b^2 = c^2 \)</dnt>"
    
    restored = restore_dnt_and_math(protected, dnt_map)
    assert r"<dnt>$$ E = mc^2 $$</dnt>" in restored
    assert r"<dnt>\( a^2 + b^2 = c^2 \)</dnt>" in restored


def test_protect_html_math_elements():
    text = r'<p>Formula: <span class="math inline">\(x + y = z\)</span> and <math><mi>x</mi></math></p>'
    protected, dnt_map = protect_dnt_and_math(text)
    
    assert len(dnt_map) >= 1
    restored = restore_dnt_and_math(protected, dnt_map)
    assert r'<span class="math inline">\(x + y = z\)</span>' in restored or '<dnt>' in restored


@patch("kalanjiyam.utils.translation_engine.TranslationEngineFactory.create")
def test_translate_text_preserves_dnt_and_math(mock_factory):
    mock_engine = mock_factory.return_value
    
    def fake_translate(text, source_lang, target_lang, **kwargs):
        # Simulate engine translating text while keeping DNTBLOCK0DNT intact
        return TranslationResponse(
            translated_text=text.replace("Formula", "सूत्र").replace("where", "जहाँ"),
            source_language=source_lang,
            target_language=target_lang,
            engine="google"
        )
    
    mock_engine.translate.side_effect = fake_translate
    
    input_text = r"Formula <dnt>$$ E = mc^2 $$</dnt> where energy is E."
    resp = translate_text(input_text, "en", "hi", "google")
    
    assert r"<dnt>$$ E = mc^2 $$</dnt>" in resp.translated_text
    assert "सूत्र" in resp.translated_text


def test_image_url_not_corrupted_by_math_protection():
    """Verify that HTML image URLs like /images/extracted_1a6ecb26.png are not modified or wrapped in $."""
    text = '<img src="/kalanjiyam/static/uploads/becc-116-slm-eng-version-1/images/extracted_1a6ecb26.png" alt="extracted" />'
    protected, dnt_map = protect_dnt_and_math(text)
    
    restored = restore_dnt_and_math(protected, dnt_map)
    assert restored == text
    assert "$extracted_" not in restored
    assert ".png$" not in restored
    assert '/kalanjiyam/static/uploads/becc-116-slm-eng-version-1/images/extracted_1a6ecb26.png' in restored


def test_restore_dnt_sanitizes_corrupted_dollar_image_urls():
    """Verify that restore_dnt_and_math automatically cleans corrupted image URLs with $ in filenames."""
    corrupted_text = '/kalanjiyam/static/uploads/becc-116-slm-eng-version-1/images/$extracted_cc29355e.png$'
    sanitized = restore_dnt_and_math(corrupted_text, {})
    assert sanitized == '/kalanjiyam/static/uploads/becc-116-slm-eng-version-1/images/extracted_cc29355e.png'


def test_auto_wrap_math_english_ocr_document_with_images():
    """Verify that English OCR document segments containing image filenames or URLs are never wrapped in $."""
    from kalanjiyam.utils.proofing_utils import auto_wrap_math
    
    english_ocr_segment = 'extracted_cc29355e.png'
    english_ocr_url = '/kalanjiyam/static/uploads/becc-116-slm-eng-version-1/images/extracted_cc29355e.png'
    
    assert auto_wrap_math(english_ocr_segment) == 'extracted_cc29355e.png'
    assert auto_wrap_math(english_ocr_url) == '/kalanjiyam/static/uploads/becc-116-slm-eng-version-1/images/extracted_cc29355e.png'
