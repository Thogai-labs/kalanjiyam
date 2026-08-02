"""Streaming JSONL/PDF importer using the application's existing OCR schema."""

from __future__ import annotations

import json
import logging
import re
import shutil
import hashlib
import sqlite3
import tempfile
import time
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
ID_DELIMITERS = ("↳", "â†³")
CATEGORY_TYPES = {
    "title": "heading", "section-header": "heading", "page-header": "running-header",
    "page-footer": "running-header", "text": "paragraph", "paragraph": "paragraph",
    "list-item": "paragraph", "table": "table", "figure": "figure", "picture": "figure",
    "image": "figure", "caption": "caption", "footnote": "footnote",
    "page-number": "page-number", "equation": "equation", "formula": "equation",
    "column-header": "column-header",
}
MISSING_TEXT_QUOTE = re.compile(r'("text"\s*:\s*"(?:\\.|[^"\\])*)}(?=\s*,\s*\{)')


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
    malformed_records: int = 0
    duplicate_pages: int = 0
    invalid_books: int = 0
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
            if book_id and page.isdecimal() and int(page) > 0:
                return book_id, int(page)
    raise ImportValidationError("id must be <bookId>↳<positive 1-based pageNumber>")


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


def _decode_generated_text(value: object) -> tuple[object, int]:
    """Decode OCR JSON, repairing only a known missing text-quote defect.

    Some partner exports omit the closing double quote of a text value just
    before an OCR block ends (``"text": "...'} ``).  The repair is deliberately
    narrow and is attempted only after strict JSON decoding has failed.
    """
    if not isinstance(value, str):
        raise ImportValidationError("generated_text must be a JSON-encoded string")
    try:
        return json.loads(value), 0
    except json.JSONDecodeError as original_error:
        repaired = value
        repairs = 0
        while repairs < 100:
            repaired, count = MISSING_TEXT_QUOTE.subn(r'\1"}', repaired)
            repairs += count
            if not count:
                break
        if not repairs:
            raise ImportValidationError(f"malformed generated_text: {original_error}") from original_error
        try:
            return json.loads(repaired), repairs
        except json.JSONDecodeError as repair_error:
            raise ImportValidationError(f"malformed generated_text after safe repair: {repair_error}") from repair_error


