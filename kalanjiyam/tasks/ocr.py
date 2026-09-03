"""Background tasks for proofing projects."""

import logging
from datetime import datetime

from celery import group
from celery.result import GroupResult

from config import create_config_only_app
from kalanjiyam import consts
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.tasks import app
from kalanjiyam.utils.assets import get_page_image_filepath
from kalanjiyam.utils.ocr_persist import apply_ocr_to_page
from kalanjiyam.utils.quotas import (
    consume_ocr_credit_for_project,
    ensure_ocr_quota_for_project,
)
from kalanjiyam.utils.revisions import add_revision


def _run_ocr_for_page_inner(
    app_env: str,
    project_slug: str,
    page_slug: str,
    engine: str = "google",
    language: str = "auto",
    force: bool = False,
):
    """Must run in the application context."""
    import json
    import time

    from kalanjiyam.models.batch import BatchItem, BatchJob, BatchOcrPage
    from kalanjiyam.utils.storage import get_storage
    from kalanjiyam.utils.ocr_runner import normalize_engine, run_ocr

    page_start_time = time.time()
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        bot_user = q.user(consts.BOT_USERNAME)
        if bot_user is None:
            raise ValueError(f'User "{consts.BOT_USERNAME}" is not defined.')

        session = q.get_session()
        project = q.project(project_slug)
        if project is None:
            raise ValueError(f'Project "{project_slug}" not found.')

        page = q.page(project.id, page_slug)
        if page is None:
            raise ValueError(
                f'Page "{page_slug}" not found in project "{project_slug}".'
            )

        engine = normalize_engine(engine)
        version_key = f"ocr:{engine}"

        # Resolve version_key and current version of the track
        pv = (
            session.query(db.PageVersion)
            .filter_by(page_id=page.id, version_key=version_key)
            .first()
        )
        current_ver = pv.version if pv else 0

        # Check if the bot has already generated an OCR revision for this same engine track
        existing_bot_revision = None
        if pv and not force:
            existing_bot_revision = (
                session.query(db.Revision)
                .filter_by(
                    page_id=page.id,
                    page_version_id=pv.id,
                    author_id=bot_user.id,
                )
                .order_by(db.Revision.created.desc())
                .first()
            )

        if existing_bot_revision:
            logging.info(
                f"OCR revision already exists for {project_slug}/{page_slug} with engine '{engine}' ({version_key}). Skipping OCR."
            )
            # Record/update UI Batch OCR metrics in BatchItem / BatchOcrPage
            try:
                batch_item = (
                    session.query(BatchItem)
                    .join(BatchJob)
                    .filter(
                        BatchItem.project_id == project.id,
                        BatchJob.job_type == "UI_BATCH_OCR",
                    )
                    .order_by(BatchItem.id.desc())
                    .first()
                )
                if not batch_item:
                    batch_item = (
                        session.query(BatchItem)
                        .filter_by(project_id=project.id)
                        .order_by(BatchItem.id.desc())
                        .first()
                    )
                if batch_item:
                    p_num = page.order
                    ocr_page = (
                        session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id, page_number=p_num)
                        .first()
                    )
                    if not ocr_page and page_slug.isdigit():
                        ocr_page = (
                            session.query(BatchOcrPage)
                            .filter_by(
                                batch_item_id=batch_item.id, page_number=int(page_slug)
                            )
                            .first()
                        )
                    if not ocr_page:
                        ocr_page = BatchOcrPage(
                            batch_item_id=batch_item.id,
                            chunk_id=None,
                            page_number=p_num,
                            status="PENDING",
                        )

                    plain_text = existing_bot_revision.content or ""
                    doc_dict = existing_bot_revision.document or {}
                    doc_json_str = json.dumps(doc_dict) if doc_dict else ""
                    blocks = doc_dict.get("blocks", []) if isinstance(doc_dict, dict) else []

                    ocr_page.ocr_latency_ms = ocr_page.ocr_latency_ms or 0.0
                    ocr_page.status = "COMPLETED"
                    ocr_page.completed_at = ocr_page.completed_at or datetime.utcnow()
                    ocr_page.engine = engine
                    ocr_page.chars = len(plain_text) if plain_text else 0
                    ocr_page.blocks = len(blocks) if blocks else None
                    ocr_page.ocr_data_size_bytes = len(plain_text.encode("utf-8")) + len(doc_json_str.encode("utf-8"))

                    image_path = get_page_image_filepath(project_slug, page_slug)
                    try:
                        if image_path and image_path.exists():
                            ocr_page.extracted_image_size_bytes = image_path.stat().st_size
                    except Exception:
                        pass

                    session.add(ocr_page)
                    session.flush()

                    # Recalculate cumulative item metrics from all completed pages
                    item_pages = (
                        session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id, status="COMPLETED")
                        .all()
                    )
                    batch_item.engine = engine
                    batch_item.total_ocr_latency_ms = sum(
                        p.ocr_latency_ms or 0 for p in item_pages
                    )
                    batch_item.extracted_images_size_bytes = sum(
                        p.extracted_image_size_bytes or 0 for p in item_pages
                    )
                    batch_item.cropped_images_size_bytes = sum(
                        p.cropped_image_size_bytes or 0 for p in item_pages
                    )
                    batch_item.ocr_data_size_bytes = sum(
                        p.ocr_data_size_bytes or 0 for p in item_pages
                    )

                    conf_list = [
                        p.confidence for p in item_pages if p.confidence is not None
                    ]
                    batch_item.avg_confidence = (
                        (sum(conf_list) / len(conf_list)) if conf_list else None
                    )
                    p05_list = [p.p05 for p in item_pages if p.p05 is not None]
                    batch_item.avg_p05 = (
                        (sum(p05_list) / len(p05_list)) if p05_list else None
                    )
                    batch_item.total_blocks = sum(p.blocks or 0 for p in item_pages)
                    batch_item.total_chars = sum(p.chars or 0 for p in item_pages)
                    batch_item.total_engine_latency_ms = sum(
                        p.engine_latency_ms or 0 for p in item_pages
                    )

                    # Update status if all pages completed
                    completed_count = len(item_pages)
                    target_total = (
                        batch_item.total_pages
                        or session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id)
                        .count()
                        or 1
                    )
                    if completed_count >= target_total:
                        batch_item.status = "COMPLETED"
                        batch_item.completed_at = datetime.utcnow()
                        if batch_item.job:
                            batch_item.job.status = "COMPLETED"
                            batch_item.job.completed_at = datetime.utcnow()

                    session.commit()
            except Exception as metric_err:
                logging.warning(f"Error recording UI batch OCR metrics for skipped page: {metric_err}")

            return existing_bot_revision.id

        # The actual API call.
        image_path = get_page_image_filepath(project_slug, page_slug)

        ensure_ocr_quota_for_project(project)
        ocr_response = run_ocr(image_path, engine_name=engine, language=language)

        # Extract visual elements if blocks are returned
        if ocr_response.blocks:
            from kalanjiyam.utils.ocr_cropper import crop_ocr_response_elements

            try:
                crop_ocr_response_elements(
                    doc_path=str(image_path),
                    ocr_response=ocr_response,
                    project_slug=project_slug,
                    output_dir=str(image_path.parent),
                )
            except Exception as e:
                logging.exception(f"Failed to crop visual elements: {e}")

        doc = apply_ocr_to_page(page, ocr_response, engine, image_path=image_path)
        session.add(page)
        session.commit()

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

            # Warm Redis OCR cache for latest version
            try:
                from kalanjiyam.utils.ocr_cache import set_cached_ocr_document, set_cached_page_bboxes

                set_cached_ocr_document(
                    project_slug,
                    page_slug,
                    version_key,
                    doc.to_dict(),
                    version=revision_id,
                )
                if getattr(page, "ocr_bounding_boxes", None):
                    set_cached_page_bboxes(
                        project_slug,
                        page_slug,
                        version_key,
                        page.ocr_bounding_boxes,
                        version=revision_id,
                    )
            except Exception as cache_err:
                logging.debug(f"Could not warm Redis OCR cache for {project_slug}/{page_slug}: {cache_err}")

            # Record UI Batch OCR metrics in BatchItem / BatchOcrPage
            try:
                page_latency_ms = (time.time() - page_start_time) * 1000.0
                batch_item = (
                    session.query(BatchItem)
                    .join(BatchJob)
                    .filter(
                        BatchItem.project_id == project.id,
                        BatchJob.job_type == "UI_BATCH_OCR",
                    )
                    .order_by(BatchItem.id.desc())
                    .first()
                )
                if not batch_item:
                    batch_item = (
                        session.query(BatchItem)
                        .filter_by(project_id=project.id)
                        .order_by(BatchItem.id.desc())
                        .first()
                    )
                if batch_item:
                    p_num = page.order
                    ocr_page = (
                        session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id, page_number=p_num)
                        .first()
                    )
                    if not ocr_page and page_slug.isdigit():
                        ocr_page = (
                            session.query(BatchOcrPage)
                            .filter_by(
                                batch_item_id=batch_item.id, page_number=int(page_slug)
                            )
                            .first()
                        )
                    if not ocr_page:
                        # Dummy chunk id or null if allowed, or find chunk
                        ocr_page = BatchOcrPage(
                            batch_item_id=batch_item.id,
                            chunk_id=None,
                            page_number=p_num,
                            status="PENDING",
                        )

                    plain_text = doc.to_plain_text() or ocr_response.text_content or ""
                    ocr_page.ocr_latency_ms = page_latency_ms
                    ocr_page.status = "COMPLETED"
                    ocr_page.completed_at = datetime.utcnow()
                    ocr_page.engine = engine
                    ocr_page.confidence = getattr(ocr_response, "page_confidence", None)
                    ocr_page.p05 = getattr(ocr_response, "p05", None)
                    ocr_page.blocks = getattr(ocr_response, "blocks_count", None) or (
                        len(doc.blocks) if doc else None
                    )
                    ocr_page.chars = getattr(ocr_response, "chars_count", None) or (
                        len(plain_text) if plain_text else None
                    )
                    ocr_page.engine_latency_ms = getattr(
                        ocr_response, "engine_latency_ms", None
                    )

                    try:
                        if image_path and image_path.exists():
                            ocr_page.extracted_image_size_bytes = (
                                image_path.stat().st_size
                            )
                    except Exception:
                        pass

                    doc_json_str = json.dumps(doc.to_dict()) if doc else ""
                    ocr_page.ocr_data_size_bytes = len(
                        plain_text.encode("utf-8")
                    ) + len(doc_json_str.encode("utf-8"))

                    page_crop_bytes = 0
                    if ocr_response.blocks:
                        from kalanjiyam.utils.storage import editor_image_key

                        for block in ocr_response.blocks:
                            blk_id = (
                                block.get("id")
                                if isinstance(block, dict)
                                else getattr(block, "id", None)
                            )
                            if blk_id:
                                c_key = editor_image_key(
                                    project.slug, f"extracted_{blk_id}.png"
                                )
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
                    item_pages = (
                        session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id, status="COMPLETED")
                        .all()
                    )
                    batch_item.engine = engine
                    batch_item.total_ocr_latency_ms = sum(
                        p.ocr_latency_ms or 0 for p in item_pages
                    )
                    batch_item.extracted_images_size_bytes = sum(
                        p.extracted_image_size_bytes or 0 for p in item_pages
                    )
                    batch_item.cropped_images_size_bytes = sum(
                        p.cropped_image_size_bytes or 0 for p in item_pages
                    )
                    batch_item.ocr_data_size_bytes = sum(
                        p.ocr_data_size_bytes or 0 for p in item_pages
                    )

                    conf_list = [
                        p.confidence for p in item_pages if p.confidence is not None
                    ]
                    batch_item.avg_confidence = (
                        (sum(conf_list) / len(conf_list)) if conf_list else None
                    )
                    p05_list = [p.p05 for p in item_pages if p.p05 is not None]
                    batch_item.avg_p05 = (
                        (sum(p05_list) / len(p05_list)) if p05_list else None
                    )
                    batch_item.total_blocks = sum(p.blocks or 0 for p in item_pages)
                    batch_item.total_chars = sum(p.chars or 0 for p in item_pages)
                    batch_item.total_engine_latency_ms = sum(
                        p.engine_latency_ms or 0 for p in item_pages
                    )

                    # Update status if all pages completed
                    completed_count = len(item_pages)
                    target_total = (
                        batch_item.total_pages
                        or session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id)
                        .count()
                        or 1
                    )
                    if completed_count >= target_total:
                        batch_item.status = "COMPLETED"
                        batch_item.completed_at = datetime.utcnow()
                        if batch_item.job:
                            batch_item.job.status = "COMPLETED"
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
    engine: str = "1",  # Default to Google OCR (1)
    language: str = "auto",
    force: bool = False,
):
    _run_ocr_for_page_inner(
        app_env,
        project_slug,
        page_slug,
        engine,
        language,
        force=force,
    )


