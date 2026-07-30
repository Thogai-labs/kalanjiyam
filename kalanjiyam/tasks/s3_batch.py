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
def process_s3_batch_item(self, batch_item_id: int, org_slug: str = None, language: str = "eng", start_page: int = 1):
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
        
        current_page = start_page
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
                
                batch_ocr_url = (
                    os.environ.get("BATCH_OCR_SERVICE_URL")
                    or current_app.config.get("BATCH_OCR_SERVICE_URL")
                    or os.environ.get("OCR_SERVICE_URL")
                    or current_app.config.get("OCR_SERVICE_URL", "")
                ).rstrip("/")
                
                batch_ocr_api_key = (
                    os.environ.get("BATCH_OCR_API_KEY")
                    or current_app.config.get("BATCH_OCR_API_KEY")
                    or os.environ.get("OCR_SERVICE_API_KEY")
                    or current_app.config.get("OCR_SERVICE_API_KEY", "")
                )
                
                if not batch_ocr_url:
                    LOG.warning(f"BATCH_OCR_SERVICE_URL / OCR_SERVICE_URL is not configured. Skipping OCR for BatchItem {batch_item_id}.")
                else:
                    headers = {"Accept": "application/json"}
                    if batch_ocr_api_key:
                        headers["X-API-Key"] = batch_ocr_api_key

                    if batch_ocr_url.endswith("/v1/ocr") or batch_ocr_url.endswith("/ocr"):
                        url = batch_ocr_url
                    else:
                        url = f"{batch_ocr_url}/v1/ocr"
                    
                    engine = "tesseract"
                    language = language or "eng"
                    version_key = f"ocr:{engine}"
                    bot_user = q.user("kalanjiyam-bot")
                    
                    total_ocr_latency = 0
                    total_payload_size = 0
                    
                    for n in range(start_page, page_count + 1):
                        current_page = n
                        page_key = page_image_key(project.slug, str(n))
                        img_bytes = storage.read_bytes(page_key)
                        
                        retries = 3
                        backoff = 2
                        ocr_result = None
                        
                        page_start = time.time()
                        for attempt in range(retries):
                            try:
                                with httpx.Client(timeout=300) as client:
                                    files = {"file": (f"page_{n}.jpg", img_bytes, "image/jpeg")}
                                    params = {"language": language} if language else {}
                                    resp = client.post(url, files=files, params=params, headers=headers)
                                    resp.raise_for_status()
                                    try:
                                        ocr_result = resp.json()
                                    except Exception:
                                        ocr_result = resp.text
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
                            payload_str = json.dumps(ocr_result) if isinstance(ocr_result, (dict, list)) else str(ocr_result)
                            total_payload_size += len(payload_str)
                            
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

                                # Parse results list if returned by custom OCR engine
                                if isinstance(results_list, list) and results_list:
                                    parsed_blks = []
                                    parsed_boxes = []
                                    text_parts = []
                                    for idx, item in enumerate(results_list):
                                        if isinstance(item, dict):
                                            bbox = item.get("bbox", [0, 0, 0, 0])
                                            parsed_boxes.append(bbox)
                                            val = (
                                                item.get("text")
                                                or item.get("content")
                                                or item.get("ocr_text")
                                                or item.get("transcription")
                                                or item.get("label")
                                                or ""
                                            )
                                            cat = str(item.get("category", "paragraph")).lower()
                                            
                                            import re
                                            md_heading_match = re.match(r'^(#{1,6})\s+(.*)', val, flags=re.DOTALL)
                                            if md_heading_match:
                                                level = len(md_heading_match.group(1))
                                                val = md_heading_match.group(2).strip()
                                                if level <= 2:
                                                    blk_type = "heading"
                                                else:
                                                    blk_type = "subheading"
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
                                        elif isinstance(item, str):
                                            text_parts.append(item)
                                            parsed_blks.append({
                                                "id": f"page-{n}-block-{idx+1}",
                                                "type": "paragraph",
                                                "bbox": [0, 0, 0, 0],
                                                "content": item,
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

                            # Read actual image dimensions for accurate coordinate mapping
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
                            )

                            # Extract visual elements if blocks are returned
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
                            
                            with open("/app/logs.txt", "w") as f:
                                f.write(f"{project.slug}-page-no={n}\n")
                            
                    item = session.query(BatchItem).get(batch_item_id)
                    if item:
                        item.total_ocr_latency_ms = total_ocr_latency
                        item.status = 'COMPLETED'
                        item.completed_at = datetime.utcnow()
                        LOG.info(f"Batch OCR complete for {project.slug}: {page_count} pages, {total_ocr_latency}ms latency.")
                        
                        job = session.query(BatchJob).get(item.job_id) if item.job_id else None
                        if job and all(i.status in ('COMPLETED', 'FAILED') for i in job.items):
                            job.status = 'COMPLETED' if any(i.status == 'COMPLETED' for i in job.items) else 'FAILED'
                            job.completed_at = datetime.utcnow()
                            
                        session.commit()
                
        except Exception as e:
            LOG.exception(f"Error processing BatchItem {batch_item_id}")
            item = session.query(BatchItem).get(batch_item_id)
            if item:
                item.status = 'FAILED'
                item.error_message = str(e)
                
                job = session.query(BatchJob).get(item.job_id) if item.job_id else None
                if job and all(i.status in ('COMPLETED', 'FAILED') for i in job.items):
                    job.status = 'COMPLETED' if any(i.status == 'COMPLETED' for i in job.items) else 'FAILED'
                    job.completed_at = datetime.utcnow()
                    
                session.commit()
            raise self.retry(exc=e, countdown=60, kwargs={"batch_item_id": batch_item_id, "org_slug": org_slug, "language": language, "start_page": current_page})
