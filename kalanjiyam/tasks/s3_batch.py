import logging
import os
import time
import tempfile
import mimetypes
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from slugify import slugify
from urllib.parse import urlparse

import boto3
import fitz
import httpx
from PIL import Image
from sqlalchemy import and_, or_, update

from kalanjiyam.tasks import app
from kalanjiyam.models.batch import BatchItem, BatchJob, BatchOcrChunk, BatchOcrPage
from kalanjiyam.models.group import Group, ProjectGroups
from kalanjiyam.utils.storage import get_storage, page_image_key
from kalanjiyam.utils.ocr_types import OcrResponse
from kalanjiyam.utils.ocr_persist import apply_ocr_to_page
from kalanjiyam.utils.revisions import add_revision
from kalanjiyam.enums import SitePageStatus
from kalanjiyam import database as db
from kalanjiyam import queries as q
from config import create_config_only_app

LOG = logging.getLogger(__name__)

BATCH_OCR_CHUNK_SIZE = 10
CHUNK_LEASE_TIMEOUT = timedelta(minutes=15)


def create_chunk_ranges(page_count: int, chunk_size: int = BATCH_OCR_CHUNK_SIZE) -> list[tuple[int, int]]:
    """Split page_count into contiguous ranges of chunk_size."""
    if page_count <= 0:
        return []
    ranges = []
    for start in range(1, page_count + 1, chunk_size):
        end = min(start + chunk_size - 1, page_count)
        ranges.append((start, end))
    return ranges


def _download_from_s3(s3_url: str, dest_path: str):
    parsed = urlparse(s3_url)
    bucket = parsed.netloc
    key = parsed.path.lstrip('/')
    s3_client = boto3.client('s3')
    s3_client.download_file(bucket, key, dest_path)


def _setup_project(session, project_name: str, num_pages: int, creator_id: int = None, org_slug: str = None) -> db.Project:
    """Create a project and its pages in the database."""
    slug = slugify(project_name)
    base_slug = slug
    counter = 1
    while session.query(db.Project).filter_by(slug=slug).first():
        slug = f"{base_slug}_{counter}"
        counter += 1

    board = db.Board(title=f"{slug} discussion board")
    session.add(board)
    session.flush()

    project = db.Project(
        slug=slug,
        display_title=project_name,
        creator_id=creator_id,
        board_id=board.id,
        is_publicly_viewable=False
    )
    session.add(project)
    session.flush()

    if org_slug:
        clean_org_slug = slugify(org_slug)
        group = session.query(Group).filter_by(slug=clean_org_slug).first()
        if group:
            project_group = ProjectGroups(group_id=group.id, project_id=project.id)
            session.add(project_group)
        else:
            LOG.warning(f"Organization '{org_slug}' not found. Project '{slug}' created without an organization.")

    unreviewed = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
    for n in range(1, num_pages + 1):
        session.add(
            db.Page(
                project_id=project.id,
                slug=str(n),
                order=n,
                status_id=unreviewed.id,
            )
        )
        
    return project


