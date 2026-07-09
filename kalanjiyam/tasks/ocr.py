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
    """Create a `group` task to run OCR on a project.

    Usage:

    >>> r = run_ocr_for_project(...)
    >>> progress = r.completed_count() / len(r.results)

    :param app_env: Application environment
    :param project: Project to run OCR on
    :param engine: OCR engine to use ('google' or 'tesseract')
    :param language: Language code for OCR (default: 'sa' for Sanskrit)
    :param queue: The Celery queue name to route tasks to
    :return: the Celery result, or ``None`` if no tasks were run.
    """
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        from sqlalchemy import or_
        session = q.get_session()
        bot_user = q.user(consts.BOT_USERNAME)
        bot_user_id = bot_user.id if bot_user else None

        db_project = session.query(db.Project).get(project.id)
        if not db_project:
            return None

        # Fetch IDs of all pages in the project that have user-authored revisions
        # (meaning author_id is None for anonymous edits, or is not the bot user)
        edited_page_ids = {
            row[0]
            for row in session.query(db.Revision.page_id)
            .filter(db.Revision.project_id == db_project.id)
            .filter(or_(db.Revision.author_id == None, db.Revision.author_id != bot_user_id))
            .all()
        }
        unedited_pages = [p for p in db_project.pages if p.id not in edited_page_ids]

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
        # Save the result so that we can poll for it later. If we don't do
        # this, the result won't be available at all..
        ret.save()
        return ret
    else:
        return None
