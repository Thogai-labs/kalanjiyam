"""Tests for translation engine functionality."""

import pytest
from unittest.mock import Mock, patch

from kalanjiyam.utils.translation_engine import (
    TranslationResponse,
    GoogleTranslateEngine,
    OpenAITranslateEngine,
    IndicTransEngine,
    BharatGenTranslateEngine,
    clean_translation_preambles,
    TranslationEngineFactory,
    translate_text,
    segment_text_for_translation,
    get_available_translation_engines,
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
        assert "param_lc_translate_ep4" in engines
        assert "translation_1b_exp_40" in engines
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

    def test_create_param_lc_translate_engine(self):
        """Test creating param_lc_translate_ep4 engine."""
        engine = TranslationEngineFactory.create("param_lc_translate_ep4")
        assert isinstance(engine, BharatGenTranslateEngine)
        assert engine.model_name == "param_lc_translate_ep4"

    def test_create_translation_1b_exp_engine(self):
        """Test creating translation_1b_exp_40 engine."""
        engine = TranslationEngineFactory.create("translation_1b_exp_40")
        assert isinstance(engine, BharatGenTranslateEngine)
        assert engine.model_name == "translation_1b_exp_40"
    
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
    def test_translate_gemma_4_31b_text(self, mock_client_class):
        """Test Gemma-4-31B text translation uses google/gemma-4-31b-it."""
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
            engine = IndicTransEngine("gemma_4_31b")
            response = engine.translate("Hello world", "en", "ta")

            assert response.translated_text == "வணக்கம் உலகம்"
            assert response.engine == "gemma_4_31b"

            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["json"]["model_name"] == "google/gemma-4-31b-it"
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


class TestBharatGenTranslateEngine:
    """Test BharatGen chat completions translation engine."""

    @patch('httpx.Client')
    def test_translate_param_lc_translate(self, mock_client_class):
        """Test translation using param_lc_translate_ep4 model."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "भारत एक महान देश है।"
                    }
                }
            ],
            "usage": {"total_tokens": 20}
        }
        mock_client.post.return_value = mock_response

        from flask import Flask
        app = Flask("test_app")
        app.config["BHARATGEN_TRANSLATION_API_URL"] = "https://api.bharatgen.dev/v1/chat/completions"
        app.config["BHARATGEN_TRANSLATION_API_KEY"] = "test-token"

        with app.app_context():
            engine = BharatGenTranslateEngine("param_lc_translate_ep4")
            response = engine.translate("India is a great country", "en", "hi")

            assert response.translated_text == "भारत एक महान देश है।"
            assert response.source_language == "en"
            assert response.target_language == "hi"
            assert response.engine == "param_lc_translate_ep4"

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer test-token"
            assert call_kwargs["json"]["model"] == "param_lc_translate_ep4"
            assert call_kwargs["json"]["temperature"] == 0.1
            assert call_kwargs["json"]["repetition_penalty"] == 1.02
            assert call_kwargs["json"]["max_length"] == 2048
            assert call_kwargs["json"]["chat_template_kwargs"] == {"enable_thinking": True}
            assert "You are a professional machine translation system." in call_kwargs["json"]["messages"][0]["content"]
            assert "Translate the following text from English to Hindi:" in call_kwargs["json"]["messages"][1]["content"]

    @patch('httpx.Client')
    def test_translate_with_thinking_tags_and_preambles_stripped(self, mock_client_class):
        """Test thinking tags and conversational preambles are cleanly stripped."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "<think>Thinking...</think>Here is the Marathi translation:\nदोनशे युरोपियन पायदळाचे एक विभाग"
                    }
                }
            ]
        }
        mock_client.post.return_value = mock_response

        from flask import Flask
        app = Flask("test_app")
        with app.app_context():
            engine = BharatGenTranslateEngine("translation_1b_exp_40")
            response = engine.translate("A division of two hundred European infantry", "en", "mr")
            assert response.translated_text == "दोनशे युरोपियन पायदळाचे एक विभाग"
            assert response.engine == "translation_1b_exp_40"

    @patch('httpx.Client')
    def test_translate_with_glossary(self, mock_client_class):
        """Test BharatGen translation with glossary parameter."""
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "अनुवाद"
                    }
                }
            ]
        }
        mock_client.post.return_value = mock_response

        from flask import Flask
        app = Flask("test_app")
        with app.app_context():
            engine = BharatGenTranslateEngine("param_lc_translate_ep4")
            response = engine.translate("Translation", "en", "hi", glossary="admin_terms")
            assert response.translated_text == "अनुवाद"

            call_kwargs = mock_client.post.call_args[1]
            assert "using domain terms (admin_terms)" in call_kwargs["json"]["messages"][1]["content"]

    def test_get_supported_languages(self):
        """Test supported languages of BharatGen engine."""
        engine = BharatGenTranslateEngine("param_lc_translate_ep4")
        languages = engine.get_supported_languages()
        assert "en" in languages
        assert "hi" in languages
        assert "ta" in languages
        assert "te" in languages
        assert "sa" in languages


