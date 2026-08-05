"""Background tasks for proofing projects."""


from celery import group
from celery.result import GroupResult

from kalanjiyam import consts
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.tasks import app
from kalanjiyam.utils.ocr_persist import apply_ocr_to_page
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.utils.quotas import consume_ocr_credit_for_project, ensure_ocr_quota_for_project
from kalanjiyam.utils.revisions import add_revision
from config import create_config_only_app
from typing import Optional, List
from datetime import datetime
import logging


def _run_ocr_for_page_inner(
    app_env: str,
    project_slug: str,
    page_slug: str,
    engine: str = 'google',
    language: str = 'sa',
) -> int:
    """Must run in the application context."""
    import time
    import json
    from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
    from kalanjiyam.utils.storage import get_storage, page_image_key

    page_start_time = time.time()
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        bot_user = q.user(consts.BOT_USERNAME)
        if bot_user is None:
            raise ValueError(f'User "{consts.BOT_USERNAME}" is not defined.')

        # The actual API call.
        image_path = get_page_image_filepath(project_slug, page_slug)
        
        from kalanjiyam.utils.ocr_runner import normalize_engine, run_ocr

        engine = normalize_engine(engine)
        ocr_response = run_ocr(image_path, engine_name=engine, language=language)

        # Extract visual elements if blocks are returned
        if ocr_response.blocks:
            from kalanjiyam.utils.ocr_cropper import crop_ocr_response_elements
            try:
                crop_ocr_response_elements(
                    doc_path=str(image_path),
                    ocr_response=ocr_response,
                    project_slug=project_slug,
                    output_dir=str(image_path.parent)
                )
            except Exception as e:
                logging.exception(f"Failed to crop visual elements: {e}")

        session = q.get_session()
        project = q.project(project_slug)
        if project is None:
            raise ValueError(f'Project "{project_slug}" not found.')
        ensure_ocr_quota_for_project(project)
        
        page = q.page(project.id, page_slug)
        if page is None:
            raise ValueError(f'Page "{page_slug}" not found in project "{project_slug}".')

        doc = apply_ocr_to_page(page, ocr_response, engine, image_path=image_path)
        session.add(page)
        session.commit()

        # Resolve version_key and current version of the track
        version_key = f"ocr:{engine}"
        pv = session.query(db.PageVersion).filter_by(
            page_id=page.id,
            version_key=version_key
        ).first()
        current_ver = pv.version if pv else 0

        summary = "OCR run"
        try:
            revision_id = add_revision(
                page=page,
                summary=summary,
                content=doc.to_plain_text() or ocr_response.text_content,
                status=SitePageStatus.R0,
                version=current_ver,
                author_id=bot_user.id,
                document=doc.to_dict(),
                content_format=doc.content_format,
                version_key=version_key,
            )
            consume_ocr_credit_for_project(project)

            # Record UI Batch OCR metrics in BatchItem / BatchOcrPage
            try:
                page_latency_ms = (time.time() - page_start_time) * 1000.0
                batch_item = session.query(BatchItem).join(BatchJob).filter(
                    BatchItem.project_id == project.id,
                    BatchJob.job_type == 'UI_BATCH_OCR'
                ).order_by(BatchItem.id.desc()).first()
                if not batch_item:
                    batch_item = session.query(BatchItem).filter_by(project_id=project.id).order_by(BatchItem.id.desc()).first()
                if batch_item:
                    p_num = page.order
                    ocr_page = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, page_number=p_num).first()
                    if not ocr_page and page_slug.isdigit():
                        ocr_page = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, page_number=int(page_slug)).first()
                    if not ocr_page:
                        # Dummy chunk id or null if allowed, or find chunk
                        ocr_page = BatchOcrPage(
                            batch_item_id=batch_item.id,
                            chunk_id=None,
                            page_number=p_num,
                            status='PENDING'
                        )

                    ocr_page.ocr_latency_ms = page_latency_ms
                    ocr_page.status = 'COMPLETED'
                    ocr_page.completed_at = datetime.utcnow()

                    try:
                        if image_path and image_path.exists():
                            ocr_page.extracted_image_size_bytes = image_path.stat().st_size
                    except Exception:
                        pass

                    plain_text = doc.to_plain_text() or ocr_response.text_content or ""
                    doc_json_str = json.dumps(doc.to_dict()) if doc else ""
                    ocr_page.ocr_data_size_bytes = len(plain_text.encode('utf-8')) + len(doc_json_str.encode('utf-8'))

                    page_crop_bytes = 0
                    if ocr_response.blocks:
                        from kalanjiyam.utils.storage import editor_image_key
                        for block in ocr_response.blocks:
                            blk_id = block.get("id") if isinstance(block, dict) else getattr(block, "id", None)
                            if blk_id:
                                c_key = editor_image_key(project.slug, f"extracted_{blk_id}.png")
                                try:
                                    c_path = get_storage().local_copy(c_key)
                                    if c_path.exists():
                                        page_crop_bytes += c_path.stat().st_size
                                except Exception:
                                    pass
                    ocr_page.cropped_image_size_bytes = page_crop_bytes
                    session.add(ocr_page)
                    session.flush()

                    # Recalculate cumulative item metrics from all completed pages
                    item_pages = session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id, status='COMPLETED').all()
                    batch_item.total_ocr_latency_ms = sum(p.ocr_latency_ms or 0 for p in item_pages)
                    batch_item.extracted_images_size_bytes = sum(p.extracted_image_size_bytes or 0 for p in item_pages)
                    batch_item.cropped_images_size_bytes = sum(p.cropped_image_size_bytes or 0 for p in item_pages)
                    batch_item.ocr_data_size_bytes = sum(p.ocr_data_size_bytes or 0 for p in item_pages)
                    
                    # Update status if all pages completed
                    completed_count = len(item_pages)
                    target_total = batch_item.total_pages or session.query(BatchOcrPage).filter_by(batch_item_id=batch_item.id).count() or 1
                    if completed_count >= target_total:
                        batch_item.status = 'COMPLETED'
                        batch_item.completed_at = datetime.utcnow()
                        if batch_item.job:
                            batch_item.job.status = 'COMPLETED'
                            batch_item.job.completed_at = datetime.utcnow()

                    session.commit()
            except Exception as metric_err:
                logging.warning(f"Error recording UI batch OCR metrics: {metric_err}")

            return revision_id
        except Exception as e:
            raise ValueError(
                f'OCR failed for page "{project.slug}/{page.slug}" with engine {engine} and language {language}.'
            ) from e


