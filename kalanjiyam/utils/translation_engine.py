"""Unified translation engine interface for proofing projects."""

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
from pathlib import Path

# Translation response data structure
@dataclass
class TranslationResponse:
    """Response from a translation engine."""
    #: The translated text content.
    translated_text: str
    #: Source language code.
    source_language: str
    #: Target language code.
    target_language: str
    #: Translation engine used.
    engine: str
    #: Additional metadata from the translation engine.
    metadata: Optional[Dict[str, Any]] = None


class TranslationEngine(ABC):
    """Abstract base class for translation engines."""
    
    @abstractmethod
    def translate(self, text: str, source_lang: str, target_lang: str, **kwargs) -> TranslationResponse:
        """Translate the given text from source to target language."""
        pass
    
    @abstractmethod
    def get_supported_languages(self) -> List[str]:
        """Get list of supported language codes."""
        pass


class GoogleTranslateEngine(TranslationEngine):
    """Google Translate engine implementation."""
    
    def __init__(self):
        try:
            from googletrans import Translator
            self.translator = Translator()
            self._supported_languages = None
        except ImportError:
            raise ImportError("googletrans library is required for Google Translate. Install with: pip install googletrans==4.0.0rc1")
    
    def translate(self, text: str, source_lang: str, target_lang: str, **kwargs) -> TranslationResponse:
        """Translate text using Google Translate."""
        try:
            # Map language codes to Google Translate format
            # Note: Sanskrit ('sa') is not supported by Google Translate
            language_map = {
                'sa': 'hi',  # Sanskrit -> Hindi (closest available)
                'hi': 'hi',  # Hindi
                'te': 'te',  # Telugu
                'mr': 'mr',  # Marathi
                'bn': 'bn',  # Bengali
                'gu': 'gu',  # Gujarati
                'kn': 'kn',  # Kannada
                'ml': 'ml',  # Malayalam
                'ta': 'ta',  # Tamil
                'pa': 'pa',  # Punjabi
                'or': 'or',  # Odia
                'ur': 'ur',  # Urdu
                'en': 'en',  # English
                'fr': 'fr',  # French
                'de': 'de',  # German
                'es': 'es',  # Spanish
                'ja': 'ja',  # Japanese
                'ko': 'ko',  # Korean
                'zh': 'zh',  # Chinese
                'ru': 'ru',  # Russian
                'ar': 'ar',  # Arabic
                'fa': 'fa',  # Persian
                'th': 'th',  # Thai
            }
            
            # Use mapped language codes or original if not in map
            mapped_source = language_map.get(source_lang, source_lang)
            mapped_target = language_map.get(target_lang, target_lang)
            
            # Warn if Sanskrit is being used (not supported by Google Translate)
            if source_lang == 'sa':
                logging.warning(f"Sanskrit ('sa') is not supported by Google Translate. Using Hindi ('hi') as fallback.")
            
            logging.info(f"Translating from {source_lang} ({mapped_source}) to {target_lang} ({mapped_target})")
            
            # Clean and segment text
            segments = self._segment_text(text)
            translated_segments = []
            last_result = None
            
            for segment in segments:
                if segment.strip():
                    try:
                        result = self.translator.translate(
                            segment, 
                            src=mapped_source, 
                            dest=mapped_target
                        )
                        translated_segments.append(result.text)
                        last_result = result
                    except Exception as segment_error:
                        logging.error(f"Failed to translate segment '{segment[:50]}...': {segment_error}")
                        # Add original text if translation fails
                        translated_segments.append(segment)
                else:
                    translated_segments.append(segment)
            
            translated_text = '\n'.join(translated_segments)
            
            return TranslationResponse(
                translated_text=translated_text,
                source_language=source_lang,
                target_language=target_lang,
                engine='google',
                metadata={'confidence': getattr(last_result, 'confidence', None) if last_result else None}
            )
        except Exception as e:
            logging.error(f"Google Translate failed: {e}")
            raise
    
    def get_supported_languages(self) -> List[str]:
        """Get supported language codes."""
        if self._supported_languages is None:
            try:
                from googletrans import LANGUAGES
                self._supported_languages = list(LANGUAGES.keys())
            except:
                # Fallback to common languages (excluding Sanskrit as it's not supported by Google)
                self._supported_languages = ['en', 'hi', 'te', 'mr', 'bn', 'gu', 'kn', 'ml', 'ta', 'pa', 'or', 'ur', 'fr', 'de', 'es', 'ja', 'ko', 'zh', 'ru', 'ar', 'fa', 'th']
        return self._supported_languages
    
    def _segment_text(self, text: str) -> List[str]:
        """Segment text into sentences or paragraphs for translation."""
        # Split by double newlines (paragraphs)
        paragraphs = text.split('\n\n')
        segments = []
        
        for paragraph in paragraphs:
            if paragraph.strip():
                # Split by single newlines and punctuation
                sentences = re.split(r'(?<=[.!?।॥])\s+', paragraph)
                segments.extend(sentences)
            else:
                segments.append(paragraph)
        
        return segments


