"""Unified translation engine interface for proofing projects."""

import logging
import re
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


class TranslationEngineFactory:
    """Factory for creating translation engines."""
    
    _engines = {
        'google': GoogleTranslateEngine,
        'openai': OpenAITranslateEngine,
    }
    
    @classmethod
    def create(cls, engine_name: str, **kwargs) -> TranslationEngine:
        """Create a translation engine instance.
        
        :param engine_name: Name of the engine ('google' or 'openai')
        :param kwargs: Additional arguments for the engine
        :return: Translation engine instance
        :raises: ValueError if engine name is not supported
        """
        if engine_name not in cls._engines:
            raise ValueError(f"Unsupported translation engine: {engine_name}. Supported engines: {list(cls._engines.keys())}")
        
        engine_class = cls._engines[engine_name]
        
        # Handle different constructor signatures
        if engine_name == 'google':
            return engine_class()  # GoogleTranslateEngine doesn't take kwargs
        elif engine_name == 'openai':
            api_key = kwargs.get('api_key')
            return engine_class(api_key=api_key)
        else:
            return engine_class(**kwargs)
    
    @classmethod
    def get_supported_engines(cls) -> List[str]:
        """Get list of supported translation engines."""
        return list(cls._engines.keys())


def translate_text(text: str, source_lang: str, target_lang: str, engine_name: str = 'google', **kwargs) -> TranslationResponse:
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
        
        engine = TranslationEngineFactory.create(engine_name, **kwargs)
        return engine.translate(text, source_lang, target_lang, **kwargs)
    except Exception as e:
        logging.error(f"Translation failed: {e}")
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