def _finalize_batch_item_status(session, batch_item_id: int):
    """Mark BatchItem terminal status if all chunks have finished."""
    item = session.query(BatchItem).get(batch_item_id)
    if not item:
        return

    chunks = session.query(BatchOcrChunk).filter_by(batch_item_id=batch_item_id).all()
    if not chunks:
        return

    # All chunks must be in COMPLETED or FAILED
    if not all(c.status in ('COMPLETED', 'FAILED') for c in chunks):
        return

    any_completed = any(c.status == 'COMPLETED' for c in chunks)
    all_failed = all(c.status == 'FAILED' for c in chunks)

    if all_failed:
        item.status = 'FAILED'
    elif any_completed:
        item.status = 'COMPLETED'
    else:
        item.status = 'FAILED'

    item.completed_at = datetime.utcnow()
    total_ms = sum(c.total_ocr_latency_ms for c in chunks if c.total_ocr_latency_ms)
    item.total_ocr_latency_ms = total_ms

    # Aggregate per-page metrics across completed pages
    pages = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item_id, status='COMPLETED').all()
    if pages:
        engines = [p.engine for p in pages if p.engine]
        item.engine = engines[0] if engines else None
        conf_list = [p.confidence for p in pages if p.confidence is not None]
        item.avg_confidence = (sum(conf_list) / len(conf_list)) if conf_list else None
        p05_list = [p.p05 for p in pages if p.p05 is not None]
        item.avg_p05 = (sum(p05_list) / len(p05_list)) if p05_list else None
        item.total_blocks = sum(p.blocks or 0 for p in pages)
        item.total_chars = sum(p.chars or 0 for p in pages)
        item.total_engine_latency_ms = sum(p.engine_latency_ms or 0 for p in pages)

    # Calculate total size of OCR data (revisions content & document JSON) and cropped images for this project
    if item.project_id:
        try:
            project = session.query(db.Project).get(item.project_id)
            if project and project.pages:
                page_ids = [p.id for p in project.pages]
                revs = session.query(db.Revision).filter(db.Revision.page_id.in_(page_ids)).all()
                from kalanjiyam.utils.document_storage import load_revision_document

                total_ocr_bytes = 0
                for rev in revs:
                    if rev.content:
                        total_ocr_bytes += len(rev.content.encode('utf-8'))
                    rev_doc = load_revision_document(rev)
                    if rev_doc:
                        total_ocr_bytes += len(json.dumps(rev_doc).encode('utf-8'))
                item.ocr_data_size_bytes = total_ocr_bytes

                # Calculate cropped element images size from storage
                storage = get_storage()
                cropped_bytes = 0
                for p in project.pages:
                    for rev in p.revisions:
                        rev_doc = load_revision_document(rev) or {}
                        for block in rev_doc.get("blocks", []):
                            blk_id = block.get("id")
                            if blk_id:
                                crop_key = f"{project.slug}/images/extracted_{blk_id}.png"
                                try:
                                    if storage.exists(crop_key):
                                        cropped_bytes += storage.size(crop_key)
                                except Exception:
                                    pass
                item.cropped_images_size_bytes = cropped_bytes
        except Exception as e:
            LOG.warning(f"Error calculating OCR data/cropped image sizes for item #{batch_item_id}: {e}")

    failed_pages = (
        session.query(BatchOcrPage.page_number)
        .filter(BatchOcrPage.batch_item_id == batch_item_id, BatchOcrPage.status == 'FAILED')
        .order_by(BatchOcrPage.page_number)
        .all()
    )
    failed_page_numbers = [p[0] for p in failed_pages]
    if failed_page_numbers:
        item.error_message = f"Completed with OCR warnings on page(s): {failed_page_numbers}"
    else:
        item.error_message = None

    session.commit()
    LOG.info(f"Finalized BatchItem #{batch_item_id} status: {item.status}")

    if item.job_id:
        _finalize_batch_job_status(session, item.job_id)


def _finalize_batch_job_status(session, job_id: int):
    """Mark BatchJob terminal status if all items have finished."""
    job = session.query(BatchJob).get(job_id)
    if not job or job.status in ('COMPLETED', 'FAILED'):
        return

    items = session.query(BatchItem).filter_by(job_id=job_id).all()
    if not items:
        return

    if all(i.status in ('COMPLETED', 'FAILED') for i in items):
        job.status = 'COMPLETED' if any(i.status == 'COMPLETED' for i in items) else 'FAILED'
        job.completed_at = datetime.utcnow()
        session.commit()
        LOG.info(f"Finalized BatchJob #{job_id} status: {job.status}")


def _chunk_is_cancelled(session, chunk_id: int) -> bool:
    """Return true when cancellation or another terminal state won the race."""
    chunk = session.query(BatchOcrChunk).get(chunk_id)
    if not chunk or chunk.status != 'IN_PROGRESS':
        return True
    item = session.query(BatchItem).get(chunk.batch_item_id)
    job = session.query(BatchJob).get(item.job_id) if item and item.job_id else None
    return bool(job and job.status == 'FAILED' and job.error_message == 'Cancelled by user')


from flask import has_app_context, current_app
import contextlib