class OpenAITranslateEngine(TranslationEngine):
    """OpenAI GPT-based translation engine."""
    
    def __init__(self, api_key: Optional[str] = None):
        try:
            import openai
            self.client = openai.OpenAI(api_key=api_key)
        except ImportError:
            raise ImportError("openai library is required. Install with: pip install openai")
    
    def translate(self, text: str, source_lang: str, target_lang: str, **kwargs) -> TranslationResponse:
        """Translate text using OpenAI GPT."""
        try:
            # Create a prompt for translation
            prompt = f"""Translate the following text from {source_lang} to {target_lang}. 
            Maintain the original formatting, line breaks, and structure.
            Only provide the translation, no explanations.
            
            Text to translate:
            {text}"""
            
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a professional translator. Provide accurate translations while preserving formatting."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000,
                temperature=0.3
            )
            
            translated_text = response.choices[0].message.content.strip()
            
            return TranslationResponse(
                translated_text=translated_text,
                source_language=source_lang,
                target_language=target_lang,
                engine='openai',
                metadata={'model': 'gpt-3.5-turbo', 'usage': response.usage}
            )
        except Exception as e:
            logging.error(f"OpenAI translation failed: {e}")
            raise
    
    def get_supported_languages(self) -> List[str]:
        """Get supported language codes."""
        return ['en', 'hi', 'sa', 'te', 'mr', 'fr', 'de', 'es', 'ja', 'ko', 'zh']


class GenericTranslationEngine(TranslationEngine):
    """Generic translation engine client that forwards translation requests to the backend service."""

    def __init__(self, engine_name: str):
        self.engine_name = engine_name
        self.version = engine_name

    def translate(self, text: str, source_lang: str, target_lang: str, **kwargs) -> TranslationResponse:
        import httpx
        from flask import current_app

        base_url = current_app.config.get("TRANSLATION_SERVICE_URL", "").rstrip("/")
        if not base_url:
            raise RuntimeError("TRANSLATION_SERVICE_URL is not configured")

        url = f"{base_url}/translate/text"
        api_key = current_app.config.get("TRANSLATION_SERVICE_API_KEY", "")
        timeout = float(current_app.config.get("TRANSLATION_SERVICE_TIMEOUT", 300))

        headers = {"X-API-Key": api_key} if api_key else {}

        # Map language codes to English names if available
        language_map = {
            'en': 'English',
            'hi': 'Hindi',
            'bn': 'Bengali',
            'ta': 'Tamil',
            'te': 'Telugu',
            'mr': 'Marathi',
            'gu': 'Gujarati',
            'kn': 'Kannada',
            'ml': 'Malayalam',
            'pa': 'Punjabi',
            'ur': 'Urdu',
            'or': 'Odia',
            'as': 'Assamese',
            'sa': 'Sanskrit',
            'ks': 'Kashmiri',
            'sd': 'Sindhi',
            'mni': 'Manipuri',
            'sat': 'Santali',
            'npi': 'Nepali',
            'gom': 'Konkani',
            'doi': 'Dogri',
            'brx': 'Bodo',
            'mai': 'Maithili',
        }

        source_name = language_map.get(source_lang, source_lang.capitalize())
        target_name = language_map.get(target_lang, target_lang.capitalize())

        # Determine the model name based on translation direction or engine
        if self.engine_name.startswith("indictrans"):
            if source_lang == 'en':
                direction = 'en-indic'
            elif target_lang == 'en':
                direction = 'indic-en'
            else:
                direction = 'indic-indic'
            model_name = f"ai4bharat/{self.engine_name}-{direction}-1B"
        elif "gemma" in self.engine_name.lower():
            model_name = "google/gemma-4-12b-it"
        else:
            model_name = self.engine_name

        payload = {
            "text": text,
            "model_name": model_name,
            "source_language": source_name,
            "target_language": target_name,
            "gpu_id": 0,
            "batch_size": 8
        }
        if kwargs.get("glossary"):
            payload["glossary"] = kwargs["glossary"]

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload, headers=headers)

            if response.status_code >= 400:
                detail = response.text
                try:
                    detail = response.json().get("detail", detail)
                except Exception:
                    pass
                raise RuntimeError(f"Translation service error ({response.status_code}): {detail}")

            result = response.json()
            translated_text = result.get("text", "")

            return TranslationResponse(
                translated_text=translated_text,
                source_language=source_lang,
                target_language=target_lang,
                engine=self.engine_name,
                metadata={'model': model_name}
            )
        except Exception as e:
            logging.error(f"Translation service failed for engine {self.engine_name}: {e}")
            raise

    def get_supported_languages(self) -> List[str]:
        return ['en', 'hi', 'bn', 'ta', 'te', 'mr', 'gu', 'kn', 'ml', 'pa', 'ur', 'or', 'as', 'sa', 'ks', 'sd', 'mni', 'sat', 'npi', 'gom', 'doi', 'brx', 'mai']


def clean_translation_preambles(text: str, target_name: str = "") -> str:
    """Strip conversational prefixes, preambles, labels, and thinking tags from LLM translation output."""
    if not text:
        return ""

    # Strip thinking tags <think>...</think>
    text = re.sub(r"(?s)<think>.*?</think>", "", text).strip()

    # Remove markdown code block wrappers if present (e.g. ```marathi ... ```)
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) > 2:
            text = "\n".join(lines[1:-1]).strip()

    lang_names = r"(?:Marathi|Hindi|Tamil|Telugu|Bengali|Gujarati|Kannada|Malayalam|Punjabi|Odia|Urdu|Assamese|Sanskrit|English|Nepali|Konkani|Sindhi|Dogri|Bodo|Maithili|Santali|Kashmiri|Manipuri)"
    if target_name:
        lang_names = f"(?:{re.escape(target_name)}|{lang_names})"

    patterns = [
        # "Here is the Marathi translation:" / "Here is the translation to Marathi:" / "Here is the translation:"
        rf"(?i)\bhere\s+is\s+(?:the\s+)?(?:{lang_names}\s+)?translation(?:\s+(?:to|in|into)\s+{lang_names})?\s*[:\-–—]?\s*",
        # "Translated to Marathi:" / "Translated in Marathi:"
        rf"(?i)\btranslated\s+(?:to|in|into)\s+{lang_names}\s*[:\-–—]?\s*",
        # "In Marathi:" / "To Marathi:" / "Into Marathi:"
        rf"(?i)\b(?:in|to|into)\s+{lang_names}(?:\s+translation)?\s*[:\-–—]\s*",
        # "Marathi translation:"
        rf"(?i)\b{lang_names}\s+translation\s*[:\-–—]?\s*",
        # "Translation:"
        r"(?i)\btranslation\s*[:\-–—]\s*",
        # Language label prefix at line start: "Marathi: ..."
        rf"(?im)^\s*{lang_names}\s*[:\-–—]\s*",
        # Language label prefix after punctuation: "सेवा. Marathi: ..."
        rf"(?i)(?<=[.!?।॥\n])\s*{lang_names}\s*[:\-–—]\s*",
    ]

    cleaned = text
    for p in patterns:
        cleaned = re.sub(p, "", cleaned)

    # Clean whitespace per line while keeping line structure
    cleaned_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in cleaned.splitlines()]
    result = []
    for line in cleaned_lines:
        if line:
            result.append(line)
        elif result and result[-1] != "":
            result.append("")

    return "\n".join(result).strip()


