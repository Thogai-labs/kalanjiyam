"""Streaming JSONL/PDF importer using the application's existing OCR schema."""

from __future__ import annotations

import json
import logging
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import boto3
import fitz
from slugify import slugify

from kalanjiyam import database as db
from kalanjiyam.models.batch import BatchItem, BatchJob
from kalanjiyam.models.group import Group, ProjectGroups
from kalanjiyam.utils.ocr_persist import apply_ocr_to_page
from kalanjiyam.utils.ocr_types import BLOCK_TYPES, OcrResponse
from kalanjiyam.utils.storage import get_storage, page_image_key, pdf_key

LOG = logging.getLogger(__name__)
ID_DELIMITERS = ("↳", "â†³", ",")
CATEGORY_TYPES = {
    "title": "heading", "section-header": "heading", "page-header": "running-header",
    "page-footer": "running-header", "text": "paragraph", "paragraph": "paragraph",
    "list-item": "paragraph", "table": "table", "figure": "figure", "picture": "figure",
    "image": "figure", "caption": "caption", "footnote": "footnote",
    "page-number": "page-number", "equation": "equation", "formula": "equation",
    "column-header": "column-header",
}


class ImportValidationError(ValueError):
    pass


@dataclass
class BookInput:
    pages: dict[int, Path] = field(default_factory=dict)
    source_objects: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


@dataclass
class ImportSummary:
    jsonl_files: int = 0
    books: int = 0
    pages: int = 0
    matched_pdfs: int = 0
    missing_pdfs: int = 0
    ambiguous_pdfs: int = 0
    malformed_records: int = 0
    duplicate_pages: int = 0
    invalid_books: int = 0
    skipped_books: int = 0
    importable_books: int = 0


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ImportValidationError("Expected an S3 URI (s3://bucket/prefix)")
    return parsed.netloc, parsed.path.lstrip("/")


def parse_record_id(value: object) -> tuple[str, int]:
    if not isinstance(value, str) or not value:
        raise ImportValidationError("missing id")
    for delimiter in ID_DELIMITERS:
        if delimiter in value:
            book_id, page = value.rsplit(delimiter, 1)
            book_id = book_id.strip()
            page = page.strip()
            if book_id and page.isdecimal() and int(page) > 0:
                return book_id, int(page)
    raise ImportValidationError("id must be <bookId>↳<positive 1-based pageNumber> or <bookId>,<pageNumber>")


def repair_json_string(raw: str | list | dict) -> list | dict:
    if isinstance(raw, (list, dict)):
        return raw
    if not isinstance(raw, str):
        raise ImportValidationError(f"Invalid generated_text type: {type(raw)}")
    
    # 1. Direct JSON parse
    try:
        return json.loads(raw)
    except Exception:
        pass

    # 2. Handle AST literal representation (e.g. single quotes)
    import ast
    try:
        val = ast.literal_eval(raw)
        if isinstance(val, (list, dict)):
            return val
    except Exception:
        pass

    # 3. Truncated list: find last complete object closing brace '}'
    last_brace = raw.rfind("}")
    if last_brace != -1:
        truncated_list = raw[:last_brace + 1].strip()
        if not truncated_list.endswith("]"):
            truncated_list += "]"
        try:
            return json.loads(truncated_list)
        except Exception:
            pass

    # 4. Attempt auto-closing unclosed quotes/brackets
    cleaned = raw.rstrip()
    if cleaned.count('"') % 2 != 0:
        cleaned += '"'
    cleaned += "}" * max(0, cleaned.count("{") - cleaned.count("}"))
    cleaned += "]" * max(0, cleaned.count("[") - cleaned.count("]"))
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    raise ImportValidationError("Cannot parse or repair generated_text JSON")


def normalize_blocks(raw_blocks: object, book_id: str, page_number: int) -> list[dict]:
    if not isinstance(raw_blocks, list):
        raise ImportValidationError("generated_text must decode to a list")
    result = []
    for order, raw in enumerate(raw_blocks, 1):
        if not isinstance(raw, dict):
            raise ImportValidationError("OCR block is not an object")
        bbox, text = raw.get("bbox"), raw.get("text", "")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(n, (int, float)) for n in bbox):
            raise ImportValidationError("OCR block has invalid bbox")
        if not isinstance(text, str):
            raise ImportValidationError("OCR block text is not a string")
        category = str(raw.get("category", "")).strip().lower()
        block_type = CATEGORY_TYPES.get(category, "paragraph")
        result.append({"id": f"import-{book_id}-page-{page_number}-block-{order}",
                       "type": block_type if block_type in BLOCK_TYPES else "paragraph",
                       "bbox": [int(n) for n in bbox], "content": text,
                       "reading_order": order, "children": []})
    return result


