#!/usr/bin/env python3
"""Fix corrupted image URLs (containing $ extracted filenames) in database revisions and translations."""

import argparse
import os
import re
import json
import sys
from typing import Any
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv()
abs_upload = str((project_root / "uploads").resolve())
os.environ["FLASK_UPLOAD_FOLDER"] = abs_upload
os.environ["UPLOAD_FOLDER"] = abs_upload

db_uri = os.environ.get("SQLALCHEMY_DATABASE_URI", "")
if "@kalanjiyam-db" in db_uri:
    import socket
    try:
        socket.gethostbyname("kalanjiyam-db")
    except socket.gaierror:
        os.environ["SQLALCHEMY_DATABASE_URI"] = db_uri.replace("@kalanjiyam-db", "@localhost")

import kalanjiyam
from kalanjiyam import queries as q
from config import create_config_only_app


def sanitize_text(text: str) -> tuple[str, bool]:
    if not text:
        return text, False

    new_text = text
    # 1. Strip $ around extracted_ filenames
    new_text = re.sub(r'\$+(extracted_[a-zA-Z0-9_\-]+\.(?:png|jpg|jpeg|gif|svg))\$+', r'\1', new_text)

    # 2. Strip $ from /images/$extracted_... or /uploads/.../$extracted_...
    new_text = re.sub(r'(/images/|/uploads/[^/]+/)\$+(extracted_[a-zA-Z0-9_\-]+)\.(png|jpg|jpeg|gif|svg)\$+', r'\1\2.\3', new_text)

    # 3. Strip <dnt> wrapping extracted_ filenames
    new_text = re.sub(r'(?i)<dnt>([^<]*extracted_[a-zA-Z0-9_\-]+\.(?:png|jpg|jpeg|gif|svg)[^<]*)</dnt>', r'\1', new_text)

    # 4. Clean any remaining $ directly preceding extracted_ or directly following .png/$
    new_text = new_text.replace('$extracted_', 'extracted_').replace('.png$', '.png').replace('.jpg$', '.jpg').replace('.jpeg$', '.jpeg').replace('.svg$', '.svg')

    return new_text, new_text != text


def sanitize_document_dict(doc: Any) -> tuple[Any, bool]:
    if not doc:
        return doc, False

    doc_str = json.dumps(doc) if not isinstance(doc, str) else doc
    sanitized_str, modified = sanitize_text(doc_str)
    if modified:
        return (json.loads(sanitized_str) if not isinstance(doc, str) else sanitized_str), True
    return doc, False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        default="development",
        help="App environment (development, production, testing)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without modifying the database",
    )
    args = parser.parse_args()

    os.environ["UPLOAD_FOLDER"] = str((project_root / "uploads").resolve())
    try:
        try:
            from config import create_config_only_app
            app = create_config_only_app(args.env)
        except Exception:
            app = kalanjiyam.create_app(args.env)

        with app.app_context():
            session = q.get_session()
            engine = session.get_bind()
            print(f"Connected to DB URL: {engine.url}")

            from kalanjiyam.models.proofing import Page, Revision, Translation, PageVersion

            page_count = 0
            rev_count = 0
            trans_count = 0
            all_found_urls = set()

            def log_urls(source_name: str, item_id: Any, text_content: str):
                if not text_content:
                    return
                # Find any src=... or URLs with extracted_ or image extensions
                matches = re.findall(r'(?:src=["\']([^"\']+)["\']|([^\s"\'<>]*extracted_[^\s"\'<>]*))', str(text_content))
                for m1, m2 in matches:
                    url = m1 or m2
                    if url:
                        all_found_urls.add(url)
                        print(f"[{source_name} {item_id}] Found image URL: {url}")

            print("--- Scanning Database for Image URLs ---")

            # 1. Clean Pages
            pages = session.query(Page).all()
            for page in pages:
                if page.ocr_bounding_boxes:
                    log_urls("Page", page.slug, page.ocr_bounding_boxes)
                    nb, ch = sanitize_text(page.ocr_bounding_boxes)
                    if ch:
                        page.ocr_bounding_boxes = nb
                        page_count += 1
                        print(f"--> Repaired Page {page.slug}")

            # 2. Clean Revisions
            revisions = session.query(Revision).all()
            for rev in revisions:
                r_changed = False
                if rev.content:
                    log_urls("Revision content", rev.id, rev.content)
                    nc, ch = sanitize_text(rev.content)
                    if ch:
                        rev.content = nc
                        r_changed = True
                if rev.document:
                    log_urls("Revision document", rev.id, json.dumps(rev.document))
                    nd, ch = sanitize_document_dict(rev.document)
                    if ch:
                        rev.document = nd
                        r_changed = True
                if r_changed:
                    rev_count += 1
                    print(f"--> Repaired Revision {rev.id}")

            # 3. Clean Translations
            translations = session.query(Translation).all()
            for tr in translations:
                if tr.content:
                    log_urls("Translation", tr.id, tr.content)
                    nt, ch = sanitize_text(tr.content)
                    if ch:
                        tr.content = nt
                        trans_count += 1
                        print(f"--> Repaired Translation {tr.id}")

            print(f"\nTotal unique image URLs found in DB: {len(all_found_urls)}")
            for url in sorted(all_found_urls):
                print(f"  - {url}")

            if not args.dry_run:
                session.commit()
                print(f"\n[SUCCESS] Repaired {page_count} pages, {rev_count} revisions, and {trans_count} translations.")
            else:
                print(f"\n[DRY-RUN] Found {page_count} pages, {rev_count} revisions, and {trans_count} translations to repair.")
    except Exception as err:
        print(f"Database connection error: {err}")
        print("\nNote: Execute this script inside the web container:")
        print("  docker exec -it kalanjiyam-web python scripts/fix_corrupted_image_urls.py")


if __name__ == "__main__":
    main()