class BharatGenTranslateEngine(TranslationEngine):
    """Translation engine using BharatGen chat completions API."""

    def __init__(
        self,
        model_name: str = "param_lc_translate_ep4",
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.model_name = model_name
        self.version = model_name
        self._api_url = api_url
        self._api_key = api_key

    def translate(
        self, text: str, source_lang: str, target_lang: str, **kwargs
    ) -> TranslationResponse:
        import httpx
        from flask import current_app, has_app_context

        import os
        api_url = self._api_url
        api_key = self._api_key
        timeout = 300.0

        if has_app_context():
            if not api_url:
                api_url = current_app.config.get("BHARATGEN_TRANSLATION_API_URL")
            if not api_key:
                api_key = current_app.config.get(
                    "BHARATGEN_TRANSLATION_API_KEY"
                ) or current_app.config.get("BHARATGEN_API_KEY")
            timeout = float(
                current_app.config.get("BHARATGEN_TRANSLATION_TIMEOUT", 300)
            )

        if not api_url:
            api_url = (
                os.environ.get("BHARATGEN_TRANSLATION_API_URL")
                or "https://api.bharatgen.dev/v1/chat/completions"
            )

        if not api_key:
            api_key = os.environ.get(
                "BHARATGEN_TRANSLATION_API_KEY"
            ) or os.environ.get("BHARATGEN_API_KEY")

        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                api_key = os.environ.get(
                    "BHARATGEN_TRANSLATION_API_KEY"
                ) or os.environ.get("BHARATGEN_API_KEY")
            except Exception:
                pass

        if not api_key or not str(api_key).strip():
            raise RuntimeError(
                "BHARATGEN_TRANSLATION_API_KEY is not configured on the server. "
                "Please add BHARATGEN_TRANSLATION_API_KEY to your .env file or server environment variables."
            )

        api_key = str(api_key).strip().strip("'").strip('"')

        language_map = {
            "en": "English",
            "hi": "Hindi",
            "bn": "Bengali",
            "ta": "Tamil",
            "te": "Telugu",
            "mr": "Marathi",
            "gu": "Gujarati",
            "kn": "Kannada",
            "ml": "Malayalam",
            "pa": "Punjabi",
            "ur": "Urdu",
            "or": "Odia",
            "as": "Assamese",
            "sa": "Sanskrit",
            "ks": "Kashmiri",
            "sd": "Sindhi",
            "mni": "Manipuri",
            "sat": "Santali",
            "npi": "Nepali",
            "gom": "Konkani",
            "doi": "Dogri",
            "brx": "Bodo",
            "mai": "Maithili",
        }

        source_name = language_map.get(source_lang, source_lang.capitalize())
        target_name = language_map.get(target_lang, target_lang.capitalize())

        system_content = (
            f"You are a professional machine translation system. Your sole task is to accurately translate text from {source_name} to {target_name}. "
            f"Output ONLY the direct translated text. "
            f"Do NOT write any introduction, notes, explanations, preambles, or labels (e.g. do NOT output 'Here is the translation', 'Translation:', 'In {target_name}:', '{target_name}:', 'Translated to {target_name}:'). "
            f"Preserve all line breaks, formatting, and special tags exactly."
        )

        user_content = f"Translate the following text from {source_name} to {target_name}:\n\n{text}"
        if kwargs.get("glossary"):
            user_content = f"Translate the following text from {source_name} to {target_name} using domain terms ({kwargs['glossary']}):\n\n{text}"

        payload = {
            "model": self.model_name,
            "temperature": kwargs.get("temperature", 0.1),
            "repetition_penalty": kwargs.get("repetition_penalty", 1.02),
            "max_length": kwargs.get("max_length", 2048),
            "chat_template_kwargs": {
                "enable_thinking": kwargs.get("enable_thinking", True)
            },
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": (
                f"Bearer {api_key}"
                if not api_key.startswith("Bearer ")
                else api_key
            ),
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(api_url, json=payload, headers=headers)

            if response.status_code >= 400:
                detail = response.text
                try:
                    res_json = response.json()
                    detail = (
                        res_json.get("detail")
                        or res_json.get("error", {}).get("message")
                        or detail
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    f"BharatGen translation service error ({response.status_code}): {detail}"
                )

            result = response.json()
            choices = result.get("choices", [])
            if not choices:
                raise RuntimeError(
                    f"Invalid response from BharatGen translation API: {result}"
                )

            content = choices[0].get("message", {}).get("content", "")
            # Clean reasoning tags, markdown code blocks, and conversational preambles/labels
            content = clean_translation_preambles(content, target_name)

            return TranslationResponse(
                translated_text=content,
                source_language=source_lang,
                target_language=target_lang,
                engine=self.model_name,
                metadata={"model": self.model_name, "usage": result.get("usage")},
            )
        except Exception as e:
            logging.error(
                f"BharatGen translation service failed for model {self.model_name}: {e}"
            )
            raise

    def get_supported_languages(self) -> List[str]:
        return [
            "en",
            "hi",
            "bn",
            "ta",
            "te",
            "mr",
            "gu",
            "kn",
            "ml",
            "pa",
            "ur",
            "or",
            "as",
            "sa",
            "ks",
            "sd",
            "mni",
            "sat",
            "npi",
            "gom",
            "doi",
            "brx",
            "mai",
        ]


class LlmGemmaTranslateEngine(TranslationEngine):
    """Translation engine using llm-gemma via /v1/chat/completions or /v1/ocr."""

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 300.0,
    ):
        self.engine_name = "llm_gemma"
        self.model_name = "llm-gemma"
        self.version = "llm-gemma"
        self._api_url = api_url
        self._api_key = api_key
        self._timeout = timeout

    def translate(
        self, text: str, source_lang: str, target_lang: str, **kwargs
    ) -> TranslationResponse:
        import httpx
        from flask import current_app, has_app_context
        import os

        api_url = self._api_url
        api_key = self._api_key
        timeout = self._timeout

        if has_app_context():
            if not api_url:
                api_url = current_app.config.get("LLM_GEMMA_TRANSLATION_API_URL")
                if not api_url:
                    ocr_base = (current_app.config.get("OCR_SERVICE_URL") or "").rstrip("/")
                    if ocr_base:
                        if ocr_base.endswith("/v1/ocr"):
                            api_url = ocr_base[:-7] + "/v1/chat/completions"
                        elif ocr_base.endswith("/v1"):
                            api_url = ocr_base + "/chat/completions"
                        else:
                            api_url = ocr_base + "/v1/chat/completions"
            if not api_key:
                api_key = (
                    current_app.config.get("LLM_GEMMA_TRANSLATION_API_KEY")
                    or current_app.config.get("OCR_SERVICE_API_KEY")
                    or ""
                )
            timeout = float(
                current_app.config.get(
                    "LLM_GEMMA_TRANSLATION_TIMEOUT",
                    current_app.config.get("OCR_SERVICE_TIMEOUT", 300),
                )
            )

        if not api_url:
            api_url = os.environ.get("LLM_GEMMA_TRANSLATION_API_URL")
            if not api_url:
                ocr_base = (os.environ.get("OCR_SERVICE_URL") or "http://localhost:8000").rstrip("/")
                if ocr_base.endswith("/v1/ocr"):
                    api_url = ocr_base[:-7] + "/v1/chat/completions"
                elif ocr_base.endswith("/v1"):
                    api_url = ocr_base + "/chat/completions"
                else:
                    api_url = ocr_base + "/v1/chat/completions"

        if not api_key:
            api_key = (
                os.environ.get("LLM_GEMMA_TRANSLATION_API_KEY")
                or os.environ.get("OCR_SERVICE_API_KEY")
                or ""
            )

        language_map = {
            "en": "English",
            "hi": "Hindi",
            "bn": "Bengali",
            "ta": "Tamil",
            "te": "Telugu",
            "mr": "Marathi",
            "gu": "Gujarati",
            "kn": "Kannada",
            "ml": "Malayalam",
            "pa": "Punjabi",
            "ur": "Urdu",
            "or": "Odia",
            "as": "Assamese",
            "sa": "Sanskrit",
            "ks": "Kashmiri",
            "sd": "Sindhi",
            "mni": "Manipuri",
            "sat": "Santali",
            "npi": "Nepali",
            "gom": "Konkani",
            "doi": "Dogri",
            "brx": "Bodo",
            "mai": "Maithili",
        }

        source_name = language_map.get(source_lang, source_lang.capitalize())
        target_name = language_map.get(target_lang, target_lang.capitalize())

        glossary_part = (
            f" Use domain glossary terms: {kwargs['glossary']}."
            if kwargs.get("glossary")
            else ""
        )
        prompt = (
            f"You are a professional machine translation system. "
            f"Translate the following text from {source_name} to {target_name}.{glossary_part}\n"
            f"Maintain the original formatting, line breaks, and structure.\n"
            f"Output ONLY the direct translated text. Do not add any explanations, notes, labels, or preamble.\n\n"
            f"Text to translate:\n{text}"
        )

        headers = {"Content-Type": "application/json"}
        if api_key and str(api_key).strip():
            clean_key = str(api_key).strip().strip("'").strip('"')
            headers["X-API-Key"] = clean_key

        def _call_chat(url: str):
            chat_payload = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            }
            with httpx.Client(timeout=timeout) as client:
                res = client.post(url, json=chat_payload, headers=headers)
            if res.status_code >= 400:
                detail = res.text
                try:
                    res_json = res.json()
                    detail = (
                        res_json.get("detail")
                        or res_json.get("error", {}).get("message")
                        or detail
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    f"LLM Gemma translation service error ({res.status_code}): {detail}"
                )
            res_json = res.json()
            choices = res_json.get("choices") or []
            translated_str = ""
            if choices and isinstance(choices, list):
                msg = choices[0].get("message") or {}
                translated_str = msg.get("content") or ""
            return translated_str, res_json

        def _call_ocr(url: str):
            ocr_payload = {
                "text": text,
                "prompt": prompt,
                "model_name": self.model_name,
                "engine": self.model_name,
                "source_language": source_name,
                "target_language": target_name,
                "source_lang": source_lang,
                "target_lang": target_lang,
            }
            with httpx.Client(timeout=timeout) as client:
                res = client.post(url, json=ocr_payload, headers=headers)
            if res.status_code >= 400:
                detail = res.text
                try:
                    res_json = res.json()
                    detail = (
                        res_json.get("detail")
                        or res_json.get("error", {}).get("message")
                        or detail
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    f"LLM Gemma translation service error ({res.status_code}): {detail}"
                )
            res_json = res.json()
            translated_str = (
                res_json.get("translated_text")
                or res_json.get("text")
                or res_json.get("text_content")
            )
            return translated_str or "", res_json

        try:
            if api_url.endswith("/v1/ocr"):
                try:
                    translated, result = _call_ocr(api_url)
                except Exception as ocr_err:
                    logging.warning(
                        f"LLM Gemma translation via /v1/ocr failed ({ocr_err}), falling back to /v1/chat/completions"
                    )
                    chat_url = api_url[:-7] + "/v1/chat/completions"
                    translated, result = _call_chat(chat_url)
                    api_url = chat_url
            else:
                translated, result = _call_chat(api_url)

            if translated is None:
                translated = ""

            translated = clean_translation_preambles(translated, target_name)

            return TranslationResponse(
                translated_text=translated,
                source_language=source_lang,
                target_language=target_lang,
                engine=self.engine_name,
                metadata={
                    "model": self.model_name,
                    "endpoint": api_url,
                    "usage": result.get("usage"),
                },
            )
        except Exception as e:
            logging.error(f"LLM Gemma translation failed for {self.model_name}: {e}")
            raise

    def get_supported_languages(self) -> List[str]:
        return [
            "en",
            "hi",
            "bn",
            "ta",
            "te",
            "mr",
            "gu",
            "kn",
            "ml",
            "pa",
            "ur",
            "or",
            "as",
            "sa",
            "ks",
            "sd",
            "mni",
            "sat",
            "npi",
            "gom",
            "doi",
            "brx",
            "mai",
        ]