def parse_jsonl_record(line: str | bytes) -> tuple[str, int, list[dict]]:
    try:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ImportValidationError("JSONL record is not a JSON object")
        book_id, page_number = parse_record_id(record.get("id"))
        raw_gen = record.get("generated_text")
        if raw_gen is None:
            raise ImportValidationError("missing generated_text field")
        generated = repair_json_string(raw_gen)
    except (KeyError, TypeError, json.JSONDecodeError, ImportValidationError) as exc:
        raise ImportValidationError(f"malformed JSONL record: {exc}") from exc
    return book_id, page_number, normalize_blocks(generated, book_id, page_number)


def _is_s3_uri(uri: str) -> bool:
    return str(uri).startswith("s3://")


def _list_objects(client, uri: str, suffix: str) -> list[str]:
    if _is_s3_uri(uri):
        bucket, prefix = parse_s3_uri(uri)
        found = []
        for response in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in response.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                key_lower = key.lower()
                if suffix == ".pdf":
                    if key_lower.endswith(".pdf") or Path(key).suffix == "":
                        found.append(f"s3://{bucket}/{key}")
                else:
                    if key_lower.endswith(suffix):
                        found.append(f"s3://{bucket}/{key}")
        return sorted(found)
    else:
        p = Path(uri)
        if not p.exists():
            return []
        if p.is_file():
            return [str(p)]
        found = []
        for f in p.rglob("*"):
            if not f.is_file():
                continue
            if suffix == ".pdf":
                if f.name.lower().endswith(".pdf") or f.suffix == "":
                    found.append(str(f))
            else:
                if f.name.lower().endswith(suffix):
                    found.append(str(f))
        return sorted(found)


def _read_lines(source: str, client=None):
    if _is_s3_uri(source):
        bucket, key = parse_s3_uri(source)
        return client.get_object(Bucket=bucket, Key=key)["Body"].iter_lines()
    else:
        with Path(source).open("rb") as f:
            return f.read().splitlines()


def _fetch_pdf(pdf_path: str, target_local: Path, client=None):
    if _is_s3_uri(pdf_path):
        bucket, key = parse_s3_uri(pdf_path)
        client.download_file(bucket, key, str(target_local))
    else:
        import shutil
        shutil.copyfile(pdf_path, target_local)


def _parts(uri: str) -> tuple[str, str]:
    return parse_s3_uri(uri)


def _pdf_index(client, uri: str) -> dict[str, list[str]]:
    output = defaultdict(list)
    for item in _list_objects(client, uri, ".pdf"):
        stem = Path(urlparse(item).path if _is_s3_uri(item) else item).stem
        if item not in output[stem]:
            output[stem].append(item)
        stem_lower = stem.strip().lower()
        if item not in output[stem_lower]:
            output[stem_lower].append(item)
        stem_slug = slugify(stem)
        if stem_slug and item not in output[stem_slug]:
            output[stem_slug].append(item)
    return output


def _create_project(session, book_id: str, count: int, org_slug: str):
    project = session.query(db.Project).filter_by(source_book_id=book_id).first()
    if project:
        return project
    base = slugify(f"import-{book_id}") or "import-book"
    slug, number = base, 2
    while session.query(db.Project).filter_by(slug=slug).first():
        slug, number = f"{base}-{number}", number + 1
    board = db.Board(title=f"{slug} discussion board")
    session.add(board); session.flush()
    project = db.Project(slug=slug, source_book_id=book_id, display_title=book_id, board_id=board.id)
    session.add(project); session.flush()
    group = session.query(Group).filter_by(slug=org_slug).one()
    session.add(ProjectGroups(group_id=group.id, project_id=project.id))
    status = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
    for page_number in range(1, count + 1):
        session.add(db.Page(project_id=project.id, slug=str(page_number), order=page_number, status_id=status.id))
    session.flush()
    return project


def _persist_ocr(session, page, blocks: list[dict], width: int, height: int):
    response = OcrResponse(text_content="\n\n".join(b["content"] for b in blocks), bounding_boxes=[],
                           blocks=blocks, content_format="blocks", page_width=width, page_height=height,
                           pipeline="jsonl_import", source_type="pdf")
    document = apply_ocr_to_page(page, response, "jsonl_import")
    track = session.query(db.PageVersion).filter_by(page_id=page.id, version_key="ocr:jsonl_import").first()
    if track and session.query(db.Revision).filter_by(page_version_id=track.id).first():
        return
    if not track:
        track = db.PageVersion(page_id=page.id, version_key="ocr:jsonl_import", version=1, updated_at=datetime.utcnow())
        session.add(track); session.flush()
    status = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
    session.add(db.Revision(project_id=page.project_id, page_id=page.id, page_version_id=track.id,
                status_id=status.id, summary="Imported JSONL OCR", content=document.to_plain_text(),
                document=document.to_dict(), content_format=document.content_format))


