import logging
import os
import time
import tempfile
import mimetypes
from datetime import datetime
from pathlib import Path
from slugify import slugify
from urllib.parse import urlparse

import boto3
import fitz
from PIL import Image

from kalanjiyam.tasks import app
from kalanjiyam.models.batch import BatchItem, BatchJob
from kalanjiyam.models.group import Group, ProjectGroups
from kalanjiyam.utils.storage import get_storage, page_image_key
from kalanjiyam import database as db
from kalanjiyam import queries as q
from config import create_config_only_app

LOG = logging.getLogger(__name__)

def _download_from_s3(s3_url: str, dest_path: str):
    parsed = urlparse(s3_url)
    bucket = parsed.netloc
    key = parsed.path.lstrip('/')
    s3_client = boto3.client('s3')
    s3_client.download_file(bucket, key, dest_path)


def _setup_project(session, project_name: str, num_pages: int, creator_id: int = None, org_slug: str = None) -> db.Project:
    """Create a project and its pages in the database."""
    slug = slugify(project_name)
    # Check collision
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

@app.task(bind=True, max_retries=3)
def process_s3_batch_item(self, batch_item_id: int, org_slug: str = None, language: str = "eng"):
    """Celery task to download, convert, and OCR a batch item."""
    start_time = time.time()
    app_env = os.environ.get("KALANJIYAM_DEPLOYMENT_ENV", os.environ.get("FLASK_ENV", "development"))
    flask_app = create_config_only_app(app_env)
    
    with flask_app.app_context():
        session = q.get_session()
        item = session.query(BatchItem).get(batch_item_id)
        if not item:
            LOG.error(f"BatchItem {batch_item_id} not found.")
            return

        if item.status == 'FAILED':
            LOG.warning(f"BatchItem {batch_item_id} was cancelled or already failed. Skipping.")
            return

        item.status = 'IN_PROGRESS'
        session.commit()

        storage = get_storage()
        
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_dir_path = Path(tmp_dir)
                extract_start = time.time()
                
                if item.mime_type == 'application/pdf':
                    # Download PDF if S3
                    if item.file_path.startswith("s3://"):
                        local_path = tmp_dir_path / "source.pdf"
                        _download_from_s3(item.file_path, str(local_path))
                    else:
                        local_path = Path(item.file_path[7:])
                        
                    source_size = local_path.stat().st_size if local_path.is_file() else 0
                    item.source_size_bytes = source_size
                    item.status = 'DOWNLOADED'
                    session.commit()

                    # Extract using PyMuPDF
                    project_name = Path(item.file_path).stem
                    doc = fitz.open(local_path)
                    page_count = doc.page_count
                    
                    project = _setup_project(session, project_name, page_count, org_slug=org_slug)
                    item.project_id = project.id
                    
                    for page in doc:
                        n = page.number + 1
                        pix = page.get_pixmap(dpi=200)
                        tmp_img_path = tmp_dir_path / f"{n}.jpg"
                        pix.pil_save(tmp_img_path, optimize=True)
                        
                        storage.save(page_image_key(project.slug, str(n)), tmp_img_path)
                        tmp_img_path.unlink()
                else:
                    # Image folder logic (S3 or local directory)
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
                    
                    project = _setup_project(session, project_name, page_count, org_slug=org_slug)
                    item.project_id = project.id
                    
                    total_bytes = 0
                    for n, img_path in enumerate(image_paths, start=1):
                        total_bytes += img_path.stat().st_size
                        # Convert to standard JPEG via Pillow if needed
                        tmp_jpg = tmp_dir_path / f"{n}.jpg"
                        with Image.open(img_path) as im:
                            im.convert("RGB").save(tmp_jpg, "JPEG", quality=90, optimize=True)
                        
                        storage.save(page_image_key(project.slug, str(n)), tmp_jpg)
                        if tmp_jpg.exists() and tmp_jpg != img_path:
                            tmp_jpg.unlink()
                            
                    item.source_size_bytes = total_bytes
                    item.status = 'DOWNLOADED'
                    session.commit()
                
                extract_time = (time.time() - extract_start) * 1000
                item.extraction_latency_ms = extract_time
                
                item.status = 'IMAGES_EXTRACTED'
                session.commit()
                
                # 4. Fire OCR API
                from flask import current_app
                import httpx
                from kalanjiyam.utils.ocr_types import OcrResponse
                from kalanjiyam.utils.ocr_persist import apply_ocr_to_page
                from kalanjiyam.utils.revisions import add_revision
                from kalanjiyam.enums import SitePageStatus
                import json
                
                batch_ocr_url = (current_app.config.get("BATCH_OCR_SERVICE_URL") or current_app.config.get("OCR_SERVICE_URL", "")).rstrip("/")
                batch_ocr_api_key = current_app.config.get("BATCH_OCR_API_KEY") or current_app.config.get("OCR_SERVICE_API_KEY", "")
                
                if not batch_ocr_url:
                    LOG.warning(f"BATCH_OCR_SERVICE_URL / OCR_SERVICE_URL is not configured. Skipping OCR for BatchItem {batch_item_id}.")
                else:
                    headers = {"X-API-Key": batch_ocr_api_key} if batch_ocr_api_key else {}
                    url = f"{batch_ocr_url}/v1/ocr"
                    
                    engine = "tesseract"
                    language = language or "eng"
                    version_key = f"ocr:{engine}"
                    bot_user = q.user("kalanjiyam-bot")
                    
                    total_ocr_latency = 0
                    total_payload_size = 0
                    
                    for n in range(1, page_count + 1):
                        page_key = page_image_key(project.slug, str(n))
                        img_bytes = storage.read_bytes(page_key)
                        
                        retries = 3
                        backoff = 2
                        ocr_result = None
                        
                        page_start = time.time()
                        for attempt in range(retries):
                            try:
                                with httpx.Client(timeout=300) as client:
                                    files = {"image": (f"{n}.jpg", img_bytes, "image/jpeg")}
                                    data = {"engine": engine, "language": language}
                                    resp = client.post(url, files=files, data=data, headers=headers)
                                    resp.raise_for_status()
                                    ocr_result = resp.json()
                                    break
                            except Exception as e:
                                if attempt == retries - 1:
                                    LOG.exception(f"OCR failed for page {n} after {retries} attempts: {e}")
                                    raise
                                time.sleep(backoff)
                                backoff *= 2
                                
                        page_latency = (time.time() - page_start) * 1000
                        total_ocr_latency += page_latency
                        
                        if ocr_result:
                            payload_str = json.dumps(ocr_result)
                            total_payload_size += len(payload_str)
                            
                            ocr_resp = OcrResponse(
                                text_content=ocr_result.get("text", ""),
                                bounding_boxes=ocr_result.get("bounding_boxes", []),
                                blocks=ocr_result.get("blocks", []),
                                layout_html=ocr_result.get("layout_html", "")
                            )
                            
                            # Extract visual elements if blocks are returned
                            if ocr_resp.blocks:
                                from kalanjiyam.utils.ocr_cropper import crop_ocr_response_elements
                                try:
                                    tmp_crop_src = tmp_dir_path / f"tmp_crop_{n}.jpg"
                                    tmp_crop_src.write_bytes(img_bytes)
                                    crop_ocr_response_elements(
                                        doc_path=str(tmp_crop_src),
                                        ocr_response=ocr_resp,
                                        project_slug=project.slug,
                                        output_dir=str(tmp_dir_path)
                                    )
                                    tmp_crop_src.unlink()
                                except Exception as e:
                                    LOG.exception(f"Failed to crop visual elements for page {n}: {e}")
                            
                            page_record = q.page(project.id, str(n))
                            doc = apply_ocr_to_page(page_record, ocr_resp, engine)
                            session.add(page_record)
                            session.commit()
                            
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
                            
                    item.total_ocr_latency_ms = total_ocr_latency
                    LOG.info(f"Batch OCR complete for {project.slug}: {page_count} pages, {total_ocr_latency}ms latency.")
                
                item.status = 'COMPLETED'
                item.completed_at = datetime.utcnow()
                
                # Check if all items in parent job are finished
                if item.job and all(i.status in ('COMPLETED', 'FAILED') for i in item.job.items):
                    item.job.status = 'COMPLETED' if any(i.status == 'COMPLETED' for i in item.job.items) else 'FAILED'
                    item.job.completed_at = datetime.utcnow()
                    
                session.commit()
                
        except Exception as e:
            LOG.exception(f"Error processing BatchItem {batch_item_id}")
            item.status = 'FAILED'
            item.error_message = str(e)
            
            # Check if all items in parent job are finished
            if item.job and all(i.status in ('COMPLETED', 'FAILED') for i in item.job.items):
                item.job.status = 'COMPLETED' if any(i.status == 'COMPLETED' for i in item.job.items) else 'FAILED'
                item.job.completed_at = datetime.utcnow()
                
            session.commit()
            raise self.retry(exc=e, countdown=60)
