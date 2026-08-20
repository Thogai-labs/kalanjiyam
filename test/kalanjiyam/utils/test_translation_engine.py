"""Tests for translation engine functionality."""

import pytest
from unittest.mock import Mock, patch

from kalanjiyam.utils.translation_engine import (
    TranslationResponse,
    GoogleTranslateEngine,
    OpenAITranslateEngine,
    IndicTransEngine,
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
        assert "indictrans2" in engines
        assert "gemma" in engines
        assert "google" not in engines
        assert "openai" not in engines
    
    def test_create_indictrans_engine(self):
        """Test creating IndicTrans engine."""
        engine = TranslationEngineFactory.create("indictrans2")
        assert isinstance(engine, IndicTransEngine)
        assert engine.version == "indictrans2"

    def test_create_gemma_engine(self):
        """Test creating Gemma engine."""
        engine = TranslationEngineFactory.create("gemma")
        assert isinstance(engine, IndicTransEngine)
        assert engine.version == "gemma"
    
    def test_create_unsupported_engine(self):
        """Test creating unsupported engine raises error."""
        with pytest.raises(ValueError, match="Unsupported translation engine"):
            TranslationEngineFactory.create("unsupported")


class TestIndicTransEngine:
    """Test IndicTrans engine."""
    
    @patch('httpx.Client')
    def test_translate_simple_text(self, mock_client_class):
        """Test simple text translation."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        # Mock the translation response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "Hello world"}
        mock_client.post.return_value = mock_response
        
        # Create app context because current_app is used
        from flask import Flask
        app = Flask("test_app")
        app.config["TRANSLATION_SERVICE_URL"] = "http://localhost:8888"
        app.config["TRANSLATION_SERVICE_TIMEOUT"] = 10
        
        with app.app_context():
            engine = IndicTransEngine("indictrans2")
            response = engine.translate("नमस्ते दुनिया", "sa", "en")
            
            assert response.translated_text == "Hello world"
            assert response.source_language == "sa"
            assert response.target_language == "en"
            assert response.engine == "indictrans2"
            
            # Check mock call
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["json"]["text"] == "नमस्ते दुनिया"
            assert call_kwargs["json"]["model_name"] == "ai4bharat/indictrans2-indic-en-1B"
            assert call_kwargs["json"]["source_language"] == "Sanskrit"
            assert call_kwargs["json"]["target_language"] == "English"

    @patch('httpx.Client')
    def test_translate_gemma_text(self, mock_client_class):
        """Test Gemma text translation uses google/gemma-4-12b-it."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "வணக்கம் உலகம்"}
        mock_client.post.return_value = mock_response

        from flask import Flask
        app = Flask("test_app")
        app.config["TRANSLATION_SERVICE_URL"] = "http://localhost:8888"

        with app.app_context():
            engine = IndicTransEngine("gemma")
            response = engine.translate("Hello world", "en", "ta")

            assert response.translated_text == "வணக்கம் உலகம்"
            assert response.engine == "gemma"

            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["json"]["model_name"] == "google/gemma-4-12b-it"
            assert call_kwargs["json"]["source_language"] == "English"
            assert call_kwargs["json"]["target_language"] == "Tamil"

    @patch('httpx.Client')
    def test_translate_with_api_key(self, mock_client_class):
        """Test text translation sends X-API-Key header when configured."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "Hello world"}
        mock_client.post.return_value = mock_response
        
        from flask import Flask
        app = Flask("test_app")
        app.config["TRANSLATION_SERVICE_URL"] = "http://localhost:8888"
        app.config["TRANSLATION_SERVICE_API_KEY"] = "test-secret-key"
        
        with app.app_context():
            engine = IndicTransEngine("indictrans2")
            response = engine.translate("नमस्ते दुनिया", "sa", "en")
            
            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["headers"] == {"X-API-Key": "test-secret-key"}

    @patch('httpx.Client')
    def test_translate_with_glossary(self, mock_client_class):
        """Test translation passes glossary payload."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "Translated"}
        mock_client.post.return_value = mock_response

        from flask import Flask
        app = Flask("test_app")
        app.config["TRANSLATION_SERVICE_URL"] = "http://localhost:8888"

        with app.app_context():
            engine = IndicTransEngine("indictrans2")
            response = engine.translate("Hello", "en", "hi", glossary="administrative")
            assert response.translated_text == "Translated"

            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["json"]["glossary"] == "administrative"

    def test_get_supported_languages(self):
        """Test getting supported languages."""
        engine = IndicTransEngine("indictrans2")
        languages = engine.get_supported_languages()
        assert "en" in languages
        assert "sa" in languages
        assert "hi" in languages


class TestAvailableTranslationEngines:
    """Test dynamic fetching of translation engines."""

    @patch('httpx.Client')
    def test_get_available_translation_engines(self, mock_client_class):
        """Test fetching available engines includes X-API-Key header and parses Gemma and IndicTrans."""
        from kalanjiyam.utils.translation_engine import get_available_translation_engines
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"model_name": "ai4bharat/indictrans2-en-indic-1B"},
            {"model_name": "google/gemma-4-12b-it"}
        ]
        mock_client.get.return_value = mock_response

        from flask import Flask
        app = Flask("test_app")
        app.config["TRANSLATION_SERVICE_URL"] = "http://localhost:8888"
        app.config["TRANSLATION_SERVICE_API_KEY"] = "test-api-key"

        with app.app_context():
            engines = get_available_translation_engines()
            assert len(engines) == 2
            assert engines[0]["value"] == "indictrans2"
            assert engines[0]["label"] == "IndicTrans v2"
            assert engines[1]["value"] == "gemma"
            assert engines[1]["label"] == "Gemma 4 12B"

            mock_client.get.assert_called_once()
            call_kwargs = mock_client.get.call_args[1]
            assert call_kwargs["headers"] == {"X-API-Key": "test-api-key"}


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
            engine="indictrans2"
        )
        mock_engine.translate.return_value = mock_response
        mock_factory.create.return_value = mock_engine
        
        response = translate_text("नमस्ते दुनिया", "sa", "en", "indictrans2")
        
        assert response.translated_text == "Hello world"
        mock_factory.create.assert_called_once_with("indictrans2")
        mock_engine.translate.assert_called_once_with("नमस्ते दुनिया", "sa", "en")