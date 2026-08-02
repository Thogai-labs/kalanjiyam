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


class IndicTransEngine(TranslationEngine):
    """Client for the external standalone IndicTrans translation service."""

    def __init__(self, version: str):
        # version is 'indictrans2' or 'indictrans3'
        self.version = version

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

        # Map language codes to English names
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

        # Determine the model name based on translation direction
        if source_lang == 'en':
            direction = 'en-indic'
        elif target_lang == 'en':
            direction = 'indic-en'
        else:
            direction = 'indic-indic'

        model_name = f"ai4bharat/{self.version}-{direction}-1B"

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
                engine=self.version,
                metadata={'model': model_name}
            )
        except Exception as e:
            logging.error(f"IndicTrans translation failed: {e}")
            raise

    def get_supported_languages(self) -> List[str]:
        return ['en', 'hi', 'bn', 'ta', 'te', 'mr', 'gu', 'kn', 'ml', 'pa', 'ur', 'or', 'as', 'sa', 'ks', 'sd', 'mni', 'sat', 'npi', 'gom', 'doi', 'brx', 'mai']


class TranslationEngineFactory:
    """Factory for creating translation engines."""
    
    _engines = {
        'indictrans2': lambda: IndicTransEngine('indictrans2'),
    }
    
    @classmethod
    def create(cls, engine_name: str, **kwargs) -> TranslationEngine:
        """Create a translation engine instance.
        
        :param engine_name: Name of the engine ('indictrans2' or other indictrans variants)
        :param kwargs: Additional arguments for the engine
        :return: Translation engine instance
        :raises: ValueError if engine name is not supported
        """
        if engine_name not in cls._engines:
            if engine_name.startswith('indictrans'):
                return IndicTransEngine(engine_name)
            raise ValueError(f"Unsupported translation engine: {engine_name}. Supported engines: {list(cls._engines.keys())}")
        
        engine_class_or_factory = cls._engines[engine_name]
        return engine_class_or_factory()
    
    @classmethod
    def get_supported_engines(cls) -> List[str]:
        """Get list of supported translation engines."""
        return list(cls._engines.keys())

    @classmethod
    def is_supported(cls, engine_name: str) -> bool:
        """Check if the translation engine is supported."""
        return engine_name in cls._engines or engine_name.startswith('indictrans')


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
    """Fetch available translation engines dynamically from the translation service endpoint."""
    import httpx
    from flask import current_app
    base_url = current_app.config.get("TRANSLATION_SERVICE_URL", "").rstrip("/")
    if not base_url:
        return []
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
            versions = set()
            for m in models:
                name = m.get("model_name", "")
                parts = name.split('/')
                if len(parts) > 1:
                    family_part = parts[1]
                    family = family_part.split('-')[0]
                    versions.add(family)
                else:
                    versions.add(name)
            
            choices = []
            label_map = {
                'indictrans2': 'IndicTrans v2',
                'indictrans3': 'IndicTrans v3',
            }
            for v in sorted(list(versions)):
                choices.append({
                    'value': v,
                    'label': label_map.get(v, v.replace('_', ' ').title())
                })
            return choices
    except Exception as e:
        logging.error(f"Failed to fetch translation models: {e}")
    return []


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
 