def _get_app_context():
    if has_app_context():
        return contextlib.nullcontext()
    app_env = os.environ.get("KALANJIYAM_DEPLOYMENT_ENV", os.environ.get("FLASK_ENV", "development"))
    flask_app = create_config_only_app(app_env)
    return flask_app.app_context()


@app.task(bind=True, max_retries=3)
def process_s3_batch_item(self, batch_item_id: int, org_slug: str = None, language: str = "eng", **kwargs):
    """Preparation task: downloads input, sets up project, renders images, creates chunks & dispatches chunk tasks."""
    with _get_app_context():
        session = q.get_session()
        item = session.query(BatchItem).get(batch_item_id)
        if not item:
            LOG.error(f"BatchItem {batch_item_id} not found.")
            return

        if item.status in ('COMPLETED', 'FAILED'):
            LOG.warning(f"BatchItem {batch_item_id} is already {item.status}. Skipping preparation.")
            return

        item.status = 'IN_PROGRESS'
        session.commit()

        storage = get_storage()
        extract_start = time.time()

        try:
            # Check if project and chunks already exist (Preparation Idempotency)
            existing_chunks = session.query(BatchOcrChunk).filter_by(batch_item_id=batch_item_id).all()
            if item.project_id and existing_chunks:
                project = session.query(db.Project).get(item.project_id)
                page_count = len(project.pages) if project else 0
                LOG.info(f"Item #{batch_item_id} already has project and {len(existing_chunks)} chunks. Skipping re-rendering.")
            else:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_dir_path = Path(tmp_dir)
                    
                    if item.mime_type == 'application/pdf':
                        if item.file_path.startswith("s3://"):
                            local_path = tmp_dir_path / "source.pdf"
                            _download_from_s3(item.file_path, str(local_path))
                        else:
                            local_path = Path(item.file_path[7:])
                            
                        source_size = local_path.stat().st_size if local_path.is_file() else 0
                        item.source_size_bytes = source_size
                        item.status = 'DOWNLOADED'
                        session.commit()

                        project_name = Path(item.file_path).stem
                        doc = fitz.open(local_path)
                        page_count = doc.page_count
                        
                        if item.project_id:
                            project = session.query(db.Project).get(item.project_id)
                        else:
                            project = _setup_project(session, project_name, page_count, org_slug=org_slug)
                            item.project_id = project.id
                        session.commit()
                        
                        extracted_img_bytes = 0
                        for page in doc:
                            n = page.number + 1
                            pix = page.get_pixmap(dpi=200)
                            tmp_img_path = tmp_dir_path / f"{n}.jpg"
                            pix.pil_save(tmp_img_path, optimize=True)
                            
                            extracted_img_bytes += tmp_img_path.stat().st_size
                            storage.save(page_image_key(project.slug, str(n)), tmp_img_path)
                            tmp_img_path.unlink()
                        item.extracted_images_size_bytes = extracted_img_bytes
                    else:
                        image_paths = []
                        project_name = Path(item.file_path.rstrip('/')).name or "image_project"
                        
                        if item.file_path.startswith("s3://"):
                            parsed = urlparse(item.file_path)
                            bucket = parsed.netloc
                            prefix = parsed.path.lstrip('/')
                            s3_client = boto3.client('s3')
                            
                            paginator = s3_client.get_paginator('list_objects_v2')
                            for p in paginator.paginate(Bucket=bucket, Prefix=prefix):
                                for obj in p.get('Contents', []):
                                    key = obj['Key']
                                    if key.endswith('/'): continue
                                    mime, _ = mimetypes.guess_type(key)
                                    if mime and mime.startswith('image/'):
                                        local_img = tmp_dir_path / Path(key).name
                                        s3_client.download_file(bucket, key, str(local_img))
                                        image_paths.append(local_img)
                        else:
                            local_dir = Path(item.file_path[7:])
                            for f in sorted(local_dir.iterdir()):
                                if f.is_file():
                                    mime, _ = mimetypes.guess_type(f.name)
                                    if mime and mime.startswith('image/'):
                                        image_paths.append(f)
                                        
                        image_paths.sort(key=lambda p: p.name)
                        page_count = len(image_paths)
                        
                        if item.project_id:
                            project = session.query(db.Project).get(item.project_id)
                        else:
                            project = _setup_project(session, project_name, page_count, org_slug=org_slug)
                            item.project_id = project.id
                        session.commit()
                        
                        total_bytes = 0
                        extracted_img_bytes = 0
                        for n, img_path in enumerate(image_paths, start=1):
                            total_bytes += img_path.stat().st_size
                            tmp_jpg = tmp_dir_path / f"{n}.jpg"
                            with Image.open(img_path) as im:
                                im.convert("RGB").save(tmp_jpg, "JPEG", quality=90, optimize=True)
                            
                            extracted_img_bytes += tmp_jpg.stat().st_size
                            storage.save(page_image_key(project.slug, str(n)), tmp_jpg)
                            if tmp_jpg.exists() and tmp_jpg != img_path:
                                tmp_jpg.unlink()
                                
                        item.source_size_bytes = total_bytes
                        item.extracted_images_size_bytes = extracted_img_bytes
                        item.status = 'DOWNLOADED'
                        session.commit()
                    
                    extract_time = (time.time() - extract_start) * 1000
                    item.extraction_latency_ms = extract_time

            item.status = 'IMAGES_EXTRACTED'
            session.commit()

            # Create or fetch chunks for ranges
            ranges = create_chunk_ranges(page_count, BATCH_OCR_CHUNK_SIZE)
            dispatched_chunks = []
            for start_p, end_p in ranges:
                chunk = session.query(BatchOcrChunk).filter_by(
                    batch_item_id=batch_item_id, start_page=start_p, end_page=end_p
                ).first()
                if not chunk:
                    chunk = BatchOcrChunk(
                        batch_item_id=batch_item_id,
                        start_page=start_p,
                        end_page=end_p,
                        status='PENDING'
                    )
                    session.add(chunk)
                    session.flush()
                
                # Also create/ensure BatchOcrPage rows
                for p_num in range(start_p, end_p + 1):
                    ocr_p = session.query(BatchOcrPage).filter_by(chunk_id=chunk.id, page_number=p_num).first()
                    if not ocr_p:
                        ocr_p = BatchOcrPage(
                            chunk_id=chunk.id,
                            batch_item_id=batch_item_id,
                            page_number=p_num,
                            status='PENDING'
                        )
                        session.add(ocr_p)
                
                dispatched_chunks.append(chunk)

            # MUST commit DB transaction before dispatching tasks to Celery!
            session.commit()
            
            # Dispatch pending/failed chunk tasks
            for chunk in dispatched_chunks:
                if chunk.status in ('PENDING', 'FAILED'):
                    process_s3_batch_chunk.apply_async(
                        args=[chunk.id, org_slug, language],
                        queue='s3_batch'
                    )
            LOG.info(f"Preparation complete for BatchItem #{batch_item_id}: dispatched {len(dispatched_chunks)} chunk tasks.")

        except Exception as e:
            LOG.exception(f"Error in preparation for BatchItem #{batch_item_id}")
            item = session.query(BatchItem).get(batch_item_id)
            if item and self.request.retries < self.max_retries:
                item.status = 'PENDING'
                item.error_message = str(e)
                session.commit()
                raise self.retry(exc=e, countdown=60, kwargs={"batch_item_id": batch_item_id, "org_slug": org_slug, "language": language})
            if item:
                item.status = 'FAILED'
                item.error_message = str(e)
                item.completed_at = datetime.utcnow()
                session.commit()
                if item.job_id:
                    _finalize_batch_job_status(session, item.job_id)
            raise