class TestCleanTranslationPreambles:
    """Test suite for stripping conversational LLM preambles from translation outputs."""

    def test_strip_various_preamble_formats(self):
        sample_output = """In Marathi:
जी.ओ. Translated to Marathi:
मेजर जनरल हेक्टर मुन्रो यांनी केले.
Translated to Marathi:
दोनशे युरोपियन पायदळाचे एक विभाग
Here is the Marathi translation:
आर्टिलरीच्या कॉर्प्सचा आणि एका बटालियनच्या शिपायांचा समावेश असेल
Marathi: बॉम्बेच्या शॉर्टेस्ट रूटवर एक डॅचमेंट म्हणून.
Translation:
प्रत्येक युरोपियन बटालियनमध्ये एक कॅप्टन असतो."""

        cleaned = clean_translation_preambles(sample_output, "Marathi")
        assert "In Marathi:" not in cleaned
        assert "Translated to Marathi:" not in cleaned
        assert "Here is the Marathi translation:" not in cleaned
        assert "Marathi:" not in cleaned
        assert "Translation:" not in cleaned
        assert "जी.ओ." in cleaned
        assert "मेजर जनरल हेक्टर मुन्रो यांनी केले." in cleaned
        assert "दोनशे युरोपियन पायदळाचे एक विभाग" in cleaned
        assert "प्रत्येक युरोपियन बटालियनमध्ये एक कॅप्टन असतो." in cleaned

    def test_strip_markdown_code_fences(self):
        wrapped = "```marathi\nभारत एक महान देश आहे.\n```"
        cleaned = clean_translation_preambles(wrapped, "Marathi")
        assert cleaned == "भारत एक महान देश आहे."

    def test_strip_thinking_tags(self):
        with_thinking = "<think>Analysing English sentence...</think>भारत एक महान देश है।"
        cleaned = clean_translation_preambles(with_thinking, "Hindi")
        assert cleaned == "भारत एक महान देश है।"


class TestAvailableTranslationEngines:
    """Test dynamic fetching of translation engines."""

    @patch('httpx.Client')
    def test_get_available_translation_engines(self, mock_client_class):
        """Test fetching available engines includes remote service engines and BharatGen models."""
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
            assert len(engines) == 5
            assert engines[0]["value"] == "indictrans2"
            assert engines[0]["label"] == "IndicTrans v2"
            assert engines[1]["value"] == "gemma"
            assert engines[1]["label"] == "Gemma 4 12B"
            assert engines[2]["value"] == "llm_gemma"
            assert engines[2]["label"] == "LLM Gemma"
            assert engines[3]["value"] == "param_lc_translate_ep4"
            assert engines[3]["label"] == "Param LC Translate EP4"
            assert engines[4]["value"] == "translation_1b_exp_40"
            assert engines[4]["label"] == "Translation 1B Exp 40"

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