def parse_jsonl_record(line: str | bytes) -> tuple[str, int, list[dict]]:
    try:
        record = json.loads(line)
        book_id, page_number = parse_record_id(record.get("id"))
        generated, repairs = _decode_generated_text(record["generated_text"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ImportValidationError(f"malformed JSONL record: {exc}") from exc
    if repairs:
        LOG.warning("Repaired %s missing generated_text quote(s) book=%s page=%s", repairs, book_id, page_number)
    return book_id, page_number, normalize_blocks(generated, book_id, page_number)


def _is_s3_uri(value: str) -> bool:
    return urlparse(value).scheme == "s3"


def _list_objects(client, location: str, suffix: str) -> list[str]:
    """List matching S3 objects or files below a local directory."""
    if not _is_s3_uri(location):
        directory = Path(location)
        if not directory.is_dir():
            raise ImportValidationError(f"Local directory does not exist: {directory}")
        return sorted(str(path) for path in directory.rglob("*")
                      if path.is_file() and path.suffix.lower() == suffix)
    bucket, prefix = parse_s3_uri(location)
    found = []
    for response in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        found.extend(f"s3://{bucket}/{obj['Key']}" for obj in response.get("Contents", [])
                     if obj["Key"].lower().endswith(suffix) and not obj["Key"].endswith("/"))
    return sorted(found)


def _parts(uri: str) -> tuple[str, str]:
    return parse_s3_uri(uri)


def _iter_lines(client, source: str):
    if _is_s3_uri(source):
        bucket, key = _parts(source)
        yield from client.get_object(Bucket=bucket, Key=key)["Body"].iter_lines()
        return
    with Path(source).open("rb") as stream:
        yield from stream


def _copy_pdf_to_local(client, source: str, destination: Path) -> None:
    if _is_s3_uri(source):
        bucket, key = _parts(source)
        client.download_file(bucket, key, str(destination))
        return
    shutil.copyfile(source, destination)


def _retry(operation, *, attempts: int = 3):
    """Retry bounded transient filesystem/S3 operations."""
    for attempt in range(attempts):
        try:
            return operation()
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(2 ** attempt)


def _safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pdf_index(client, uri: str) -> dict[str, list[str]]:
    output = defaultdict(list)
    for item in _list_objects(client, uri, ".pdf"):
        output[Path(urlparse(item).path if _is_s3_uri(item) else item).stem].append(item)
    return output


class DiskIndex:
    """SQLite-backed page index; keeps large JSONL batches out of RAM."""

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            CREATE TABLE pages (book_id TEXT, page_number INTEGER, path TEXT,
                                source TEXT, PRIMARY KEY (book_id, page_number));
            CREATE TABLE errors (book_id TEXT, message TEXT);
            CREATE TABLE books (book_id TEXT PRIMARY KEY);
        """)

    def add_page(self, book_id: str, page_number: int, path: Path, source: str) -> bool:
        self.connection.execute("INSERT OR IGNORE INTO books VALUES (?)", (book_id,))
        try:
            self.connection.execute("INSERT INTO pages VALUES (?, ?, ?, ?)",
                                    (book_id, page_number, str(path), source))
            return True
        except sqlite3.IntegrityError:
            self.error(book_id, f"duplicate page {page_number}")
            return False

    def error(self, book_id: str, message: str) -> None:
        self.connection.execute("INSERT OR IGNORE INTO books VALUES (?)", (book_id,))
        self.connection.execute("INSERT INTO errors VALUES (?, ?)", (book_id, message))

    def book_ids(self):
        return (row[0] for row in self.connection.execute("SELECT book_id FROM books ORDER BY book_id"))

    def book(self, book_id: str) -> BookInput:
        book = BookInput()
        for number, path, source in self.connection.execute(
            "SELECT page_number, path, source FROM pages WHERE book_id=? ORDER BY page_number", (book_id,)
        ):
            book.pages[number] = Path(path)
            book.source_objects.add(source)
        book.errors = [row[0] for row in self.connection.execute(
            "SELECT message FROM errors WHERE book_id=?", (book_id,)
        )]
        return book

    def count_books(self) -> int:
        return self.connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()


def _create_project(session, book_id: str, count: int, org_slug: str):
    project = session.query(db.Project).filter_by(source_book_id=book_id).first()
    if project:
        status = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
        existing_orders = {
            row[0] for row in session.query(db.Page.order).filter_by(project_id=project.id)
        }
        for page_number in range(1, count + 1):
            if page_number not in existing_orders:
                session.add(db.Page(
                    project_id=project.id,
                    slug=str(page_number),
                    order=page_number,
                    status_id=status.id,
                ))
        session.flush()
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
               dry_run: bool = False, client=None) -> ImportSummary:
    """Run a bounded import from S3 prefixes or local directories.

    JSONL page numbers are 1-based.  Local and S3 sources can be mixed.
    """
    if client is None and (_is_s3_uri(jsonl_uri) or _is_s3_uri(pdf_uri)):
        client = boto3.client("s3")
    summary = ImportSummary()
    with tempfile.TemporaryDirectory(prefix="kalanjiyam-jsonl-") as temporary:
        root = Path(temporary)
        index = DiskIndex(root / "manifest.sqlite")
        for source in _list_objects(client, jsonl_uri, ".jsonl"):
            summary.jsonl_files += 1
            for line_no, line in enumerate(_iter_lines(client, source), 1):
                if not line.strip():
                    continue
                try:
                    book_id, page_no, blocks = parse_jsonl_record(line)
                except ImportValidationError as exc:
                    summary.malformed_records += 1
                    LOG.warning("JSONL import malformed record object=%s line=%s error=%s", source, line_no, exc)
                    continue
                page_file = root / "pages" / _safe_name(book_id) / f"{page_no}.json"
                page_file.parent.mkdir(parents=True, exist_ok=True)
                page_file.write_text(json.dumps(blocks), encoding="utf-8")
                if not index.add_page(book_id, page_no, page_file, source):
                    summary.duplicate_pages += 1
                    page_file.unlink(missing_ok=True)
                    continue
                summary.pages += 1
        if not summary.jsonl_files:
            raise ImportValidationError("no JSONL files discovered")
        index.connection.commit()
        summary.books = index.count_books()
        book_ids = list(index.book_ids())
        pdfs = _pdf_index(client, pdf_uri)
        invalid_book_ids: set[str] = set()
        validation_errors: dict[str, str] = {}
        for book_id in book_ids:
            book = index.book(book_id)
            matches = pdfs.get(book_id, [])
            if len(matches) != 1:
                book.errors.append("missing PDF" if not matches else "ambiguous PDF matches")
                if not matches:
                    summary.missing_pdfs += 1
                invalid_book_ids.add(book_id)
                validation_errors[book_id] = "; ".join(book.errors)
                continue
            summary.matched_pdfs += 1
            local_pdf = root / f"validate-{_safe_name(book_id)}.pdf"
            document = None
            try:
                _retry(lambda: _copy_pdf_to_local(client, matches[0], local_pdf))
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
            if book.errors:
                invalid_book_ids.add(book_id)
                validation_errors[book_id] = "; ".join(book.errors)
        summary.invalid_books = len(invalid_book_ids)
        summary.importable_books = summary.books - summary.invalid_books
        if dry_run:
            index.close()
            return summary

        job = BatchJob(target_uri=jsonl_uri, jsonl_uri=jsonl_uri, pdf_uri=pdf_uri,
                       job_type="JSONL_IMPORT", status="IN_PROGRESS")
        session.add(job); session.flush()
        storage = get_storage()
        completed = False
        for book_id in book_ids:
            book = index.book(book_id)
            item = BatchItem(job_id=job.id, file_path=(pdfs.get(book_id) or [""])[0],
                             mime_type="application/pdf", source_book_id=book_id,
                             source_jsonl_uri=jsonl_uri,
                             total_pages=len(book.pages), status="VALIDATING")
            session.add(item); session.flush()
            if book_id in invalid_book_ids:
                item.status = "FAILED"
                item.error_message = validation_errors.get(book_id, "; ".join(book.errors))
                item.completed_at = datetime.utcnow()
                session.commit()
                continue
            try:
                project = _create_project(session, book_id, len(book.pages), org_slug)
                item.project_id, item.status = project.id, "PROCESSING"
                session.flush()
                local_pdf = root / f"process-{_safe_name(book_id)}.pdf"
                _retry(lambda: _copy_pdf_to_local(client, item.file_path, local_pdf))
                _retry(lambda: storage.save(pdf_key(project.slug), local_pdf))
                pages_by_order = {
                    page.order: page
                    for page in session.query(db.Page).filter_by(project_id=project.id)
                }
                with fitz.open(local_pdf) as document:
                    for page_no in range(1, document.page_count + 1):
                        pixmap = document[page_no - 1].get_pixmap(dpi=200)
                        image = root / f"image-{_safe_name(book_id)}-{page_no}.jpg"
                        pixmap.pil_save(image, optimize=True)
                        _retry(lambda: storage.save(page_image_key(project.slug, str(page_no)), image))
                        page = pages_by_order.get(page_no)
                        if page is None:
                            raise ImportValidationError(
                                f"project {project.id} is missing page row {page_no} after reconciliation"
                            )
                        blocks = json.loads(book.pages[page_no].read_text(encoding="utf-8"))
                        _persist_ocr(session, page, blocks, pixmap.width, pixmap.height)
                        session.flush()
                item.status, item.completed_at = "COMPLETED", datetime.utcnow()
                completed = True
            except Exception as exc:
                LOG.exception("JSONL import failed book=%s", book_id)
                item.status, item.error_message, item.completed_at = "FAILED", str(exc), datetime.utcnow()
            session.commit()
        job.status, job.completed_at = ("COMPLETED" if completed else "FAILED"), datetime.utcnow()
        session.commit()
        index.close()
    return summary