# Backward-compatible alias
IndicTransEngine = GenericTranslationEngine


SUPPORTED_TRANSLATION_ENGINES = [
    "indictrans2",
    "gemma",
    "llm_gemma",
    "param_lc_translate_ep4",
    "translation_1b_exp_40",
    "indictrans3",
    "google",
    "openai",
]

TRANSLATION_SERVICE_ENGINE_ALIASES = {
    "indictrans-2": "indictrans2",
    "indictrans_2": "indictrans2",
    "indictrans-3": "indictrans3",
    "indictrans_3": "indictrans3",
    "gemma-4": "gemma",
    "gemma4": "gemma",
    "gemma_4": "gemma",
    "llm-gemma": "llm_gemma",
    "llm_gemma": "llm_gemma",
    "param-lc-translate-ep4": "param_lc_translate_ep4",
    "param_lc": "param_lc_translate_ep4",
    "translation-1b-exp-40": "translation_1b_exp_40",
    "translation_1b": "translation_1b_exp_40",
}

TRANSLATION_ENGINE_MAP = {
    "1": "indictrans2",
    "2": "gemma",
    "3": "param_lc_translate_ep4",
    "4": "translation_1b_exp_40",
    "5": "indictrans3",
    "6": "google",
    "7": "openai",
    "8": "llm_gemma",
}