@app.task(bind=True)
def run_ocr_for_page(
    self,
    *,
    app_env: str,
    project_slug: str,
    page_slug: str,
    engine: str = '1',  # Default to Google OCR (1)
    language: str = 'sa',
):
    _run_ocr_for_page_inner(
        app_env,
        project_slug,
        page_slug,
        engine,
        language,
    )


def run_ocr_for_project(
    app_env: str,
    project: db.Project,
    engine: str = '1',  # Default to Google OCR (1)
    language: str = 'sa',
    queue: str | None = None,
) -> GroupResult | None:
    """Create a `group` task to run OCR on a project."""
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        from sqlalchemy import or_
        from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrPage
        session = q.get_session()
        bot_user = q.user(consts.BOT_USERNAME)
        bot_user_id = bot_user.id if bot_user else None

        db_project = session.query(db.Project).get(project.id)
        if not db_project:
            return None

        edited_page_ids = {
            row[0]
            for row in session.query(db.Revision.page_id)
            .filter(db.Revision.project_id == db_project.id)
            .filter(or_(db.Revision.author_id == None, db.Revision.author_id != bot_user_id))
            .all()
        }
        unedited_pages = [p for p in db_project.pages if p.id not in edited_page_ids]

        if unedited_pages:
            # Create BatchJob and BatchItem for UI-triggered batch OCR
            batch_job = BatchJob(
                target_uri=f"ui://project/{project.slug}",
                status='IN_PROGRESS',
                job_type='UI_BATCH_OCR'
            )
            session.add(batch_job)
            session.flush()

            project_title = getattr(db_project, 'display_title', None) or project.slug
            batch_item = BatchItem(
                job_id=batch_job.id,
                file_path=f"{project_title} ({project.slug})",
                project_id=db_project.id,
                status='IN_PROGRESS',
                total_pages=len(unedited_pages),
            )
            
            try:
                from kalanjiyam.utils.storage import get_storage, pdf_key
                s_key = pdf_key(project.slug)
                s_path = get_storage().local_copy(s_key)
                if s_path.exists():
                    batch_item.source_size_bytes = s_path.stat().st_size
            except Exception:
                pass

            session.add(batch_item)
            session.flush()

            for p in unedited_pages:
                ocr_p = BatchOcrPage(
                    batch_item_id=batch_item.id,
                    chunk_id=None,
                    page_number=p.order,
                    status='PENDING'
                )
                session.add(ocr_p)
            session.commit()

    if unedited_pages:
        tasks = group(
            run_ocr_for_page.s(
                app_env=app_env,
                project_slug=project.slug,
                page_slug=p.slug,
                engine=engine,
                language=language,
            )
            for p in unedited_pages
        )
        if queue:
            ret = tasks.apply_async(queue=queue)
        else:
            ret = tasks.apply_async()
        ret.save()
        return ret
    else:
        return None