class TestTranslationMaskingAndChoices:
    """Test numeric engine masking and choice builders for translation models."""

    def test_normalize_translation_engine(self):
        from kalanjiyam.utils.translation_engine import normalize_translation_engine

        # Numeric keys
        assert normalize_translation_engine("1") == "indictrans2"
        assert normalize_translation_engine("2") == "gemma"
        assert normalize_translation_engine("3") == "param_lc_translate_ep4"
        assert normalize_translation_engine("4") == "translation_1b_exp_40"
        assert normalize_translation_engine("5") == "indictrans3"
        assert normalize_translation_engine("6") == "google"
        assert normalize_translation_engine("7") == "openai"
        assert normalize_translation_engine("8") == "llm_gemma"
        assert normalize_translation_engine("9") == "gemma_4_31b"

        # Service aliases
        assert normalize_translation_engine("gemma-4") == "gemma"
        assert normalize_translation_engine("gemma4") == "gemma"
        assert normalize_translation_engine("gemma-4-31b") == "gemma_4_31b"
        assert normalize_translation_engine("gemma_4_31b") == "gemma_4_31b"
        assert normalize_translation_engine("gemma-31b") == "gemma_4_31b"
        assert normalize_translation_engine("gemma_31b") == "gemma_4_31b"
        assert normalize_translation_engine("llm-gemma") == "llm_gemma"
        assert normalize_translation_engine("llm_gemma") == "llm_gemma"
        assert normalize_translation_engine("param-lc-translate-ep4") == "param_lc_translate_ep4"
        assert normalize_translation_engine("translation-1b-exp-40") == "translation_1b_exp_40"
        assert normalize_translation_engine("indictrans-2") == "indictrans2"
        assert normalize_translation_engine("indictrans-3") == "indictrans3"

        # Canonical names unchanged
        assert normalize_translation_engine("indictrans2") == "indictrans2"
        assert normalize_translation_engine("gemma") == "gemma"
        assert normalize_translation_engine("gemma_4_31b") == "gemma_4_31b"
        assert normalize_translation_engine("llm_gemma") == "llm_gemma"

    def test_build_translation_choices_regular_user(self):
        from kalanjiyam.utils.translation_engine import build_translation_choices

        engines = [
            {"value": "indictrans2", "label": "IndicTrans v2"},
            {"value": "gemma", "label": "Gemma 4 12B"},
            {"value": "gemma_4_31b", "label": "Gemma 4 31B"},
            {"value": "llm_gemma", "label": "LLM Gemma"},
            {"value": "param_lc_translate_ep4", "label": "Param LC Translate EP4"},
        ]
        choices = build_translation_choices(
            available_engines=engines,
            is_super_admin=False,
            recommended_engine="gemma_4_31b",
            default_engine="indictrans2",
        )

        assert len(choices) == 5
        # Masked names for regular users
        assert choices[0]["value"] == "1"
        assert choices[0]["label"] == "Translation 1"
        assert choices[0]["is_default"] is True
        assert choices[0]["is_recommended"] is False

        assert choices[1]["value"] == "2"
        assert choices[1]["label"] == "Translation 2"

        assert choices[2]["value"] == "9"
        assert choices[2]["label"] == "Translation 9"
        assert choices[2]["is_recommended"] is True

        assert choices[3]["value"] == "8"
        assert choices[3]["label"] == "Translation 8"
        assert choices[3]["is_recommended"] is False

        assert choices[4]["value"] == "3"
        assert choices[4]["label"] == "Translation 3"
        assert choices[4]["is_recommended"] is False

    def test_build_translation_choices_super_admin(self):
        from kalanjiyam.utils.translation_engine import build_translation_choices

        engines = [
            {"value": "indictrans2", "label": "IndicTrans v2"},
            {"value": "gemma", "label": "Gemma 4 12B"},
            {"value": "gemma_4_31b", "label": "Gemma 4 31B"},
            {"value": "llm_gemma", "label": "LLM Gemma"},
        ]
        choices = build_translation_choices(
            available_engines=engines,
            is_super_admin=True,
            recommended_engine="9",  # Can also be passed by numeric ID
            default_engine="1",
        )

        assert len(choices) == 4
        # Real labels for super admin
        assert choices[0]["value"] == "1"
        assert choices[0]["label"] == "IndicTrans v2"
        assert choices[0]["is_default"] is True

        assert choices[1]["value"] == "2"
        assert choices[1]["label"] == "Gemma 4 12B"

        assert choices[2]["value"] == "9"
        assert choices[2]["label"] == "Gemma 4 31B"
        assert choices[2]["is_recommended"] is True

        assert choices[3]["value"] == "8"
        assert choices[3]["label"] == "LLM Gemma"

    def test_factory_with_numeric_keys(self):
        # Should be able to create engines by numeric ID
        engine = TranslationEngineFactory.create("1")
        assert isinstance(engine, IndicTransEngine)
        assert engine.version == "indictrans2"

        engine2 = TranslationEngineFactory.create("2")
        assert isinstance(engine2, IndicTransEngine)
        assert engine2.version == "gemma"

        engine9 = TranslationEngineFactory.create("9")
        assert isinstance(engine9, IndicTransEngine)
        assert engine9.version == "gemma_4_31b"

        engine_31b_name = TranslationEngineFactory.create("gemma-4-31b")
        assert isinstance(engine_31b_name, IndicTransEngine)
        assert engine_31b_name.version == "gemma_4_31b"

        engine8 = TranslationEngineFactory.create("8")
        from kalanjiyam.utils.translation_engine import LlmGemmaTranslateEngine
        assert isinstance(engine8, LlmGemmaTranslateEngine)
        assert engine8.model_name == "llm-gemma"

        engine_by_name = TranslationEngineFactory.create("llm_gemma")
        assert isinstance(engine_by_name, LlmGemmaTranslateEngine)

        engine_by_alias = TranslationEngineFactory.create("llm-gemma")
        assert isinstance(engine_by_alias, LlmGemmaTranslateEngine)

        assert TranslationEngineFactory.is_supported("1") is True
        assert TranslationEngineFactory.is_supported("2") is True
        assert TranslationEngineFactory.is_supported("3") is True
        assert TranslationEngineFactory.is_supported("4") is True
        assert TranslationEngineFactory.is_supported("8") is True
        assert TranslationEngineFactory.is_supported("9") is True
        assert TranslationEngineFactory.is_supported("gemma-4-31b") is True
        assert TranslationEngineFactory.is_supported("gemma_4_31b") is True
        assert TranslationEngineFactory.is_supported("llm_gemma") is True
        assert TranslationEngineFactory.is_supported("llm-gemma") is True
        assert TranslationEngineFactory.is_supported("unsupported") is False

    def test_system_settings_translation_fields(self):
        from kalanjiyam.models.settings import SystemSetting

        setting = SystemSetting()
        assert setting.default_translation_engine == "indictrans2"
        assert setting.recommended_translation_engine is None

        # Test property aliases
        setting.default_translation_model = "llm_gemma"
        assert setting.default_translation_engine == "llm_gemma"
        assert setting.default_translation_model == "llm_gemma"

        setting.recommended_translation_model = "param_lc_translate_ep4"
        assert setting.recommended_translation_engine == "param_lc_translate_ep4"
        assert setting.recommended_translation_model == "param_lc_translate_ep4"

        setting.best_translation_model = "translation_1b_exp_40"
        assert setting.recommended_translation_engine == "translation_1b_exp_40"
        assert setting.best_translation_model == "translation_1b_exp_40"


