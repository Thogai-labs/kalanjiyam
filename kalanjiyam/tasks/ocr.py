"""Background tasks for proofing projects."""


from celery import group
from celery.result import GroupResult

from kalanjiyam import consts
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SitePageStatus
from kalanjiyam.tasks import app
from kalanjiyam.utils import google_ocr
from kalanjiyam.utils.assets import get_page_image_filepath
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
    # Decode numeric engine values to actual engine names
    engine_map = {
        '1': 'google',
        '2': 'tesseract',
        '3': 'surya',
        '4': 'docling'
    }
    if engine in engine_map:
        engine = engine_map[engine]
    """Must run in the application context."""

    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        bot_user = q.user(consts.BOT_USERNAME)
        if bot_user is None:
            raise ValueError(f'User "{consts.BOT_USERNAME}" is not defined.')

        # The actual API call.
        image_path = get_page_image_filepath(project_slug, page_slug)

        from kalanjiyam.utils.ocr_engine import run_ocr

        # Get GPU configuration for Surya OCR
        gpu_config = None
        if engine == 'surya':
            from kalanjiyam.utils.surya_gpu_config import get_gpu_config_from_env
            gpu_config = get_gpu_config_from_env()

        ocr_response = run_ocr(
            image_path, engine_name=engine, language=language, gpu_config=gpu_config)

        session = q.get_session()
        project = q.project(project_slug)
        if project is None:
            raise ValueError(f'Project "{project_slug}" not found.')

        page = q.page(project.id, page_slug)
        if page is None:
            raise ValueError(
                f'Page "{page_slug}" not found in project "{project_slug}".')

        # Use the appropriate serialize function based on engine
        if engine == 'google':
            page.ocr_bounding_boxes = google_ocr.serialize_bounding_boxes(
                ocr_response.bounding_boxes
            )
        elif engine == 'surya':
            from kalanjiyam.utils.surya_ocr import serialize_bounding_boxes
            page.ocr_bounding_boxes = serialize_bounding_boxes(
                ocr_response.bounding_boxes
            )
        elif engine == 'docling':   # Add this new condition
            from kalanjiyam.utils.docling_ocr import serialize_bounding_boxes
            page.ocr_bounding_boxes = serialize_bounding_boxes(
                ocr_response.bounding_boxes
            )
        else:
            from kalanjiyam.utils.tesseract_ocr import serialize_bounding_boxes
            page.ocr_bounding_boxes = serialize_bounding_boxes(
                ocr_response.bounding_boxes
            )

        session.add(page)
        session.commit()

        summary = f"Run OCR ({engine}, {language})"
        try:
            return add_revision(
                page=page,
                summary=summary,
                content=ocr_response.text_content,
                status=SitePageStatus.R0,
                version=0,
                author_id=bot_user.id,
            )
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
) -> GroupResult | None:
    """Create a `group` task to run OCR on a project.

    Usage:

    >>> r = run_ocr_for_project(...)
    >>> progress = r.completed_count() / len(r.results)

    :param app_env: Application environment
    :param project: Project to run OCR on
    :param engine: OCR engine to use ('google' or 'tesseract')
    :param language: Language code for OCR (default: 'sa' for Sanskrit)
    :return: the Celery result, or ``None`` if no tasks were run.
    """
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        unedited_pages = [p for p in project.pages if p.version == 0]

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
        ret = tasks.apply_async()
        # Save the result so that we can poll for it later. If we don't do
        # this, the result won't be available at all..
        ret.save()
        return ret
    else:
        return None
