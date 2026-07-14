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
    import kalanjiyam.database as db
    from kalanjiyam.queries import get_session

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated Hello",
            source_language="en",
            target_language="sa",
            engine="indictrans2"
        )

        r = rama_client.get("/api/translate/test-project/1/?source_lang=en&target_lang=sa&engine=indictrans2")
        assert r.status_code == 200
        assert r.text == "Translated Hello"

        # Assert PageVersion was created
        session = get_session()
        page = session.query(db.Page).filter_by(slug="1").first()
        pv = session.query(db.PageVersion).filter_by(
            page_id=page.id,
            version_key="translation:indictrans2:en->sa"
        ).first()
        assert pv is not None
        assert len(pv.revisions) == 1
        assert pv.revisions[0].content == "Translated Hello"


def test_translate_api_post(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Translated Hello Block",
            source_language="en",
            target_language="sa",
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
            "/api/translate/test-project/1/?source_lang=en&target_lang=sa&engine=indictrans2",
            json=payload
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["blocks"][0]["content"] == "Translated Hello Block"
        mock_translate.assert_called_once_with("Hello Sanskrit", "en", "sa", "indictrans2")

        # Assert PageVersion and Revision records were created
        session = get_session()
        page = session.query(db.Page).filter_by(slug="1").first()
        pv = session.query(db.PageVersion).filter_by(
            page_id=page.id,
            version_key="translation:indictrans2:en->sa"
        ).first()
        assert pv is not None
        assert len(pv.revisions) == 1
        assert pv.revisions[0].content == "Translated Hello Block"
        assert pv.revisions[0].document["blocks"][0]["content"] == "Translated Hello Block"


def test_translate_api_post_preserves_html(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

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


def test_translate_api_selective_translation_english(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="வணக்கம் உலகமே.",
            source_language="en",
            target_language="ta",
            engine="indictrans2"
        )

        payload = {
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "content": "சும்மா இருக்கச் சொல்லுது.\nHello world.\nनमः शिवाय"
                }
            ]
        }
        r = rama_client.post(
            "/api/translate/test-project/1/?source_lang=en&target_lang=ta&engine=indictrans2",
            json=payload
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["blocks"][0]["content"] == "சும்மா இருக்கச் சொல்லுது.\nவணக்கம் உலகமே.\nनमः शिवाय"
        mock_translate.assert_called_once_with("Hello world.", "en", "ta", "indictrans2")


def test_translate_api_selective_translation_tamil(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text="Hello world translated.",
            source_language="ta",
            target_language="en",
            engine="indictrans2"
        )

        payload = {
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "content": "சும்மா இருக்கச் சொல்லுது.\nHello world.\nनमः शिवाय"
                }
            ]
        }
        r = rama_client.post(
            "/api/translate/test-project/1/?source_lang=ta&target_lang=en&engine=indictrans2",
            json=payload
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["blocks"][0]["content"] == "Hello world translated.\nHello world.\nनमः शिवाय"
        mock_translate.assert_called_once_with("சும்மா இருக்கச் சொல்லுது.", "ta", "en", "indictrans2")


def test_translate_api_selective_translation_proper_nouns(rama_client):
    from unittest.mock import patch
    from kalanjiyam.utils.translation_engine import TranslationResponse
    from kalanjiyam.queries import get_session
    import kalanjiyam.database as db

    session = get_session()
    session.query(db.Translation).delete()
    session.query(db.Revision).filter(db.Revision.id > 1).delete()
    session.commit()

    with patch("kalanjiyam.views.proofing.page.translate_text") as mock_translate:
        mock_translate.return_value = TranslationResponse(
            translated_text=(
                "திருமதி நளினி தனேஜா\n\n"
                "டெல்லி பல்கலைக்கழகம்\n\n"
                "பேராசிரியர் கபில் குமார் (கூட்டுபவர்)\n\n"
                "இக்னோ, புது டெல்லி"
            ),
            source_language="en",
            target_language="ta",
            engine="indictrans2"
        )

        payload = {
            "blocks": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "content": (
                        "Ms. Nalini Taneja\n"
                        "पत्रव्यवहार अभ्यास शाळा\n"
                        "Delhi University\n"
                        "\n"
                        "Prof. Kapil Kumar (Convener)\n"
                        "अध्यक्ष, इतिहास विभाग\n"
                        "सामाजिक विज्ञान शाळा\n"
                        "IGNOU, New Delhi"
                    )
                }
            ]
        }
        r = rama_client.post(
            "/api/translate/test-project/1/?source_lang=en&target_lang=ta&engine=indictrans2",
            json=payload
        )
        assert r.status_code == 200
        data = r.get_json()
        
        expected_content = (
            "திருமதி நளினி தனேஜா\n"
            "पत्रव्यवहार अभ्यास शाळा\n"
            "டெல்லி பல்கலைக்கழகம்\n"
            "\n"
            "பேராசிரியர் கபில் குமார் (கூட்டுபவர்)\n"
            "अध्यक्ष, इतिहास विभाग\n"
            "सामाजिक विज्ञान शाळा\n"
            "இக்னோ, புது டெல்லி"
        )
        assert data["blocks"][0]["content"] == expected_content
        
        expected_joined_text = (
            "Ms. Nalini Taneja\n\n"
            "Delhi University\n\n"
            "Prof. Kapil Kumar (Convener)\n\n"
            "IGNOU, New Delhi"
        )
        mock_translate.assert_called_once_with(expected_joined_text, "en", "ta", "indictrans2")