class TestLlmGemmaTranslateEngine:
    """Test LlmGemmaTranslateEngine calling /v1/ocr."""

    @patch("httpx.Client")
    def test_translate_llm_gemma_calls_chat_completions(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello world in English"},
                }
            ]
        }
        mock_client.post.return_value = mock_response

        from flask import Flask
        from kalanjiyam.utils.translation_engine import LlmGemmaTranslateEngine

        app = Flask("test_app")
        app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        app.config["OCR_SERVICE_API_KEY"] = "test-secret"

        with app.app_context():
            engine = LlmGemmaTranslateEngine()
            response = engine.translate("नमस्ते दुनिया", "hi", "en")

            assert response.translated_text == "Hello world in English"
            assert response.engine == "llm_gemma"

            mock_client.post.assert_called_once()
            call_url = mock_client.post.call_args[0][0]
            call_kwargs = mock_client.post.call_args[1]

            assert call_url == "http://ocr.test/v1/chat/completions"
            assert call_kwargs["headers"]["X-API-Key"] == "test-secret"
            assert call_kwargs["headers"]["Content-Type"] == "application/json"
            assert call_kwargs["json"]["model"] == "llm-gemma"
            assert "Translate the following text from Hindi to English" in call_kwargs["json"]["messages"][0]["content"]
            assert "नमस्ते दुनिया" in call_kwargs["json"]["messages"][0]["content"]

    @patch("httpx.Client")
    def test_translate_llm_gemma_fallback_from_ocr(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client

        # First call to /v1/ocr fails (e.g. 422 missing image), second call to /v1/chat/completions succeeds
        ocr_fail = Mock()
        ocr_fail.status_code = 422
        ocr_fail.text = "Missing image"
        ocr_fail.json.return_value = {"detail": "Missing image"}

        chat_success = Mock()
        chat_success.status_code = 200
        chat_success.json.return_value = {
            "choices": [{"message": {"content": "Fallback translation"}}]
        }

        mock_client.post.side_effect = [ocr_fail, chat_success]

        from kalanjiyam.utils.translation_engine import LlmGemmaTranslateEngine

        engine = LlmGemmaTranslateEngine(
            api_url="http://ocr.test/v1/ocr", api_key="test-secret"
        )
        response = engine.translate("Test text", "en", "mr")

        assert response.translated_text == "Fallback translation"
        assert mock_client.post.call_count == 2
        assert mock_client.post.call_args_list[0][0][0] == "http://ocr.test/v1/ocr"
        assert mock_client.post.call_args_list[1][0][0] == "http://ocr.test/v1/chat/completions"

    @patch("httpx.Client")
    def test_translate_llm_gemma_custom_url_override(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Translated"}}]
        }
        mock_client.post.return_value = mock_response

        from flask import Flask
        from kalanjiyam.utils.translation_engine import LlmGemmaTranslateEngine

        app = Flask("test_app")
        app.config["OCR_SERVICE_URL"] = "http://ocr.test"
        app.config["LLM_GEMMA_TRANSLATION_API_URL"] = "http://custom-llm.test/v1/chat/completions"
        app.config["LLM_GEMMA_TRANSLATION_API_KEY"] = "custom-key"

        with app.app_context():
            engine = LlmGemmaTranslateEngine()
            response = engine.translate("Text", "en", "ta")

            assert response.translated_text == "Translated"
            call_url = mock_client.post.call_args[0][0]
            assert call_url == "http://custom-llm.test/v1/chat/completions"
            assert mock_client.post.call_args[1]["headers"]["X-API-Key"] == "custom-key"

    @patch("httpx.Client")
    def test_translate_llm_gemma_cleans_preambles(self, mock_client_class):
        mock_client = Mock()
        mock_client_class.return_value.__enter__.return_value = mock_client
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "<think>thinking</think>\nHere is the English translation:\nDirect text."
                    }
                }
            ]
        }
        mock_client.post.return_value = mock_response

        from flask import Flask
        from kalanjiyam.utils.translation_engine import LlmGemmaTranslateEngine

        app = Flask("test_app")
        app.config["OCR_SERVICE_URL"] = "http://ocr.test"

        with app.app_context():
            engine = LlmGemmaTranslateEngine()
            response = engine.translate("Direct text.", "hi", "en")
            assert response.translated_text == "Direct text."