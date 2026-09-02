"""Automated translation script for UI catalog files (.po) using Gemma / BharatGen / LLM backend.

Usage:
    uv run python -m kalanjiyam.scripts.translate_catalogs
    uv run python -m kalanjiyam.scripts.translate_catalogs --locales ta hi_IN sa te_IN
    uv run python -m kalanjiyam.scripts.translate_catalogs --engine llm_gemma --batch-size 15
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from babel.messages import pofile
from babel.messages.catalog import Catalog, Message

# Setup project root
PROJECT_DIR = Path(__file__).resolve().parents[2]
TRANSLATIONS_DIR = PROJECT_DIR / "kalanjiyam" / "translations"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("translate_catalogs")


def normalize_locale(locale_code: str) -> str:
    """Normalize locale string for engine language mappings."""
    mapping = {
        "ta": "ta",
        "hi_IN": "hi",
        "hi": "hi",
        "sa": "sa",
        "te_IN": "te",
        "te": "te",
        "en": "en",
    }
    return mapping.get(locale_code, locale_code.split("_")[0])


def clean_json_response(raw_text: str) -> str:
    """Extract JSON object from LLM output (stripping thinking blocks, code fences, etc.)."""
    if not raw_text:
        return "{}"

    # Strip <think>...</think>
    text = re.sub(r"(?s)<think>.*?</think>", "", raw_text).strip()

    # Extract markdown code fence json if present
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()

    # Find the outermost { and }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    return text


def batch_translate_strings(
    engine: Any,
    items: List[Tuple[str, str]],  # [(key, text_to_translate), ...]
    target_locale: str,
    source_locale: str = "en",
) -> Dict[str, str]:
    """Translate a batch of strings using structured JSON prompting."""
    if not items:
        return {}

    target_lang = normalize_locale(target_locale)
    source_lang = normalize_locale(source_locale)

    input_payload = {key: text for key, text in items}
    input_json = json.dumps(input_payload, ensure_ascii=False, indent=2)

    prompt = (
        f"You are a professional machine translation system specializing in software localization.\n"
        f"Translate the following UI strings from {source_lang} to {target_lang}.\n\n"
        f"CRITICAL RULES:\n"
        f"1. Preserve all placeholders exactly as-is (e.g. %(name)s, %(count)d, {{0}}, %s, %d).\n"
        f"2. Preserve all HTML markup and tags intact (e.g. <a href=\"...\">, <b>, </code>, <span>).\n"
        f"3. Return ONLY a valid JSON object where the keys match the input keys and values are the translations.\n"
        f"4. Do NOT output any preambles, explanations, or notes.\n\n"
        f"Input JSON:\n{input_json}"
    )

    try:
        response = engine.translate(
            text=prompt,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        translated_raw = response.translated_text if hasattr(response, "translated_text") else str(response)
        cleaned_json = clean_json_response(translated_raw)
        result_dict = json.loads(cleaned_json)
        if isinstance(result_dict, dict):
            return {str(k): str(v).strip() for k, v in result_dict.items()}
    except Exception as e:
        logger.warning(f"Batch JSON translation failed ({e}). Falling back to individual string translation...")

    # Fallback to translating string by string if batch fails
    fallback_results = {}
    for key, text in items:
        for attempt in range(1, 4):
            try:
                resp = engine.translate(
                    text=text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                fallback_results[key] = (resp.translated_text if hasattr(resp, "translated_text") else str(resp)).strip()
                break
            except Exception as err:
                if attempt == 3:
                    logger.error(f"Failed to translate item '{text[:30]}...': {err}")
                else:
                    time.sleep(1.5 * attempt)
    return fallback_results


def translate_po_file(
    po_path: Path,
    locale_code: str,
    engine: Any,
    batch_size: int = 20,
    overwrite: bool = False,
    dry_run: bool = False,
) -> int:
    """Translate empty messages in a .po file and save back to disk."""
    if not po_path.exists():
        logger.error(f"Catalog file not found: {po_path}")
        return 0

    with open(po_path, "rb") as f:
        catalog: Catalog = pofile.read_po(f, locale=locale_code)

    untranslated_entries: List[Tuple[Message, str, bool]] = []

    for msg in catalog:
        if not msg.id:
            continue

        if isinstance(msg.id, tuple):
            singular, plural = msg.id
            if overwrite or not msg.string or not any(msg.string):
                untranslated_entries.append((msg, singular, False))
                untranslated_entries.append((msg, plural, True))
        else:
            if overwrite or not msg.string or (isinstance(msg.string, str) and not msg.string.strip()):
                untranslated_entries.append((msg, msg.id, False))

    total_to_translate = len(untranslated_entries)
    logger.info(f"[{locale_code}] Found {total_to_translate} strings to translate in {po_path.name}")

    if total_to_translate == 0 or dry_run:
        return 0

    translated_count = 0
    for i in range(0, total_to_translate, batch_size):
        chunk = untranslated_entries[i : i + batch_size]
        batch_items = [(str(idx), text) for idx, (_, text, _) in enumerate(chunk)]

        logger.info(f"[{locale_code}] Translating batch {i + 1} to {min(i + batch_size, total_to_translate)} of {total_to_translate}...")
        translations = batch_translate_strings(engine, batch_items, locale_code)

        for idx, (msg_obj, text, is_plural) in enumerate(chunk):
            key = str(idx)
            if key in translations and translations[key]:
                trans_text = translations[key]
                if isinstance(msg_obj.id, tuple):
                    current_str = list(msg_obj.string) if isinstance(msg_obj.string, tuple) else ["", ""]
                    if len(current_str) < 2:
                        current_str = ["", ""]
                    if is_plural:
                        current_str[1] = trans_text
                    else:
                        current_str[0] = trans_text
                    msg_obj.string = tuple(current_str)
                else:
                    msg_obj.string = trans_text
                translated_count += 1

        with open(po_path, "wb") as f:
            pofile.write_po(f, catalog, width=76)

    logger.info(f"[{locale_code}] Successfully translated {translated_count} messages.")
    return translated_count


def compile_catalogs() -> bool:
    """Compile all .po catalogs to .mo files."""
    try:
        logger.info("Compiling .po translation catalogs to .mo files...")
        result = subprocess.run(
            ["pybabel", "compile", "-d", str(TRANSLATIONS_DIR)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("Catalog compilation succeeded.")
            return True
        else:
            logger.error(f"pybabel compile error: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Failed to run pybabel compile: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Translate UI catalog files (.po) using Gemma/LLM backend.")
    parser.add_argument(
        "--locales",
        nargs="+",
        default=["ta", "hi_IN", "sa", "te_IN"],
        help="List of locales to translate (e.g. ta hi_IN sa te_IN)",
    )
    parser.add_argument(
        "--engine",
        default="llm_gemma",
        help="Translation engine to use (default: llm_gemma; options: llm_gemma, bharatgen, google, openai, generic)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Number of strings to batch per translation request (default: 20)",
    )
    parser.add_argument(
        "--force",
        "-f",
        "--overwrite",
        dest="overwrite",
        action="store_true",
        help="Force re-translation of all strings (overwrites already translated entries)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report untranslated strings without translating",
    )
    parser.add_argument(
        "--api-url",
        default=None,
        help="Override backend translation / OCR API URL",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Override backend API key",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip compiling .po to .mo after translation",
    )

    args = parser.parse_args()

    from kalanjiyam.utils.translation_engine import TranslationEngineFactory

    engine_kwargs = {}
    if args.api_url:
        engine_kwargs["api_url"] = args.api_url
    if args.api_key:
        engine_kwargs["api_key"] = args.api_key

    logger.info(f"Initializing translation engine '{args.engine}'...")
    try:
        engine = TranslationEngineFactory.create(args.engine, **engine_kwargs)
    except Exception as e:
        logger.warning(f"Could not initialize translation engine '{args.engine}': {e}. Skipping auto-translation.")
        sys.exit(0)

    total_translated = 0
    try:
        for locale in args.locales:
            if locale == "en":
                logger.info("Skipping source locale 'en'.")
                continue

            po_file = TRANSLATIONS_DIR / locale / "LC_MESSAGES" / "messages.po"
            if not po_file.exists():
                logger.warning(f"No catalog found for locale '{locale}' at {po_file}. Skipping.")
                continue

            count = translate_po_file(
                po_path=po_file,
                locale_code=locale,
                engine=engine,
                batch_size=args.batch_size,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
            total_translated += count
            if not args.dry_run and not args.no_compile and count > 0:
                compile_catalogs()

    except KeyboardInterrupt:
        logger.info("\nTranslation interrupted by user (Ctrl+C). Saving completed translations...")
    finally:
        if not args.dry_run and not args.no_compile and total_translated > 0:
            compile_catalogs()

    logger.info(f"All done! Total translations processed: {total_translated}")


if __name__ == "__main__":
    main()
