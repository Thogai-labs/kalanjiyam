import json
from unittest.mock import patch, Mock
import pytest

def test_api_glossaries_proxy(rama_client):
    """Test that /api/glossaries proxies requests to the external translation service."""
    mock_glossaries = [
        {
            "name": "administrative",
            "source_language_code": "en",
            "target_language_code": "mr",
            "filename": "administrative_en_mr.csv"
        }
    ]
    
    # Mock httpx.Client.get
    with patch("httpx.Client.get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_glossaries
        mock_get.return_value = mock_response
        
        r = rama_client.get("/api/glossaries")
        assert r.status_code == 200
        assert r.json == mock_glossaries
        mock_get.assert_called_once()
        assert "glossaries" in mock_get.call_args[0][0]


def test_api_glossaries_proxy_with_api_key(rama_client, flask_app):
    """Test that /api/glossaries passes X-API-Key header when configured."""
    mock_glossaries = [{"name": "admin"}]
    flask_app.config["TRANSLATION_SERVICE_API_KEY"] = "test-glossary-api-key"
    
    with patch("httpx.Client.get") as mock_get:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_glossaries
        mock_get.return_value = mock_response
        
        r = rama_client.get("/api/glossaries")
        assert r.status_code == 200
        mock_get.assert_called_once()
        headers = mock_get.call_args[1].get("headers", {})
        assert headers.get("X-API-Key") == "test-glossary-api-key"


def test_api_translate_with_glossary(rama_client):
    """Test that the translation API passes the glossary parameter to translate_text."""
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated with Glossary",
            source_language="en",
            target_language="sa",
            engine="indictrans2"
        )

        r = rama_client.get("/api/translate/test-project/1/?source_lang=en&target_lang=sa&engine=indictrans2&glossary=administrative")
        assert r.status_code == 200
        assert r.text == "Translated with Glossary"
        mock_translate.assert_called_once_with("Foo", "en", "sa", "indictrans2", glossary="administrative")


def test_docx_translate_with_glossary(rama_client):
    """Test that DOCX translator POST extracts glossary and queues task with it."""
    import io
    from kalanjiyam.models.proofing import Project
    from kalanjiyam.queries import get_session

    data = {
        "file": (io.BytesIO(b"dummy docx content"), "test.docx"),
        "source_lang": "sa",
        "target_lang": "en",
        "engine": "indictrans2",
        "glossary": "administrative"
    }

    with patch("kalanjiyam.utils.storage.LocalStorage.save"), \
         patch("redis.Redis.from_url") as mock_redis_class, \
         patch("kalanjiyam.tasks.translation.run_docx_translation.delay") as mock_celery_task:
        
        mock_redis = Mock()
        mock_redis_class.return_value = mock_redis
        mock_celery_task.return_value.id = "mock-task-id"

        r = rama_client.post(
            "/proofing/translate/docx",
            data=data,
            content_type="multipart/form-data"
        )
        assert r.status_code == 200
        
        # Verify glossary was passed to celery task delay
        mock_celery_task.assert_called_once()
        kwargs = mock_celery_task.call_args[1]
        assert kwargs["glossary"] == "administrative"
        
        # Verify redis serialization contains glossary
        mock_redis.setex.assert_called_once()
        redis_args = mock_redis.setex.call_args[0]
        stored_info = json.loads(redis_args[2])
        assert stored_info["glossary"] == "administrative"


def test_project_batch_translate_with_glossary(rama_client):
    """Test that project batch translation POST extracts glossary and runs task with it."""
    data = {
        "source_lang": "sa",
        "target_lang": "en",
        "engine": "indictrans2",
        "glossary": "administrative"
    }

    with patch("kalanjiyam.tasks.translation.run_translation_for_project") as mock_run_project_trans, \
         patch("kalanjiyam.views.proofing.project.redis_client") as mock_redis:
        mock_run_project_trans.return_value.id = "mock-group-task-id"
        mock_redis.get.return_value = None

        r = rama_client.post(
            "/proofing/test-project/batch-translate",
            data=data
        )
        # Should redirect/render status
        assert r.status_code == 200
        mock_run_project_trans.assert_called_once()
        kwargs = mock_run_project_trans.call_args[1]
        assert kwargs["glossary"] == "administrative"


def test_create_project_direct_docx_translate_with_glossary(rama_client):
    """Test that direct docx translation workflow in create_project route extracts glossary and triggers Celery task."""
    import io

    data = {
        "docx_workflow": "direct",
        "local_file": (io.BytesIO(b"dummy docx content"), "test.docx"),
        "source_lang": "sa",
        "target_lang": "en",
        "engine": "indictrans2",
        "glossary": "administrative"
    }

    with patch("kalanjiyam.utils.storage.LocalStorage.save"), \
         patch("redis.Redis.from_url") as mock_redis_class, \
         patch("kalanjiyam.tasks.translation.run_docx_translation.delay") as mock_celery_task:
        
        mock_redis = Mock()
        mock_redis_class.return_value = mock_redis
        mock_celery_task.return_value.id = "mock-task-id"

        r = rama_client.post(
            "/proofing/create-project",
            data=data,
            content_type="multipart/form-data"
        )
        assert r.status_code == 200
        
        # Verify glossary was passed to celery task delay
        mock_celery_task.assert_called_once()
        kwargs = mock_celery_task.call_args[1]
        assert kwargs["glossary"] == "administrative"
        
        # Verify redis serialization contains glossary
        mock_redis.setex.assert_called_once()
        redis_args = mock_redis.setex.call_args[0]
        stored_info = json.loads(redis_args[2])
        assert stored_info["glossary"] == "administrative"


def test_api_translate_with_multiple_glossaries(rama_client):
    """Test that the translation API passes multiple glossaries formatted as comma-separated string to translate_text."""
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated with multiple",
            source_language="en",
            target_language="sa",
            engine="indictrans2"
        )

        r = rama_client.get("/api/translate/test-project/1/?source_lang=en&target_lang=sa&engine=indictrans2&glossary=administrative,%20agri")
        assert r.status_code == 200
        assert r.text == "Translated with multiple"
        mock_translate.assert_called_once_with("Foo", "en", "sa", "indictrans2", glossary="administrative, agri")


def test_api_translate_with_all_glossaries(rama_client):
    """Test that the translation API passes the special 'all' option to translate_text."""
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated with all",
            source_language="en",
            target_language="sa",
            engine="indictrans2"
        )

        r = rama_client.get("/api/translate/test-project/1/?source_lang=en&target_lang=sa&engine=indictrans2&glossary=all")
        assert r.status_code == 200
        assert r.text == "Translated with all"
        mock_translate.assert_called_once_with("Foo", "en", "sa", "indictrans2", glossary="all")