REVERSE_TRANSLATION_ENGINE_MAP = {v: k for k, v in TRANSLATION_ENGINE_MAP.items()}

TRANSLATION_ENGINE_LABELS = {
    "indictrans2": "IndicTrans v2",
    "gemma": "Gemma 4 12B",
    "llm_gemma": "LLM Gemma",
    "param_lc_translate_ep4": "Param LC Translate EP4",
    "translation_1b_exp_40": "Translation 1B Exp 40",
    "indictrans3": "IndicTrans v3",
    "google": "Google",
    "openai": "OpenAI",
}


def normalize_translation_service_engine(engine: str) -> str:
    """Map a translation service engine id or alias to the Kalanjiyam internal id."""
    name = (engine or "").lower().strip().replace("-", "_")
    for service_id, app_id in TRANSLATION_SERVICE_ENGINE_ALIASES.items():
        if name == service_id.replace("-", "_"):
            return app_id
    return name


def normalize_translation_engine(engine: str) -> str:
    """Normalize numeric masked key or alias to canonical translation engine name."""
    if not engine:
        return ""
    stripped = str(engine).strip()
    if stripped in TRANSLATION_ENGINE_MAP:
        return TRANSLATION_ENGINE_MAP[stripped]
    return normalize_translation_service_engine(stripped)