def run_ocr_for_project(
    app_env: str,
    project: db.Project,
    engine: str = "1",  # Default to Google OCR (1)
    language: str = "auto",
    queue: str | None = None,
) -> GroupResult | None:
    """Create a `group` task to run OCR on a project."""
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        from sqlalchemy import or_

        from kalanjiyam.models.batch import BatchItem, BatchJob, BatchOcrPage

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
            .filter(
                or_(db.Revision.author_id == None, db.Revision.author_id != bot_user_id)
            )
            .all()
        }
        unedited_pages = [p for p in db_project.pages if p.id not in edited_page_ids]

        if unedited_pages:
            # Create BatchJob and BatchItem for UI-triggered batch OCR
            batch_job = BatchJob(
                target_uri=f"ui://project/{project.slug}",
                status="IN_PROGRESS",
                job_type="UI_BATCH_OCR",
            )
            session.add(batch_job)
            session.flush()

            project_title = getattr(db_project, "display_title", None) or project.slug
            batch_item = BatchItem(
                job_id=batch_job.id,
                file_path=f"{project_title} ({project.slug})",
                project_id=db_project.id,
                status="IN_PROGRESS",
                total_pages=len(unedited_pages),
            )

            try:
                from kalanjiyam.utils.storage import (
                    get_storage,
                    pdf_key,
                    project_docx_key,
                )

                storage = get_storage()

                # Check for PDF first
                for k, size in storage.list_keys(pdf_key(project.slug)):
                    if k == pdf_key(project.slug):
                        batch_item.source_size_bytes = size
                        break

                # If no PDF, check for DOCX
                if batch_item.source_size_bytes is None:
                    for k, size in storage.list_keys(project_docx_key(project.slug)):
                        if k == project_docx_key(project.slug):
                            batch_item.source_size_bytes = size
                            break
            except Exception:
                pass

            session.add(batch_item)
            session.flush()

            for p in unedited_pages:
                ocr_p = BatchOcrPage(
                    batch_item_id=batch_item.id,
                    chunk_id=None,
                    page_number=p.order,
                    status="PENDING",
                )
                session.add(ocr_p)
            session.commit()

    if unedited_pages:
        from kalanjiyam.tasks import PRIORITY_BATCH, PRIORITY_LOW

        priority = PRIORITY_LOW if queue == "low_priority" else PRIORITY_BATCH
        tasks = group(
            run_ocr_for_page.s(
                app_env=app_env,
                project_slug=project.slug,
                page_slug=p.slug,
                engine=engine,
                language=language,
            ).set(priority=priority)
            for p in unedited_pages
        )
        if queue:
            ret = tasks.apply_async(queue=queue, priority=priority)
        else:
            ret = tasks.apply_async(priority=priority)
        ret.save()
        return ret
    else:
        return None


