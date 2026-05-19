"""Background tasks for translation services."""

import logging
from celery import group
from celery.result import GroupResult

from kalanjiyam import consts
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.tasks import app
from kalanjiyam.utils.translation_engine import translate_text, segment_text_for_translation
from config import create_config_only_app

LOG = logging.getLogger(__name__)


def _run_translation_for_page_inner(
    app_env: str,
    project_slug: str,
    page_slug: str,
    source_lang: str = 'sa',
    target_lang: str = 'en',
    engine: str = 'google',
    revision_id: int = None,
) -> int:
    """Must run in the application context."""

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
            raise ValueError(f'Page "{page_slug}" not found in project "{project_slug}".')

        # Get the revision to translate
        if revision_id is None:
            # Use the latest revision
            if not page.revisions:
                raise ValueError(f'No revisions found for page "{page_slug}".')
            revision = page.revisions[-1]  # Latest revision
        else:
            revision = session.query(db.Revision).filter_by(id=revision_id).first()
            if not revision or revision.page_id != page.id:
                raise ValueError(f'Revision {revision_id} not found for page "{page_slug}".')

        # Check if translation already exists
        existing_translation = session.query(db.Translation).filter_by(
            page_id=page.id,
            revision_id=revision.id,
            source_language=source_lang,
            target_language=target_lang,
            translation_engine=engine
        ).first()

        if existing_translation:
            LOG.info(f"Translation already exists for {project_slug}/{page_slug} ({source_lang}->{target_lang})")
            return existing_translation.id

        # Segment text for translation
        text_segments = segment_text_for_translation(revision.content, max_length=1000)
        
        # Translate each segment
        translated_segments = []
        translation_failed = False
        
        for segment in text_segments:
            if segment.strip():
                try:
                    translation_response = translate_text(
                        segment, 
                        source_lang, 
                        target_lang, 
                        engine
                    )
                    translated_segments.append(translation_response.translated_text)
                except Exception as e:
                    LOG.error(f"Translation failed for segment: {e}")
                    translation_failed = True
                    break  # Stop translation if any segment fails
            else:
                translated_segments.append(segment)

        # Only create translation record if translation was successful
        if not translation_failed:
            # Combine translated segments
            translated_content = '\n\n'.join(translated_segments)

            # Create translation record
            translation = db.Translation(
                page_id=page.id,
                revision_id=revision.id,
                author_id=bot_user.id,
                content=translated_content,
                source_language=source_lang,
                target_language=target_lang,
                translation_engine=engine,
                status='completed'
            )
            
            session.add(translation)
            session.commit()
            
            LOG.info(f"Translation completed for {project_slug}/{page_slug} ({source_lang}->{target_lang})")
            return translation.id
        else:
            LOG.warning(f"Translation failed for {project_slug}/{page_slug} ({source_lang}->{target_lang}) - no translation record created")
            return None


@app.task(bind=True)
def run_translation_for_page(
    self,
    *,
    app_env: str,
    project_slug: str,
    page_slug: str,
    source_lang: str = 'sa',
    target_lang: str = 'en',
    engine: str = 'google',
    revision_id: int = None,
):
    """Run translation for a single page."""
    try:
        return _run_translation_for_page_inner(
            app_env,
            project_slug,
            page_slug,
            source_lang,
            target_lang,
            engine,
            revision_id,
        )
    except Exception as e:
        LOG.error(f"Translation task failed for {project_slug}/{page_slug}: {e}")
        raise


def run_translation_for_project(
    app_env: str,
    project: db.Project,
    source_lang: str = 'sa',
    target_lang: str = 'en',
    engine: str = 'google',
    revision_id: int = None,
) -> GroupResult | None:
    """Create a `group` task to run translation on a project.

    Usage:

    >>> r = run_translation_for_project(...)
    >>> progress = r.completed_count() / len(r.results)

    :param app_env: Application environment
    :param project: Project to run translation on
    :param source_lang: Source language code
    :param target_lang: Target language code
    :param engine: Translation engine to use
    :param revision_id: Specific revision ID to translate (optional)
    :return: the Celery result, or ``None`` if no tasks were run.
    """
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        # Get pages that have revisions
        pages_with_revisions = [p for p in project.pages if p.revisions]

    if pages_with_revisions:
        tasks = group(
            run_translation_for_page.s(
                app_env=app_env,
                project_slug=project.slug,
                page_slug=p.slug,
                source_lang=source_lang,
                target_lang=target_lang,
                engine=engine,
                revision_id=revision_id,
            )
            for p in pages_with_revisions
        )
        ret = tasks.apply_async()
        # Save the result so that we can poll for it later
        ret.save()
        return ret
    else:
        return None


@app.task(bind=True)
def run_translation_for_revision(
    self,
    *,
    app_env: str,
    revision_id: int,
    source_lang: str = 'sa',
    target_lang: str = 'en',
    engine: str = 'google',
):
    """Run translation for a specific revision across all pages in the project."""
    flask_app = create_config_only_app(app_env)
    with flask_app.app_context():
        session = q.get_session()
        revision = session.query(db.Revision).filter_by(id=revision_id).first()
        if not revision:
            raise ValueError(f'Revision {revision_id} not found.')
        
        project = revision.project
        if not project:
            raise ValueError(f'Project not found for revision {revision_id}.')
        
        # Run translation for the specific page of this revision
        return _run_translation_for_page_inner(
            app_env,
            project.slug,
            revision.page.slug,
            source_lang,
            target_lang,
            engine,
            revision_id,
        )


def _clear_translation_task_from_redis(task_id):
    """Clear translation task from Redis when it completes or fails."""
    try:
        import redis
        import os
        import json
        
        redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
        
        # Find the task key by scanning Redis keys
        for key in redis_client.scan_iter(match="translation_task:*"):
            task_info = redis_client.get(key)
            if task_info:
                task_data = json.loads(task_info)
                if task_data.get('task_id') == task_id:
                    redis_client.delete(key)
                    break
    except Exception as e:
        LOG.warning(f"Error clearing translation task from Redis: {e}") 