def build_translation_choices(
    available_engines: Optional[List[Any]] = None,
    is_super_admin: bool = False,
    recommended_engine: Optional[str] = None,
    default_engine: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Build the list of translation engine choices for forms and UI.

    Value is the stable numeric key (matches JS decodeTranslationEngine).
    Label is "Translation N" for regular users, real name for super admins.
    """
    if available_engines is None:
        raw_list = get_available_translation_engines()
    else:
        raw_list = available_engines

    choices = []
    seq = 1
    seen_engines = set()

    for item in raw_list:
        if isinstance(item, dict):
            raw_name = (
                item.get("value")
                or item.get("engine")
                or item.get("model_name")
                or ""
            )
            item_label = item.get("label")
        else:
            raw_name = str(item)
            item_label = None

        engine_name = normalize_translation_engine(raw_name)
        if not engine_name or engine_name in seen_engines:
            continue
        seen_engines.add(engine_name)

        numeric_value = REVERSE_TRANSLATION_ENGINE_MAP.get(engine_name, str(seq))
        real_name = item_label or TRANSLATION_ENGINE_LABELS.get(
            engine_name, engine_name.replace("_", " ").title()
        )
        label = real_name if is_super_admin else f"Translation {numeric_value}"
        is_rec = bool(
            recommended_engine
            and (engine_name == recommended_engine or numeric_value == recommended_engine)
        )
        is_def = bool(
            default_engine
            and (engine_name == default_engine or numeric_value == default_engine)
        )

        choices.append(
            {
                "value": numeric_value,
                "label": label,
                "engine": engine_name,
                "is_recommended": is_rec,
                "is_default": is_def,
            }
        )
        seq += 1

    return choices


class TranslationEngineFactory:
    """Dynamic factory for creating translation engines."""

    _engines = {
        "indictrans2": lambda: GenericTranslationEngine("indictrans2"),
        "gemma": lambda: GenericTranslationEngine("gemma"),
        "llm_gemma": lambda: LlmGemmaTranslateEngine(),
        "param_lc_translate_ep4": lambda: BharatGenTranslateEngine(
            "param_lc_translate_ep4"
        ),
        "translation_1b_exp_40": lambda: BharatGenTranslateEngine(
            "translation_1b_exp_40"
        ),
    }

    @classmethod
    def create(cls, engine_name: str, **kwargs) -> TranslationEngine:
        """Create a translation engine instance dynamically.

        :param engine_name: Name of the engine ('indictrans2', 'gemma', 'llm_gemma', 'param_lc_translate_ep4', 'translation_1b_exp_40', or any backend model)
        :param kwargs: Additional arguments for the engine
        :return: Translation engine instance
        :raises: ValueError if engine name is not supported
        """
        engine_name = normalize_translation_engine(engine_name)
        if not engine_name or engine_name in ["unsupported"]:
            raise ValueError(f"Unsupported translation engine: {engine_name}")

        if engine_name in cls._engines:
            return cls._engines[engine_name]()
        if engine_name == "google":
            return GoogleTranslateEngine()
        if engine_name == "openai":
            return OpenAITranslateEngine(**kwargs)
        if engine_name in ("llm_gemma", "llm-gemma"):
            return LlmGemmaTranslateEngine(**kwargs)
        if (
            engine_name in ("param_lc_translate_ep4", "translation_1b_exp_40")
            or "param_lc" in engine_name
            or "translation_1b" in engine_name
        ):
            return BharatGenTranslateEngine(engine_name)
        return GenericTranslationEngine(engine_name)

    @classmethod
    def get_supported_engines(cls) -> List[str]:
        """Get list of supported translation engines dynamically."""
        dynamic = get_available_translation_engines()
        if dynamic:
            return [e["value"] for e in dynamic]
        return list(cls._engines.keys())

    @classmethod
    def is_supported(cls, engine_name: str) -> bool:
        """Check if the translation engine is supported."""
        engine_name = normalize_translation_engine(engine_name)
        if not engine_name or engine_name in ["unsupported"]:
            return False
        return True


def protect_dnt_and_math(text: str) -> tuple[str, dict[str, str]]:
    """Detect <dnt> blocks and math equations, wrap math in <dnt> if needed, and substitute with safe placeholders.
    
    :param text: Input string to process
    :return: A tuple of (protected_text, dnt_map)
    """
    if not text:
        return text, {}

    processed_text = text

    # Step 1: Extract existing <dnt>...</dnt> blocks to prevent double-wrapping
    existing_dnts = []
    def _extract_existing(m):
        idx = len(existing_dnts)
        existing_dnts.append(m.group(0))
        return f"__EXISTING_DNT_{idx}__"

    processed_text = re.sub(r'(?i)<dnt\b[^>]*>[\s\S]*?</dnt>', _extract_existing, processed_text)

    # Step 2: Temporarily protect HTML tags so math patterns do not match inside tags or attributes (src=, href=, etc.)
    html_tags = []
    def _extract_html_tag(m):
        tag = m.group(0)
        if "__EXISTING_DNT_" in tag:
            return tag
        idx = len(html_tags)
        html_tags.append(tag)
        return f"__TEMP_HTML_TAG_{idx}__"

    processed_text = re.sub(r'<[^>]+>', _extract_html_tag, processed_text)

    # Step 3: Wrap math patterns in <dnt>...</dnt>
    math_patterns = [
        r'\$\$[\s\S]*?\$\$',  # $$...$$
        r'\\\[[\s\S]*?\\\]',  # \[...\]
        r'\\\([\s\S]*?\\\)',  # \(...\)
        r'<(?:math|math-field)\b[^>]*>[\s\S]*?</(?:math|math-field)>',
        r'<(?:span|div)\b[^>]*(?:class="[^"]*\bmath\b[^"]*"|data-type="math")[^>]*>[\s\S]*?</(?:span|div)>',
    ]

    for pattern in math_patterns:
        processed_text = re.sub(pattern, lambda m: f"<dnt>{m.group(0)}</dnt>", processed_text)

    # Step 4: Wrap single-dollar math ($formula$) carefully
    def _is_math_dollar(match):
        full_match = match.group(0)
        content = match.group(1).strip()
        # Do not treat URLs, file paths (containing / or .png/.jpg), or HTTP links as math
        if re.search(r'[/\\.]', content) or 'http:' in content or 'https:' in content:
            return full_match
        # Require math symbols or valid math variable expressions
        if re.search(r'[\\+=\^_{\}]', content) or (re.search(r'[a-zA-Z]', content) and len(content) <= 50):
            return f"<dnt>{full_match}</dnt>"
        return full_match

    processed_text = re.sub(r'(?<!\$)\$([^$\n\r<>]+?)\$(?!\$)', _is_math_dollar, processed_text)

    # Step 5: Restore HTML tags
    for idx, tag in enumerate(html_tags):
        processed_text = processed_text.replace(f"__TEMP_HTML_TAG_{idx}__", tag)

    # Step 6: Restore existing <dnt> blocks
    for idx, dnt_content in enumerate(existing_dnts):
        processed_text = processed_text.replace(f"__EXISTING_DNT_{idx}__", dnt_content)

    # Step 7: Substitute all <dnt>...</dnt> blocks with unique placeholders
    dnt_map = {}
    def _replace_dnt(match):
        idx = len(dnt_map)
        placeholder = f"DNTBLOCK{idx}DNT"
        dnt_map[placeholder] = match.group(0)
        return placeholder

    protected_text = re.sub(r'(?i)<dnt\b[^>]*>[\s\S]*?</dnt>', _replace_dnt, processed_text)
    return protected_text, dnt_map


def restore_dnt_and_math(text: str, dnt_map: dict[str, str]) -> str:
    """Restore placeholders back to original <dnt>...</dnt> blocks.
    
    :param text: Translated text containing placeholders
    :param dnt_map: Mapping from placeholder to original <dnt> block
    :return: Restored text
    """
    if not text:
        return text

    result = text
    if dnt_map:
        for placeholder, original_content in dnt_map.items():
            if placeholder in result:
                result = result.replace(placeholder, original_content)
            else:
                pattern = re.escape(placeholder)
                result = re.sub(pattern, original_content, result, flags=re.IGNORECASE)

    # Sanitize any image URLs where $ or <dnt> was inserted around extracted filenames
    result = re.sub(r'(?i)<dnt>([^<]*extracted_[a-zA-Z0-9_\-]+\.(?:png|jpg|jpeg|gif|svg)[^<]*)</dnt>', r'\1', result)
    result = re.sub(r'\$+(extracted_[a-zA-Z0-9_\-]+\.(?:png|jpg|jpeg|gif|svg))\$+', r'\1', result)
    result = re.sub(r'(/images/)\$+(extracted_[a-zA-Z0-9_\-]+)\.(png|jpg|jpeg|gif|svg)\$+', r'\1\2.\3', result)
    result = re.sub(r'/(?:images|uploads)/[^\'"\s]*?\$+(extracted_[a-zA-Z0-9_\-]+\.(?:png|jpg|jpeg|gif|svg))\$*', lambda m: m.group(0).replace('$', ''), result)

    return result


def translate_text(text: str, source_lang: str, target_lang: str, engine_name: str = 'indictrans2', **kwargs) -> TranslationResponse:
    """Convenience function to translate text using the specified engine.
    
    :param text: Text to translate
    :param source_lang: Source language code
    :param target_lang: Target language code
    :param engine_name: Translation engine to use
    :param kwargs: Additional arguments for the engine
    :return: Translation response
    """
    try:
        # Validate input
        if not text or not text.strip():
            raise ValueError("Text to translate cannot be empty")
        
        if not source_lang or not target_lang:
            raise ValueError("Source and target language codes are required")
        
        logging.info(f"Starting translation: {source_lang} -> {target_lang} using {engine_name}")
        start_time = time.time()
        
        # Protect <dnt> blocks and math equations
        protected_text, dnt_map = protect_dnt_and_math(text)

        engine = TranslationEngineFactory.create(engine_name, **kwargs)
        response = engine.translate(protected_text, source_lang, target_lang, **kwargs)

        latency_ms = round((time.time() - start_time) * 1000, 2)
        try:
            from kalanjiyam.utils.metrics import record_metric
            record_metric(
                category="translation",
                name=f"translation.{engine_name}",
                latency_ms=latency_ms,
                status="SUCCESS",
                details={"engine": engine_name, "source_lang": source_lang, "target_lang": target_lang},
            )
        except Exception:
            pass

        # Restore <dnt> blocks and math equations
        if dnt_map and response and response.translated_text:
            response.translated_text = restore_dnt_and_math(response.translated_text, dnt_map)

        return response
    except Exception as e:
        logging.error(f"Translation failed: {e}")
        try:
            from kalanjiyam.utils.metrics import record_metric
            record_metric(
                category="translation",
                name=f"translation.{engine_name}",
                status="FAILED",
                error_level="ERROR",
                error_message=str(e),
                details={"engine": engine_name, "source_lang": source_lang, "target_lang": target_lang},
            )
        except Exception:
            pass
        raise


def segment_text_for_translation(text: str, max_length: int = 1000) -> List[str]:
    """Segment text into chunks suitable for translation.
    
    :param text: Text to segment
    :param max_length: Maximum length of each segment
    :return: List of text segments
    """
    if len(text) <= max_length:
        return [text]
    
    # Split by paragraphs first
    paragraphs = text.split('\n\n')
    segments = []
    current_segment = ""
    
    for paragraph in paragraphs:
        # If adding this paragraph would exceed max_length, start a new segment
        if len(current_segment) + len(paragraph) + 2 > max_length:  # +2 for '\n\n'
            if current_segment:
                segments.append(current_segment.strip())
                current_segment = ""
            
            # If a single paragraph is too long, split it by sentences
            if len(paragraph) > max_length:
                sentences = re.split(r'(?<=[.!?।॥])\s+', paragraph)
                for sentence in sentences:
                    if len(current_segment) + len(sentence) > max_length:
                        if current_segment:
                            segments.append(current_segment.strip())
                            current_segment = ""
                        # If a single sentence is too long, split it by words
                        if len(sentence) > max_length:
                            words = sentence.split()
                            for word in words:
                                if len(current_segment) + len(word) + 1 > max_length:
                                    if current_segment:
                                        segments.append(current_segment.strip())
                                        current_segment = ""
                                current_segment += word + " "
                        else:
                            current_segment += sentence + " "
                    else:
                        current_segment += sentence + " "
            else:
                current_segment = paragraph + '\n\n'
        else:
            current_segment += paragraph + '\n\n'
    
    if current_segment:
        segments.append(current_segment.strip())
    
    return segments 


def get_available_translation_engines() -> List[Dict[str, str]]:
    """Fetch available translation engines dynamically from the translation service endpoint and include BharatGen models."""
    import httpx
    from flask import current_app, has_app_context

    seen_engines = {}

    if has_app_context():
        base_url = current_app.config.get("TRANSLATION_SERVICE_URL", "").rstrip("/")
        if base_url:
            url = f"{base_url}/models"
            api_key = current_app.config.get("TRANSLATION_SERVICE_API_KEY", "")
            headers = {"X-API-Key": api_key} if api_key else {}
            try:
                # Use a short timeout (5s) for fetching available models to avoid blocking the app
                timeout = 5.0
                with httpx.Client(timeout=timeout) as client:
                    response = client.get(url, headers=headers)
                if response.status_code == 200:
                    models = response.json()
                    for m in models:
                        # Use backend provided engine/label or derive intelligently
                        engine_val = m.get("engine")
                        if not engine_val:
                            name = m.get("model_name", "")
                            parts = name.split('/')
                            if len(parts) > 1:
                                family_part = parts[1]
                                if family_part.startswith("indictrans"):
                                    engine_val = family_part.split('-')[0]
                                elif "gemma" in family_part.lower():
                                    if "llm" in family_part.lower():
                                        engine_val = "llm_gemma"
                                    else:
                                        engine_val = "gemma"
                                else:
                                    engine_val = family_part.split('-')[0]
                            else:
                                if "llm" in name.lower() and "gemma" in name.lower():
                                    engine_val = "llm_gemma"
                                else:
                                    engine_val = "gemma" if "gemma" in name.lower() else name
                        
                        label_val = m.get("label")
                        if not label_val:
                            label_map = {
                                'indictrans2': 'IndicTrans v2',
                                'indictrans3': 'IndicTrans v3',
                                'gemma': 'Gemma 4 12B',
                                'gemma4': 'Gemma 4 12B',
                                'llm_gemma': 'LLM Gemma',
                                'llm-gemma': 'LLM Gemma',
                                'param_lc_translate_ep4': 'Param LC Translate EP4',
                                'translation_1b_exp_40': 'Translation 1B Exp 40',
                            }
                            label_val = label_map.get(engine_val, engine_val.replace('_', ' ').replace('-', ' ').title())

                        if engine_val not in seen_engines:
                            seen_engines[engine_val] = {
                                'value': engine_val,
                                'label': label_val,
                                'model_name': m.get("model_name", ""),
                            }
            except Exception as e:
                logging.error(f"Failed to fetch translation models: {e}")

    # Fallback to default remote engines if none discovered
    if not seen_engines:
        seen_engines['indictrans2'] = {
            'value': 'indictrans2',
            'label': 'IndicTrans v2',
            'model_name': 'ai4bharat/indictrans2',
        }
        seen_engines['gemma'] = {
            'value': 'gemma',
            'label': 'Gemma 4 12B',
            'model_name': 'google/gemma-4-12b-it',
        }
        seen_engines['llm_gemma'] = {
            'value': 'llm_gemma',
            'label': 'LLM Gemma',
            'model_name': 'llm-gemma',
        }

    # Add llm-gemma model
    llm_gemma_model = {
        'value': 'llm_gemma',
        'label': 'LLM Gemma',
        'model_name': 'llm-gemma',
    }
    if llm_gemma_model['value'] not in seen_engines:
        seen_engines[llm_gemma_model['value']] = llm_gemma_model

    # Add BharatGen models
    bharatgen_models = [
        {
            'value': 'param_lc_translate_ep4',
            'label': 'Param LC Translate EP4',
            'model_name': 'param_lc_translate_ep4',
        },
        {
            'value': 'translation_1b_exp_40',
            'label': 'Translation 1B Exp 40',
            'model_name': 'translation_1b_exp_40',
        },
    ]
    for bg in bharatgen_models:
        if bg['value'] not in seen_engines:
            seen_engines[bg['value']] = bg

    sort_order = {
        'indictrans2': 0,
        'gemma': 1,
        'llm_gemma': 2,
        'param_lc_translate_ep4': 3,
        'translation_1b_exp_40': 4,
        'indictrans3': 5,
    }
    sorted_choices = sorted(
        list(seen_engines.values()),
        key=lambda x: sort_order.get(x['value'], 99)
    )
    return sorted_choices


def get_supported_languages_list() -> List[Dict[str, str]]:
    """Get list of supported languages for display, matching IndicTrans and other engines."""
    language_names = {
        'sa': 'Sanskrit',
        'hi': 'Hindi',
        'en': 'English',
        'ta': 'Tamil',
        'te': 'Telugu',
        'mr': 'Marathi',
        'kn': 'Kannada',
        'ml': 'Malayalam',
        'bn': 'Bengali',
        'gu': 'Gujarati',
        'or': 'Odia',
        'pa': 'Punjabi',
        'ur': 'Urdu',
        'as': 'Assamese',
        'ks': 'Kashmiri',
        'sd': 'Sindhi',
        'mni': 'Manipuri',
        'sat': 'Santali',
        'npi': 'Nepali',
        'gom': 'Konkani',
        'doi': 'Dogri',
        'brx': 'Bodo',
        'mai': 'Maithili',
    }
    return [{'code': code, 'name': f"{name} ({code})"} for code, name in language_names.items()]
 