def _run_enhanced_ocr_for_page_inner(
    app_env: str,
    project_slug: str,
    page_slug: str,
    engine: str = "dots_ocr",
    profile: str = "document_cleanup",
    language: str = "auto",
    save_enhanced_images: bool = False,
    force: bool = False,
):
    """Run Enhanced OCR in application context."""
    import io
    import json
    import time
    from PIL import Image

    from kalanjiyam.models.batch import BatchItem, BatchJob, BatchOcrPage
    from kalanjiyam.utils.document_storage import (
        load_page_enhanced_ocr,
        save_page_enhanced_ocr,
    )
    from kalanjiyam.utils.enhanced_ocr import run_enhanced_ocr
    from kalanjiyam.utils.image_preprocessing import (
        preprocess_image,
        validate_enhancement_profile,
    )
    from kalanjiyam.utils.ocr_persist import ocr_response_to_api_dict
    from kalanjiyam.utils.ocr_types import normalize_engine
    from kalanjiyam.utils.storage import (
        get_project_org_slug,
        get_storage,
        page_image_key,
        page_master_image_key,
    )

    page_start_time = time.time()
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        bot_user = q.user(consts.BOT_USERNAME)
        if bot_user is None:
            raise ValueError(f'User "{consts.BOT_USERNAME}" is not defined.')

        session = q.get_session()
        project = q.project(project_slug)
        if project is None:
            raise ValueError(f'Project "{project_slug}" not found.')

        page = q.page(project.id, page_slug)
        if page is None:
            raise ValueError(
                f'Page "{page_slug}" not found in project "{project_slug}".'
            )

        org_slug = get_project_org_slug(project)
        image_path = get_page_image_filepath(project_slug, page_slug, org_slug=org_slug)
        engine = normalize_engine(engine)
        profile = validate_enhancement_profile(profile)
        version_key = f"ocr:enhanced:{engine}:{profile}"

        pv = (
            session.query(db.PageVersion)
            .filter_by(page_id=page.id, version_key=version_key)
            .first()
        )
        current_ver = pv.version if pv else 0

        # Check if the bot has already generated an Enhanced OCR revision for this engine & profile
        existing_bot_revision = None
        if pv and not force:
            existing_bot_revision = (
                session.query(db.Revision)
                .filter_by(
                    page_id=page.id,
                    page_version_id=pv.id,
                    author_id=bot_user.id,
                )
                .order_by(db.Revision.created.desc())
                .first()
            )

        if existing_bot_revision:
            logging.info(
                f"Enhanced OCR revision already exists for {project_slug}/{page_slug} with engine '{engine}' and profile '{profile}' ({version_key}). Skipping Enhanced OCR."
            )
            # Record/update UI Batch Enhanced OCR metrics in BatchItem / BatchOcrPage
            try:
                batch_item = (
                    session.query(BatchItem)
                    .join(BatchJob)
                    .filter(
                        BatchItem.project_id == project.id,
                        BatchJob.job_type == "UI_BATCH_ENHANCED_OCR",
                    )
                    .order_by(BatchItem.id.desc())
                    .first()
                )
                if not batch_item:
                    batch_item = (
                        session.query(BatchItem)
                        .filter_by(project_id=project.id)
                        .order_by(BatchItem.id.desc())
                        .first()
                    )
                if batch_item:
                    p_num = page.order
                    ocr_page = (
                        session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id, page_number=p_num)
                        .first()
                    )
                    if not ocr_page and page_slug.isdigit():
                        ocr_page = (
                            session.query(BatchOcrPage)
                            .filter_by(
                                batch_item_id=batch_item.id, page_number=int(page_slug)
                            )
                            .first()
                        )
                    if not ocr_page:
                        ocr_page = BatchOcrPage(
                            batch_item_id=batch_item.id,
                            chunk_id=None,
                            page_number=p_num,
                            status="PENDING",
                        )

                    plain_text = existing_bot_revision.content or ""
                    doc_dict = existing_bot_revision.document or {}
                    doc_json_str = json.dumps(doc_dict) if doc_dict else ""
                    blocks = doc_dict.get("blocks", []) if isinstance(doc_dict, dict) else []

                    ocr_page.ocr_latency_ms = ocr_page.ocr_latency_ms or 0.0
                    ocr_page.status = "COMPLETED"
                    ocr_page.completed_at = ocr_page.completed_at or datetime.utcnow()
                    ocr_page.engine = engine
                    ocr_page.chars = len(plain_text) if plain_text else 0
                    ocr_page.blocks = len(blocks) if blocks else None
                    ocr_page.ocr_data_size_bytes = len(plain_text.encode("utf-8")) + len(doc_json_str.encode("utf-8"))

                    try:
                        if image_path and image_path.exists():
                            ocr_page.extracted_image_size_bytes = image_path.stat().st_size
                    except Exception:
                        pass

                    session.add(ocr_page)
                    session.flush()

                    # Recalculate cumulative item metrics from all completed pages
                    item_pages = (
                        session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id, status="COMPLETED")
                        .all()
                    )
                    batch_item.engine = engine
                    batch_item.total_ocr_latency_ms = sum(
                        p.ocr_latency_ms or 0 for p in item_pages
                    )
                    batch_item.extracted_images_size_bytes = sum(
                        p.extracted_image_size_bytes or 0 for p in item_pages
                    )
                    batch_item.cropped_images_size_bytes = sum(
                        p.cropped_image_size_bytes or 0 for p in item_pages
                    )
                    batch_item.ocr_data_size_bytes = sum(
                        p.ocr_data_size_bytes or 0 for p in item_pages
                    )

                    conf_list = [
                        p.confidence for p in item_pages if p.confidence is not None
                    ]
                    batch_item.avg_confidence = (
                        (sum(conf_list) / len(conf_list)) if conf_list else None
                    )
                    p05_list = [p.p05 for p in item_pages if p.p05 is not None]
                    batch_item.avg_p05 = (
                        (sum(p05_list) / len(p05_list)) if p05_list else None
                    )
                    batch_item.total_blocks = sum(p.blocks or 0 for p in item_pages)
                    batch_item.total_chars = sum(p.chars or 0 for p in item_pages)
                    batch_item.total_engine_latency_ms = sum(
                        p.engine_latency_ms or 0 for p in item_pages
                    )

                    # Update status if all pages completed
                    completed_count = len(item_pages)
                    target_total = (
                        batch_item.total_pages
                        or session.query(BatchOcrPage)
                        .filter_by(batch_item_id=batch_item.id)
                        .count()
                        or 1
                    )
                    if completed_count >= target_total:
                        batch_item.status = "COMPLETED"
                        batch_item.completed_at = datetime.utcnow()
                        if batch_item.job:
                            batch_item.job.status = "COMPLETED"
                            batch_item.job.completed_at = datetime.utcnow()

                    session.commit()
            except Exception as metric_err:
                logging.warning(f"Error recording UI batch enhanced OCR metrics for skipped page: {metric_err}")

            cached_payload = load_page_enhanced_ocr(page, engine, profile)
            if cached_payload:
                return cached_payload
            if existing_bot_revision.document:
                return existing_bot_revision.document
            return {"text_content": existing_bot_revision.content or "", "blocks": []}

        # If save_enhanced_images is True, replace active page image with preprocessed version
        if save_enhanced_images and image_path and image_path.exists():
            try:
                storage = get_storage()
                active_key = page_image_key(project_slug, page_slug, org_slug=org_slug)
                master_key = page_master_image_key(
                    project_slug, page_slug, org_slug=org_slug
                )

                # 1. Back up current master scan if not already backed up
                if not storage.exists(master_key):
                    if storage.exists(active_key):
                        storage.save(master_key, storage.read_bytes(active_key))
                    else:
                        storage.save(master_key, image_path.read_bytes())

                # 2. Preprocess from master scan
                if storage.exists(master_key):
                    master_bytes = storage.read_bytes(master_key)
                    with Image.open(io.BytesIO(master_bytes)) as img:
                        processed = preprocess_image(img, profile)
                else:
                    with Image.open(image_path) as img:
                        processed = preprocess_image(img, profile)

                if processed.mode not in ("RGB", "L"):
                    processed = processed.convert("RGB")

                buf = io.BytesIO()
                processed.save(buf, format="JPEG", quality=95)
                new_bytes = buf.getvalue()

                # 3. Save as active image
                storage.save(active_key, new_bytes)
                try:
                    image_path.write_bytes(new_bytes)
                except Exception:
                    pass
            except Exception as e:
                logging.warning(
                    f"Failed to replace active page image for {project_slug}/{page_slug}: {e}"
                )

        ocr_response = run_enhanced_ocr(
            image_path,
            engine_name=engine,
            profile=profile,
            language=language,
        )

        # Extract visual elements if blocks are returned
        if ocr_response.blocks:
            from kalanjiyam.utils.ocr_cropper import crop_ocr_response_elements

            try:
                crop_ocr_response_elements(
                    doc_path=str(image_path),
                    ocr_response=ocr_response,
                    project_slug=project_slug,
                    output_dir=str(image_path.parent),
                )
            except Exception as e:
                logging.exception(f"Failed to crop visual elements: {e}")

        ensure_ocr_quota_for_project(project)

        doc = apply_ocr_to_page(page, ocr_response, engine, image_path=image_path)
        session.add(page)
        session.commit()

        # Save standalone enhanced OCR JSON (.json.gz) to separate storage path
        payload_dict = ocr_response_to_api_dict(
            ocr_response,
            engine,
            image_width=page.page_width,
            image_height=page.page_height,
        )
        save_page_enhanced_ocr(page, payload_dict, engine, profile)

        summary = f"Enhanced OCR run ({engine}, {profile})"
        try:
            add_revision(
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
        except Exception as e:
            logging.exception(f"Failed to save revision for enhanced OCR: {e}")
            raise

        # Record UI Batch Enhanced OCR metrics in BatchItem / BatchOcrPage
        try:
            page_latency_ms = (time.time() - page_start_time) * 1000.0
            batch_item = (
                session.query(BatchItem)
                .join(BatchJob)
                .filter(
                    BatchItem.project_id == project.id,
                    BatchJob.job_type == "UI_BATCH_ENHANCED_OCR",
                )
                .order_by(BatchItem.id.desc())
                .first()
            )
            if not batch_item:
                batch_item = (
                    session.query(BatchItem)
                    .filter_by(project_id=project.id)
                    .order_by(BatchItem.id.desc())
                    .first()
                )
            if batch_item:
                p_num = page.order
                ocr_page = (
                    session.query(BatchOcrPage)
                    .filter_by(batch_item_id=batch_item.id, page_number=p_num)
                    .first()
                )
                if not ocr_page and page_slug.isdigit():
                    ocr_page = (
                        session.query(BatchOcrPage)
                        .filter_by(
                            batch_item_id=batch_item.id, page_number=int(page_slug)
                        )
                        .first()
                    )
                if not ocr_page:
                    ocr_page = BatchOcrPage(
                        batch_item_id=batch_item.id,
                        chunk_id=None,
                        page_number=p_num,
                        status="PENDING",
                    )

                plain_text = doc.to_plain_text() or ocr_response.text_content or ""
                ocr_page.ocr_latency_ms = page_latency_ms
                ocr_page.status = "COMPLETED"
                ocr_page.completed_at = datetime.utcnow()
                ocr_page.engine = engine
                ocr_page.confidence = getattr(ocr_response, "page_confidence", None)
                ocr_page.p05 = getattr(ocr_response, "p05", None)
                ocr_page.blocks = getattr(ocr_response, "blocks_count", None) or (
                    len(doc.blocks) if doc else None
                )
                ocr_page.chars = getattr(ocr_response, "chars_count", None) or (
                    len(plain_text) if plain_text else None
                )
                ocr_page.engine_latency_ms = getattr(
                    ocr_response, "engine_latency_ms", None
                )

                try:
                    if image_path and image_path.exists():
                        ocr_page.extracted_image_size_bytes = (
                            image_path.stat().st_size
                        )
                except Exception:
                    pass

                doc_json_str = json.dumps(doc.to_dict()) if doc else ""
                ocr_page.ocr_data_size_bytes = len(
                    plain_text.encode("utf-8")
                ) + len(doc_json_str.encode("utf-8"))

                page_crop_bytes = 0
                if ocr_response.blocks:
                    from kalanjiyam.utils.storage import editor_image_key

                    for block in ocr_response.blocks:
                        blk_id = (
                            block.get("id")
                            if isinstance(block, dict)
                            else getattr(block, "id", None)
                        )
                        if blk_id:
                            c_key = editor_image_key(
                                project.slug, f"extracted_{blk_id}.png"
                            )
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
                item_pages = (
                    session.query(BatchOcrPage)
                    .filter_by(batch_item_id=batch_item.id, status="COMPLETED")
                    .all()
                )
                batch_item.engine = engine
                batch_item.total_ocr_latency_ms = sum(
                    p.ocr_latency_ms or 0 for p in item_pages
                )
                batch_item.extracted_images_size_bytes = sum(
                    p.extracted_image_size_bytes or 0 for p in item_pages
                )
                batch_item.cropped_images_size_bytes = sum(
                    p.cropped_image_size_bytes or 0 for p in item_pages
                )
                batch_item.ocr_data_size_bytes = sum(
                    p.ocr_data_size_bytes or 0 for p in item_pages
                )

                conf_list = [
                    p.confidence for p in item_pages if p.confidence is not None
                ]
                batch_item.avg_confidence = (
                    (sum(conf_list) / len(conf_list)) if conf_list else None
                )
                p05_list = [p.p05 for p in item_pages if p.p05 is not None]
                batch_item.avg_p05 = (
                    (sum(p05_list) / len(p05_list)) if p05_list else None
                )
                batch_item.total_blocks = sum(p.blocks or 0 for p in item_pages)
                batch_item.total_chars = sum(p.chars or 0 for p in item_pages)
                batch_item.total_engine_latency_ms = sum(
                    p.engine_latency_ms or 0 for p in item_pages
                )

                # Update status if all pages completed
                completed_count = len(item_pages)
                target_total = (
                    batch_item.total_pages
                    or session.query(BatchOcrPage)
                    .filter_by(batch_item_id=batch_item.id)
                    .count()
                    or 1
                )
                if completed_count >= target_total:
                    batch_item.status = "COMPLETED"
                    batch_item.completed_at = datetime.utcnow()
                    if batch_item.job:
                        batch_item.job.status = "COMPLETED"
                        batch_item.job.completed_at = datetime.utcnow()

                session.commit()
        except Exception as metric_err:
            logging.warning(f"Error recording UI batch enhanced OCR metrics: {metric_err}")

    return payload_dict


@app.task(bind=True)
def run_enhanced_ocr_for_page(
    self,
    *,
    app_env: str,
    project_slug: str,
    page_slug: str,
    engine: str = "dots_ocr",
    profile: str = "document_cleanup",
    language: str = "auto",
    save_enhanced_images: bool = False,
    force: bool = False,
):
    _run_enhanced_ocr_for_page_inner(
        app_env,
        project_slug,
        page_slug,
        engine,
        profile,
        language,
        save_enhanced_images=save_enhanced_images,
        force=force,
    )


def run_enhanced_ocr_for_project(
    app_env: str,
    project: db.Project,
    engine: str = "dots_ocr",
    profile: str = "document_cleanup",
    language: str = "auto",
    save_enhanced_images: bool = False,
    queue: str | None = None,
) -> GroupResult | None:
    """Create a `group` task to run Enhanced OCR on a project."""
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        from sqlalchemy import or_

        from kalanjiyam.models.batch import BatchItem, BatchJob, BatchOcrPage

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
            .filter(
                or_(db.Revision.author_id == None, db.Revision.author_id != bot_user_id)
            )
            .all()
        }
        unedited_pages = [p for p in db_project.pages if p.id not in edited_page_ids]

        if unedited_pages:
            batch_job = BatchJob(
                target_uri=f"ui://project/{project.slug}/enhanced",
                status="IN_PROGRESS",
                job_type="UI_BATCH_ENHANCED_OCR",
            )
            session.add(batch_job)
            session.flush()

            project_title = getattr(db_project, "display_title", None) or project.slug
            batch_item = BatchItem(
                job_id=batch_job.id,
                file_path=f"{project_title} ({project.slug}) [Enhanced:{profile}]",
                project_id=db_project.id,
                status="IN_PROGRESS",
                total_pages=len(unedited_pages),
            )

            try:
                from kalanjiyam.utils.storage import (
                    get_storage,
                    pdf_key,
                    project_docx_key,
                )

                storage = get_storage()
                for k, size in storage.list_keys(pdf_key(project.slug)):
                    if k == pdf_key(project.slug):
                        batch_item.source_size_bytes = size
                        break
                if batch_item.source_size_bytes is None:
                    for k, size in storage.list_keys(project_docx_key(project.slug)):
                        if k == project_docx_key(project.slug):
                            batch_item.source_size_bytes = size
                            break
            except Exception:
                pass

            session.add(batch_item)
            session.flush()

            for p in unedited_pages:
                ocr_p = BatchOcrPage(
                    batch_item_id=batch_item.id,
                    chunk_id=None,
                    page_number=p.order,
                    status="PENDING",
                )
                session.add(ocr_p)
            session.commit()

    if unedited_pages:
        from kalanjiyam.tasks import PRIORITY_BATCH, PRIORITY_LOW

        priority = PRIORITY_LOW if queue == "low_priority" else PRIORITY_BATCH
        tasks = group(
            run_enhanced_ocr_for_page.s(
                app_env=app_env,
                project_slug=project.slug,
                page_slug=p.slug,
                engine=engine,
                profile=profile,
                language=language,
                save_enhanced_images=save_enhanced_images,
            ).set(priority=priority)
            for p in unedited_pages
        )
        if queue:
            ret = tasks.apply_async(queue=queue, priority=priority)
        else:
            ret = tasks.apply_async(priority=priority)
        ret.save()
        return ret
    else:
        return None