@app.task(bind=True, max_retries=3)
def process_s3_batch_chunk(self, chunk_id: int, org_slug: str = None, language: str = "eng", **kwargs):
    """Celery task to process a bounded page chunk of a batch item."""
    with _get_app_context():
        session = q.get_session()
        chunk = session.query(BatchOcrChunk).get(chunk_id)
        if not chunk:
            LOG.error(f"BatchOcrChunk #{chunk_id} not found.")
            return

        # Idempotency Guard
        if chunk.status == 'COMPLETED':
            LOG.info(f"BatchOcrChunk #{chunk_id} is already COMPLETED. Skipping.")
            return
        if chunk.status == 'FAILED' and chunk.error_message == 'Cancelled by user':
            LOG.info(f"BatchOcrChunk #{chunk_id} was cancelled. Skipping.")
            return
        parent_item = session.query(BatchItem).get(chunk.batch_item_id)
        parent_job = session.query(BatchJob).get(parent_item.job_id) if parent_item and parent_item.job_id else None
        if parent_job and parent_job.status == 'FAILED' and parent_job.error_message == 'Cancelled by user':
            LOG.info(f"BatchOcrChunk #{chunk_id} belongs to a cancelled job. Skipping.")
            return

        # Atomically claim pending/failed work, or reclaim only an expired lease.
        now = datetime.utcnow()
        stale_before = now - CHUNK_LEASE_TIMEOUT
        claim = session.execute(
            update(BatchOcrChunk)
            .where(
                BatchOcrChunk.id == chunk_id,
                or_(
                    BatchOcrChunk.status.in_(('PENDING', 'FAILED')),
                    and_(
                        BatchOcrChunk.status == 'IN_PROGRESS',
                        or_(BatchOcrChunk.heartbeat_at.is_(None), BatchOcrChunk.heartbeat_at < stale_before),
                    ),
                ),
            )
            .values(
                status='IN_PROGRESS', started_at=now, heartbeat_at=now,
                attempt_count=BatchOcrChunk.attempt_count + 1,
            )
        )
        session.commit()
        if not claim.rowcount:
            session.expire_all()
            chunk = session.query(BatchOcrChunk).get(chunk_id)
            if chunk and chunk.status == 'COMPLETED':
                return
            if chunk and chunk.status == 'IN_PROGRESS':
                heartbeat = chunk.heartbeat_at or now
                wait_seconds = max(1, int((heartbeat + CHUNK_LEASE_TIMEOUT - now).total_seconds()))
                LOG.info(f"Chunk #{chunk_id} has an active lease; checking again in {wait_seconds}s.")
                raise self.retry(countdown=wait_seconds, max_retries=None,
                                 kwargs={"chunk_id": chunk_id, "org_slug": org_slug, "language": language})
            return
        session.expire_all()
        chunk = session.query(BatchOcrChunk).get(chunk_id)

        item = session.query(BatchItem).get(chunk.batch_item_id)
        if not item or not item.project_id:
            LOG.error(f"Item or Project not found for BatchOcrChunk #{chunk_id}.")
            chunk.status = 'FAILED'
            chunk.error_message = "Parent item or project missing"
            session.commit()
            _finalize_batch_item_status(session, chunk.batch_item_id)
        project = session.query(db.Project).get(item.project_id)
        storage = get_storage()

        # Resolve OCR service endpoint targets (primary + secondary)
        from flask import current_app
        raw_urls = [
            os.environ.get("BATCH_OCR_SERVICE_URL") or current_app.config.get("BATCH_OCR_SERVICE_URL"),
            os.environ.get("OCR_SERVICE_URL") or current_app.config.get("OCR_SERVICE_URL"),
            os.environ.get("OCR_SERVICE_URL_2") or current_app.config.get("OCR_SERVICE_URL_2"),
        ]
        ocr_targets = []
        for u in raw_urls:
            if u:
                u_clean = u.rstrip("/")
                target_url = u_clean if (u_clean.endswith("/v1/ocr") or u_clean.endswith("/ocr")) else f"{u_clean}/v1/ocr"
                if target_url not in ocr_targets:
                    ocr_targets.append(target_url)

        if not ocr_targets:
            chunk.status = 'FAILED'
            chunk.error_message = 'BATCH_OCR_SERVICE_URL / OCR_SERVICE_URL is not configured'
            chunk.completed_at = datetime.utcnow()
            session.commit()
            _finalize_batch_item_status(session, chunk.batch_item_id)
            return

        batch_ocr_api_key = (
            os.environ.get("BATCH_OCR_API_KEY")
            or current_app.config.get("BATCH_OCR_API_KEY")
            or os.environ.get("OCR_SERVICE_API_KEY")
            or current_app.config.get("OCR_SERVICE_API_KEY", "")
        )

        engine = "tesseract"
        language = language or "eng"
        version_key = f"ocr:{engine}"
        bot_user = q.user("kalanjiyam-bot")

        chunk_start_time = time.time()
        chunk_ocr_latency = 0

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_dir_path = Path(tmp_dir)
                timeout_config = httpx.Timeout(300.0, connect=30.0)

                with httpx.Client(timeout=timeout_config) as client:
                    for n in range(chunk.start_page, chunk.end_page + 1):
                        session.expire_all()
                        if _chunk_is_cancelled(session, chunk_id):
                            LOG.info(f"Chunk #{chunk_id} was cancelled or superseded. Stopping safely.")
                            return
                        chunk = session.query(BatchOcrChunk).get(chunk_id)
                        ocr_page = session.query(BatchOcrPage).filter_by(chunk_id=chunk.id, page_number=n).first()
                        if not ocr_page:
                            ocr_page = BatchOcrPage(
                                chunk_id=chunk.id,
                                batch_item_id=item.id,
                                page_number=n,
                                status='PENDING'
                            )
                            session.add(ocr_page)
                            session.flush()

                        # Durable Page-Level Idempotency Check
                        if ocr_page.status == 'COMPLETED':
                            LOG.info(f"Page {n} of project {project.slug} is already COMPLETED. Skipping.")
                            continue

                        ocr_page.status = 'IN_PROGRESS'
                        ocr_page.attempt_count += 1
                        session.commit()

                        page_key = page_image_key(project.slug, str(n))
                        img_bytes = storage.read_bytes(page_key)

                        retries = 2
                        ocr_result = None
                        page_start = time.time()

                        for target_url in ocr_targets:
                            headers = {"Accept": "application/json"}
                            if batch_ocr_api_key:
                                headers["X-API-Key"] = batch_ocr_api_key
                            backoff = 2

                            for attempt in range(retries):
                                try:
                                    files = {"image": (f"page_{n}.jpg", img_bytes, "image/jpeg"), "file": (f"page_{n}.jpg", img_bytes, "image/jpeg")}
                                    data = {"engine": engine, "language": language}
                                    params = {"language": language} if language else {}
                                    resp = client.post(target_url, files=files, data=data, params=params, headers=headers)
                                    resp.raise_for_status()
                                    try:
                                        ocr_result = resp.json()
                                    except Exception:
                                        ocr_result = resp.text
                                    break
                                except Exception as e:
                                    if attempt == retries - 1:
                                        LOG.warning(f"OCR HTTP request failed at {target_url} for page {n} after {retries} attempts: {e}")
                                    else:
                                        time.sleep(backoff)
                                        backoff *= 2
                            if ocr_result:
                                break

                        if not ocr_result:
                            ocr_page.status = 'FAILED'
                            ocr_page.error_message = "All configured OCR service targets failed"
                            ocr_page.completed_at = datetime.utcnow()
                            session.commit()
                            continue

                        page_latency = (time.time() - page_start) * 1000
                        chunk_ocr_latency += page_latency

                        if ocr_result:
                            if isinstance(ocr_result, dict):
                                results_list = ocr_result.get("results") or ocr_result.get("blocks") or []
                                txt = (
                                    ocr_result.get("text")
                                    or ocr_result.get("text_content")
                                    or ocr_result.get("output")
                                    or ocr_result.get("result")
                                    or ocr_result.get("ocr_text")
                                    or ""
                                )
                                boxes = ocr_result.get("bounding_boxes", [])
                                blks = ocr_result.get("blocks", [])
                                html = ocr_result.get("layout_html", "")

                                if isinstance(results_list, list) and results_list:
                                    parsed_blks = []
                                    parsed_boxes = []
                                    text_parts = []
                                    for idx, p_item in enumerate(results_list):
                                        if isinstance(p_item, dict):
                                            bbox = p_item.get("bbox", [0, 0, 0, 0])
                                            parsed_boxes.append(bbox)
                                            val = (
                                                p_item.get("text")
                                                or p_item.get("content")
                                                or p_item.get("ocr_text")
                                                or p_item.get("transcription")
                                                or p_item.get("label")
                                                or ""
                                            )
                                            cat = str(p_item.get("category", "paragraph")).lower()
                                            
                                            md_heading_match = re.match(r'^(#{1,6})\s+(.*)', val, flags=re.DOTALL)
                                            if md_heading_match:
                                                level = len(md_heading_match.group(1))
                                                val = md_heading_match.group(2).strip()
                                                blk_type = "heading" if level <= 2 else "subheading"
                                            else:
                                                if "table" in cat:
                                                    blk_type = "table"
                                                elif "title" in cat or "header" in cat or "caption" in cat:
                                                    blk_type = "heading"
                                                elif "picture" in cat or "image" in cat or "figure" in cat or "diagram" in cat:
                                                    blk_type = "figure"
                                                else:
                                                    blk_type = "paragraph"

                                            if val:
                                                text_parts.append(val)

                                            parsed_blks.append({
                                                "id": f"page-{n}-block-{idx+1}",
                                                "type": blk_type,
                                                "bbox": bbox,
                                                "content": val,
                                                "reading_order": idx + 1
                                            })
                                        elif isinstance(p_item, str):
                                            text_parts.append(p_item)
                                            parsed_blks.append({
                                                "id": f"page-{n}-block-{idx+1}",
                                                "type": "paragraph",
                                                "bbox": [0, 0, 0, 0],
                                                "content": p_item,
                                                "reading_order": idx + 1
                                            })

                                    if not txt and text_parts:
                                        txt = "\n\n".join(text_parts)
                                    if not blks and parsed_blks:
                                        blks = parsed_blks
                                    if not boxes and parsed_boxes:
                                        boxes = parsed_boxes
                            else:
                                txt = str(ocr_result)
                                boxes = []
                                blks = []
                                html = ""

                            tmp_crop_src = tmp_dir_path / f"tmp_crop_{n}.jpg"
                            tmp_crop_src.write_bytes(img_bytes)

                            from PIL import Image as PILImage
                            with PILImage.open(tmp_crop_src) as img_obj:
                                img_w, img_h = img_obj.width, img_obj.height

                            ocr_resp = OcrResponse(
                                text_content=txt,
                                bounding_boxes=boxes,
                                blocks=blks,
                                layout_html=html,
                                page_width=img_w,
                                page_height=img_h,
                                coordinate_space="pixel",
                                contract_version=ocr_result.get("contract_version") if isinstance(ocr_result, dict) else None,
                            )

                            if ocr_resp.blocks:
                                from kalanjiyam.utils.ocr_cropper import crop_ocr_response_elements
                                try:
                                    crop_ocr_response_elements(
                                        doc_path=str(tmp_crop_src),
                                        ocr_response=ocr_resp,
                                        project_slug=project.slug,
                                        output_dir=str(tmp_dir_path)
                                    )
                                except Exception as e:
                                    LOG.exception(f"Failed to crop visual elements for page {n}: {e}")
                             
                            page_record = q.page(project.id, str(n))
                            doc = apply_ocr_to_page(page_record, ocr_resp, engine, image_path=tmp_crop_src)
                            session.add(page_record)
                            session.commit()

                            if tmp_crop_src.exists():
                                tmp_crop_src.unlink()
                            
                            # 1. Save revision under ocr:{engine} track
                            pv = session.query(db.PageVersion).filter_by(
                                page_id=page_record.id,
                                version_key=version_key
                            ).first()
                            current_ver = pv.version if pv else 0
                            
                            add_revision(
                                page=page_record,
                                summary="Batch OCR run",
                                content=doc.to_plain_text() or ocr_resp.text_content,
                                status=SitePageStatus.R0,
                                version=current_ver,
                                author_id=bot_user.id if bot_user else None,
                                document=doc.to_dict(),
                                content_format=doc.content_format,
                                version_key=version_key,
                            )

                            # 2. ALSO save revision under role:p1 (default active track in UI)
                            pv_p1 = session.query(db.PageVersion).filter_by(
                                page_id=page_record.id,
                                version_key="role:p1"
                            ).first()
                            p1_ver = pv_p1.version if pv_p1 else 0

                            add_revision(
                                page=page_record,
                                summary="Batch OCR run (Default Track)",
                                content=doc.to_plain_text() or ocr_resp.text_content,
                                status=SitePageStatus.R0,
                                version=p1_ver,
                                author_id=bot_user.id if bot_user else None,
                                document=doc.to_dict(),
                                content_format=doc.content_format,
                                version_key="role:p1",
                            )
                            
                            # Record page-level metrics
                            ocr_page.ocr_latency_ms = page_latency
                            ocr_page.engine = engine
                            ocr_page.confidence = getattr(ocr_resp, "page_confidence", None)
                            ocr_page.p05 = getattr(ocr_resp, "p05", None)
                            ocr_page.blocks = getattr(ocr_resp, "blocks_count", None) or (len(doc.blocks) if doc else None)
                            ocr_page.chars = getattr(ocr_resp, "chars_count", None) or (len(plain_text) if plain_text else None)
                            ocr_page.engine_latency_ms = getattr(ocr_resp, "engine_latency_ms", None) or page_latency
                            
                            # Extracted page image size from storage
                            try:
                                if storage.exists(page_key):
                                    ocr_page.extracted_image_size_bytes = storage.size(page_key)
                            except Exception:
                                pass

                            # OCR data size (content + document JSON payload)
                            plain_text = doc.to_plain_text() or ocr_resp.text_content or ""
                            doc_json_str = json.dumps(doc.to_dict()) if doc else ""
                            ocr_page.ocr_data_size_bytes = len(plain_text.encode('utf-8')) + len(doc_json_str.encode('utf-8'))

                            # Cropped visual elements size for this page
                            page_crop_bytes = 0
                            if ocr_resp.blocks:
                                for block in ocr_resp.blocks:
                                    blk_id = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
                                    if blk_id:
                                        c_key = f"{project.slug}/images/extracted_{blk_id}.png"
                                        try:
                                            if storage.exists(c_key):
                                                page_crop_bytes += storage.size(c_key)
                                        except Exception:
                                            pass
                            ocr_page.cropped_image_size_bytes = page_crop_bytes

                            ocr_page.status = 'COMPLETED'
                            ocr_page.completed_at = datetime.utcnow()
                            ocr_page.error_message = None
                            session.commit()
                            LOG.info(f"Chunk #{chunk_id}: processed page {n} for project {project.slug}")

                        # Update heartbeat per page
                        chunk.heartbeat_at = datetime.utcnow()
                        session.commit()

            # Do not overwrite a concurrent cancellation or stale-worker reclaim.
            completed = session.execute(
                update(BatchOcrChunk)
                .where(BatchOcrChunk.id == chunk_id, BatchOcrChunk.status == 'IN_PROGRESS')
                .values(total_ocr_latency_ms=chunk_ocr_latency, status='COMPLETED', completed_at=datetime.utcnow())
            )
            session.commit()
            if not completed.rowcount:
                LOG.info(f"Chunk #{chunk_id} was cancelled or reclaimed before finalization.")
                return

            LOG.info(f"BatchOcrChunk #{chunk_id} ({chunk.start_page}-{chunk.end_page}) COMPLETED in {chunk_ocr_latency:.2f}ms.")
            _finalize_batch_item_status(session, chunk.batch_item_id)

        except Exception as e:
            LOG.exception(f"Error processing BatchOcrChunk #{chunk_id}")
            chunk = session.query(BatchOcrChunk).get(chunk_id)
            if chunk:
                if self.request.retries < self.max_retries:
                    chunk.status = 'PENDING'
                    chunk.error_message = str(e)
                    session.commit()
                    raise self.retry(exc=e, countdown=60, kwargs={"chunk_id": chunk_id, "org_slug": org_slug, "language": language})
                else:
                    chunk.status = 'FAILED'
                    chunk.error_message = str(e)
                    chunk.completed_at = datetime.utcnow()
                    session.commit()
                    _finalize_batch_item_status(session, chunk.batch_item_id)
            raise