def run_import(session, *, jsonl_uri: str, pdf_uri: str, org_slug: str,
               dry_run: bool = False, allow_duplicate: bool = False, client=None) -> ImportSummary:
    """Run a bounded, streaming import. JSONL page numbers are 1-based."""
    if _is_s3_uri(jsonl_uri) or _is_s3_uri(pdf_uri):
        client = client or boto3.client("s3")
    summary = ImportSummary()
    with tempfile.TemporaryDirectory(prefix="kalanjiyam-jsonl-") as temporary:
        root = Path(temporary)
        books: dict[str, BookInput] = defaultdict(BookInput)
        for source in _list_objects(client, jsonl_uri, ".jsonl"):
            summary.jsonl_files += 1
            for line_no, line in enumerate(_read_lines(source, client), 1):
                if not line.strip():
                    continue
                try:
                    book_id, page_no, blocks = parse_jsonl_record(line)
                except ImportValidationError as exc:
                    summary.malformed_records += 1
                    LOG.warning("JSONL import malformed record object=%s line=%s error=%s", source, line_no, exc)
                    continue
                book = books[book_id]
                book.source_objects.add(source)
                if page_no in book.pages:
                    book.errors.append(f"duplicate page {page_no}")
                    summary.duplicate_pages += 1
                    continue
                page_file = root / f"page-{len(book.pages)}-{page_no}.json"
                # Include a book directory to avoid same page names across books.
                page_file = root / slugify(book_id) / page_file.name
                page_file.parent.mkdir(exist_ok=True, parents=True)
                page_file.write_text(json.dumps(blocks), encoding="utf-8")
                book.pages[page_no] = page_file
                summary.pages += 1
        if not summary.jsonl_files:
            raise ImportValidationError("no JSONL files discovered")
        summary.books = len(books)

        if not allow_duplicate and org_slug:
            group = session.query(Group).filter_by(slug=org_slug).first()
            if group:
                existing_projects = (
                    session.query(db.Project)
                    .join(ProjectGroups, db.Project.id == ProjectGroups.project_id)
                    .filter(ProjectGroups.group_id == group.id)
                    .all()
                )
                existing_ids = set()
                for project in existing_projects:
                    if project.source_book_id:
                        existing_ids.add(project.source_book_id.strip().lower())
                    if project.display_title:
                        existing_ids.add(project.display_title.strip().lower())
                    if project.slug:
                        existing_ids.add(project.slug.strip().lower())

                books_to_process = {}
                for book_id, book in books.items():
                    b_id_lower = book_id.strip().lower()
                    b_slug_lower = slugify(book_id).lower()
                    b_import_slug_lower = slugify(f"import-{book_id}").lower()
                    if (
                        b_id_lower in existing_ids
                        or b_slug_lower in existing_ids
                        or b_import_slug_lower in existing_ids
                    ):
                        summary.skipped_books += 1
                        LOG.info("Skipping existing book '%s' in organization '%s'", book_id, org_slug)
                    else:
                        books_to_process[book_id] = book
                books = books_to_process

        print(f"Discovered {summary.books} unique book(s) in JSONL ({summary.skipped_books} existing duplicate(s) skipped). Validating PDFs...", flush=True)

        pdfs = _pdf_index(client, pdf_uri)
        pdf_matches_by_book = {}
        for book_id, book in books.items():
            matches = pdfs.get(book_id) or pdfs.get(book_id.strip().lower()) or pdfs.get(slugify(book_id)) or []
            matches = list(dict.fromkeys(matches))
            if len(matches) > 1:
                # Prefer top-level PDF if uniquely at shallowest directory depth
                by_depth = sorted(matches, key=lambda p: len(Path(p).parts))
                if len(Path(by_depth[0]).parts) < len(Path(by_depth[1]).parts):
                    matches = [by_depth[0]]
            if len(matches) != 1:
                if not matches:
                    book.errors.append("missing PDF")
                    summary.missing_pdfs += 1
                else:
                    book.errors.append(f"ambiguous PDF matches: {matches}")
                    summary.ambiguous_pdfs += 1
                    LOG.warning("Ambiguous PDF matches for book '%s': %s", book_id, matches)
                continue
            pdf_matches_by_book[book_id] = matches[0]
            summary.matched_pdfs += 1
            local_pdf = root / f"validate-{slugify(book_id)}.pdf"
            document = None
            try:
                _fetch_pdf(matches[0], local_pdf, client)
                document = fitz.open(local_pdf)
                if document.needs_pass or document.page_count == 0:
                    raise ImportValidationError("encrypted or empty PDF")
                if set(book.pages) != set(range(1, document.page_count + 1)):
                    raise ImportValidationError("JSONL pages must exactly match 1..PDF page count")
            except Exception as exc:
                book.errors.append(str(exc))
            finally:
                if document:
                    document.close()
                if local_pdf.exists():
                    try:
                        local_pdf.unlink()
                    except Exception:
                        pass
        summary.invalid_books = sum(bool(book.errors) for book in books.values())
        summary.importable_books = len(books) - summary.invalid_books
        print(f"Validation finished: {summary.importable_books} importable book(s), {summary.invalid_books} invalid/missing PDF(s).", flush=True)

        if dry_run:
            return summary

        job = BatchJob(target_uri=jsonl_uri, jsonl_uri=jsonl_uri, pdf_uri=pdf_uri,
                       job_type="JSONL_IMPORT", status="IN_PROGRESS")
        session.add(job); session.flush()
        storage = get_storage()
        completed = False
        total_to_process = len(books)
        print(f"\nStarting import of {summary.importable_books} book(s) into database and storage...", flush=True)

        for idx, (book_id, book) in enumerate(books.items(), 1):
            file_path = pdf_matches_by_book.get(book_id, "")
            item = BatchItem(job_id=job.id, file_path=file_path,
                             mime_type="application/pdf", source_book_id=book_id,
                             source_jsonl_uri=",".join(sorted(book.source_objects)),
                             total_pages=len(book.pages), status="VALIDATING")
            session.add(item); session.flush()
            if book.errors:
                print(f"[{idx}/{total_to_process}] Skipping invalid book '{book_id}': {'; '.join(book.errors)}", flush=True)
                item.status, item.error_message, item.completed_at = "FAILED", "; ".join(book.errors), datetime.utcnow()
                session.commit()
                continue
            try:
                print(f"[{idx}/{total_to_process}] Importing '{book_id}' ({len(book.pages)} pages)...", flush=True)
                project = _create_project(session, book_id, len(book.pages), org_slug)
                item.project_id, item.status = project.id, "PROCESSING"
                session.flush()
                local_pdf = root / f"process-{slugify(book_id)}.pdf"
                _fetch_pdf(item.file_path, local_pdf, client)
                storage.save(pdf_key(project.slug), local_pdf)
                with fitz.open(local_pdf) as document:
                    for page_no in range(1, document.page_count + 1):
                        pixmap = document[page_no - 1].get_pixmap(dpi=200)
                        image = root / f"image-{slugify(book_id)}-{page_no}.jpg"
                        pixmap.pil_save(image, optimize=True)
                        storage.save(page_image_key(project.slug, str(page_no)), image)
                        if image.exists():
                            try:
                                image.unlink()
                            except Exception:
                                pass
                        page = session.query(db.Page).filter_by(project_id=project.id, order=page_no).one()
                        blocks = json.loads(book.pages[page_no].read_text(encoding="utf-8"))
                        _persist_ocr(session, page, blocks, pixmap.width, pixmap.height)
                        session.flush()
                if local_pdf.exists():
                    try:
                        local_pdf.unlink()
                    except Exception:
                        pass
                item.status, item.completed_at = "COMPLETED", datetime.utcnow()
                completed = True
                session.commit()
                print(f"[{idx}/{total_to_process}] ✓ Completed '{book_id}' ({len(book.pages)} pages)", flush=True)
            except Exception as exc:
                print(f"[{idx}/{total_to_process}] ✗ Failed '{book_id}': {exc}", flush=True)
                LOG.exception("JSONL import failed book=%s", book_id)
                session.rollback()
                try:
                    item_ref = session.query(BatchItem).get(item.id)
                    if item_ref:
                        item_ref.status = "FAILED"
                        item_ref.error_message = str(exc)
                        item_ref.completed_at = datetime.utcnow()
                        session.commit()
                except Exception as rollback_exc:
                    LOG.exception("Failed to update BatchItem status after rollback book=%s", book_id)
                    session.rollback()
        job.status, job.completed_at = (
            ("COMPLETED" if (completed or (summary.skipped_books > 0 and summary.invalid_books == 0)) else "FAILED"),
            datetime.utcnow(),
        )
        session.commit()
    return summary
