import kalanjiyam.utils.assets


def test_get_image_filesystem_path(flask_app):
    with flask_app.app_context():
        path = kalanjiyam.utils.assets.get_page_image_filepath("project", "1")
    assert path.match("**/project/pages/1.jpg")


def test_edit__unauth(client):
    r = client.get("/proofing/test-project/1/")
    assert "Only registered users can save changes." in r.text
    assert "Publish changes" not in r.text


def test_edit__guest_owner(flask_app, client):
    from kalanjiyam import queries as q
    with flask_app.app_context():
        project = q.project("test-project")
        project.fingerprint_id = "test-guest-fp"
        session = q.get_session()
        session.add(project)
        session.commit()

    client.set_cookie("device_fingerprint", "test-guest-fp")
    r = client.get("/proofing/test-project/1/")
    assert "Publish changes" in r.text


def test_edit__auth(rama_client):
    r = rama_client.get("/proofing/test-project/1/")
    assert "Publish changes" in r.text


def test_edit__bad_project(client):
    r = client.get("/proofing/unknown/1/")
    assert r.status_code == 404


def test_edit__bad_page(client):
    r = client.get("/proofing/test-project/unknown/")
    assert r.status_code == 404


def test_history(client):
    r = client.get("/proofing/test-project/1/history")
    assert "History:" in r.text


def test_history__bad_project(client):
    r = client.get("/proofing/unknown/1/history")
    assert r.status_code == 404


def test_history__bad_page(client):
    r = client.get("/proofing/test-project/unknown/history")
    assert r.status_code == 404


def test_revision(client):
    r = client.get("/proofing/test-project/1/revision/1")
    assert "Revision:" in r.text


def test_revision__bad_project(client):
    r = client.get("/proofing/unknown/1/revision/1")
    assert r.status_code == 404


def test_revision__bad_page(client):
    r = client.get("/proofing/test-project/unknown/revision/1")
    assert r.status_code == 404


def test_revision__bad_revision(client):
    r = client.get("/proofing/test-project/1/revision/4000")
    assert r.status_code == 404


def test_revision__bad_revision_non_numeric(client):
    r = client.get("/proofing/test-project/1/revision/unknown")
    assert r.status_code == 404


def test_translate_api_get(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated Hello",
            source_language="sa",
            target_language="en",
            engine="indictrans2"
        )

        r = rama_client.get("/api/translate/test-project/1/?source_lang=sa&target_lang=en&engine=indictrans2")
        assert r.status_code == 200
        assert r.text == "Translated Hello"


def test_translate_api_post(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated Hello Block",
            source_language="sa",
            target_language="en",
            engine="indictrans2"
        )

        payload = {
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "content": "Hello Sanskrit"
                }
            ]
        }
        r = rama_client.post(
            "/api/translate/test-project/1/?source_lang=sa&target_lang=en&engine=indictrans2",
            json=payload
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["blocks"][0]["content"] == "Translated Hello Block"
        mock_translate.assert_called_once_with("Hello Sanskrit", "sa", "en", "indictrans2")


def test_translate_api_post_preserves_html(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="नमस्ते",
            source_language="en",
            target_language="hi",
            engine="indictrans2"
        )

        payload = {
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "content": '<img class="w-full" src="/static/img.png"> Hello world'
                }
            ]
        }
        r = rama_client.post(
            "/api/translate/test-project/1/?source_lang=en&target_lang=hi&engine=indictrans2",
            json=payload
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["blocks"][0]["content"] == '<img class="w-full" src="/static/img.png"> नमस्ते'
        mock_translate.assert_called_once_with("Hello world", "en", "hi", "indictrans2")
