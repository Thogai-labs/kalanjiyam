"""Tests for translation engine functionality."""

import pytest
from unittest.mock import Mock, patch

from kalanjiyam.utils.translation_engine import (
    TranslationResponse,
    GoogleTranslateEngine,
    OpenAITranslateEngine,
    TranslationEngineFactory,
    translate_text,
    segment_text_for_translation,
)


class TestTranslationResponse:
    """Test the TranslationResponse dataclass."""
    
    def test_translation_response_creation(self):
        """Test creating a TranslationResponse instance."""
        response = TranslationResponse(
            translated_text="Hello world",
            source_language="sa",
            target_language="en",
            engine="google",
            metadata={"confidence": 0.95}
        )
        
        assert response.translated_text == "Hello world"
        assert response.source_language == "sa"
        assert response.target_language == "en"
        assert response.engine == "google"
        assert response.metadata["confidence"] == 0.95


class TestTextSegmentation:
    """Test text segmentation functionality."""
    
    def test_segment_text_short(self):
        """Test segmentation of short text."""
        text = "Short text"
        segments = segment_text_for_translation(text, max_length=100)
        assert segments == [text]
    
    def test_segment_text_long(self):
        """Test segmentation of long text."""
        text = "This is a very long text that should be split into multiple segments for translation purposes. " * 10
        segments = segment_text_for_translation(text, max_length=100)
        assert len(segments) > 1
        assert all(len(segment) <= 100 for segment in segments)
    
    def test_segment_text_with_paragraphs(self):
        """Test segmentation with paragraph breaks."""
        text = "Paragraph 1 with some content.\n\nParagraph 2 with more content.\n\nParagraph 3 with even more content."
        segments = segment_text_for_translation(text, max_length=30)
        assert len(segments) >= 3


class TestTranslationEngineFactory:
    """Test the TranslationEngineFactory."""
    
    def test_get_supported_engines(self):
        """Test getting supported engines."""
        engines = TranslationEngineFactory.get_supported_engines()
        assert "google" in engines
        assert "openai" in engines
    
    def test_create_google_engine(self):
        """Test creating Google Translate engine."""
        with patch('kalanjiyam.utils.translation_engine.GoogleTranslateEngine') as mock_engine_class:
            mock_engine = Mock()
            mock_engine_class.return_value = mock_engine
            engine = TranslationEngineFactory.create("google")
            mock_engine_class.assert_called_once()
    
    def test_create_openai_engine(self):
        """Test creating OpenAI engine."""
        with patch('kalanjiyam.utils.translation_engine.OpenAITranslateEngine') as mock_engine_class:
            mock_engine = Mock()
            mock_engine_class.return_value = mock_engine
            engine = TranslationEngineFactory.create("openai", api_key="test-key")
            mock_engine_class.assert_called_once_with(api_key="test-key")
    
    def test_create_unsupported_engine(self):
        """Test creating unsupported engine raises error."""
        with pytest.raises(ValueError, match="Unsupported translation engine"):
            TranslationEngineFactory.create("unsupported")


class TestGoogleTranslateEngine:
    """Test Google Translate engine."""
    
    @patch('kalanjiyam.utils.translation_engine.Translator')
    def test_translate_simple_text(self, mock_translator_class):
        """Test simple text translation."""
        mock_translator = Mock()
        mock_translator_class.return_value = mock_translator
        
        # Mock the translation result
        mock_result = Mock()
        mock_result.text = "Hello world"
        mock_translator.translate.return_value = mock_result
        
        with patch('kalanjiyam.utils.translation_engine.GoogleTranslateEngine.__init__', return_value=None):
            engine = GoogleTranslateEngine()
            engine.translator = mock_translator
            engine._supported_languages = None
            
            response = engine.translate("नमस्ते दुनिया", "sa", "en")
            
            assert response.translated_text == "Hello world"
            assert response.source_language == "sa"
            assert response.target_language == "en"
            assert response.engine == "google"
    
    def test_get_supported_languages(self):
        """Test getting supported languages."""
        with patch('kalanjiyam.utils.translation_engine.GoogleTranslateEngine.__init__', return_value=None):
            engine = GoogleTranslateEngine()
            engine._supported_languages = ['en', 'sa', 'hi']
            languages = engine.get_supported_languages()
            assert "en" in languages
            assert "sa" in languages


class TestOpenAITranslateEngine:
    """Test OpenAI translation engine."""
    
    @patch('kalanjiyam.utils.translation_engine.openai')
    def test_translate_simple_text(self, mock_openai):
        """Test simple text translation with OpenAI."""
        # Mock the OpenAI client and response
        mock_client = Mock()
        mock_openai.OpenAI.return_value = mock_client
        
        mock_response = Mock()
        mock_response.choices = [Mock()]
        mock_response.choices[0].message.content = "Hello world"
        mock_response.usage = {"total_tokens": 100}
        mock_client.chat.completions.create.return_value = mock_response
        
        with patch('kalanjiyam.utils.translation_engine.OpenAITranslateEngine.__init__', return_value=None):
            engine = OpenAITranslateEngine()
            engine.client = mock_client
            
            response = engine.translate("नमस्ते दुनिया", "sa", "en")
            
            assert response.translated_text == "Hello world"
            assert response.source_language == "sa"
            assert response.target_language == "en"
            assert response.engine == "openai"
            assert response.metadata["model"] == "gpt-3.5-turbo"
    
    def test_get_supported_languages(self):
        """Test getting supported languages."""
        with patch('kalanjiyam.utils.translation_engine.OpenAITranslateEngine.__init__', return_value=None):
            engine = OpenAITranslateEngine()
            languages = engine.get_supported_languages()
            assert "en" in languages
            assert "sa" in languages
            assert "hi" in languages


class TestTranslateTextFunction:
    """Test the convenience translate_text function."""
    
    @patch('kalanjiyam.utils.translation_engine.TranslationEngineFactory')
    def test_translate_text(self, mock_factory):
        """Test the translate_text convenience function."""
        mock_engine = Mock()
        mock_response = TranslationResponse(
            translated_text="Hello world",
            source_language="sa",
            target_language="en",
            engine="google"
        )
        mock_engine.translate.return_value = mock_response
        mock_factory.create.return_value = mock_engine
        
        response = translate_text("नमस्ते दुनिया", "sa", "en", "google")
        
        assert response.translated_text == "Hello world"
        mock_factory.create.assert_called_once_with("google")
        mock_engine.translate.assert_called_once_with("नमस्ते दुनिया", "sa", "en") 