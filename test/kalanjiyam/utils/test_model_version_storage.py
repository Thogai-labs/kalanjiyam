"""Tests for model versioning and .json.gz storage naming."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from kalanjiyam.utils.document_storage import (
    derive_revision_tag,
    save_revision_document,
    load_revision_document,
)
from kalanjiyam.utils.storage import MemoryStorage, revision_document_key


def test_derive_revision_tag_ocr_model():
    # Test OCR model tag derivation
    page_ver = SimpleNamespace(version_key="ocr:google")
    rev = SimpleNamespace(page_version=page_ver, summary="OCR run", author=None, translations=None)
    assert derive_revision_tag(rev) == "ocr-google"

    page_ver_tess = SimpleNamespace(version_key="ocr:tesseract_manuscript")
    rev_tess = SimpleNamespace(page_version=page_ver_tess, summary="OCR run", author=None, translations=None)
    assert derive_revision_tag(rev_tess) == "ocr-tesseract-manuscript"


def test_derive_revision_tag_translation_model():
    # Test Translation model tag derivation with src and tgt languages
    page_ver = SimpleNamespace(version_key="translation:nayan:sa->en")
    rev = SimpleNamespace(page_version=page_ver, summary="Translation: nayan sa->en", author=None, translations=None)
    assert derive_revision_tag(rev) == "translation-nayan_sa-en"

    # Test via translations relation
    trans = SimpleNamespace(translation_engine="google", source_language="hi", target_language="ta")
    rev_trans = SimpleNamespace(page_version=None, summary="Translation run", author=None, translations=[trans])
    assert derive_revision_tag(rev_trans) == "translation-google_hi-ta"


def test_derive_revision_tag_user():
    # Test User edit tag derivation
    author = SimpleNamespace(username="john_doe")
    page_ver = SimpleNamespace(version_key="user:1")
    rev = SimpleNamespace(page_version=page_ver, summary="Proofreading edit", author=author, translations=None)
    assert derive_revision_tag(rev) == "user-john-doe"


def test_save_and_load_revision_document_with_timestamp(monkeypatch):
    storage = MemoryStorage()
    monkeypatch.setattr("kalanjiyam.utils.storage.get_storage", lambda: storage)

    project = SimpleNamespace(slug="my-book")
    page = SimpleNamespace(slug="page-1", revisions=[])
    group = SimpleNamespace(slug="my-org")
    project.groups = [group]

    page_ver = SimpleNamespace(version_key="ocr:google", version=1)
    rev = SimpleNamespace(
        id=10,
        project=project,
        page=page,
        page_version=page_ver,
        summary="OCR run (google)",
        author=None,
        translations=None,
        created=None,
    )
    page.revisions.append(rev)

    doc = {
        "content_format": "blocks",
        "blocks": [{"id": "b1", "content": "Sample text", "reading_order": 1}],
    }

    # Save revision document
    success = save_revision_document(rev, doc)
    assert success is True

    # Check key path in storage
    key = revision_document_key("my-book", "page-1", 1, tag="ocr-google", org_slug="my-org")
    assert storage.exists(key)

    # Verify timestamp is inside JSON payload
    loaded = storage.load_json_gz(key)
    assert isinstance(loaded, dict)
    assert "timestamp" in loaded
    assert loaded["blocks"][0]["content"] == "Sample text"

    # Load via load_revision_document
    loaded_via_func = load_revision_document(rev)
    assert loaded_via_func is not None
    assert loaded_via_func["timestamp"] == loaded["timestamp"]


def test_export_project_data_model_payload_filenames(monkeypatch):
    from config import create_config_only_app
    from kalanjiyam.admin import KalanjiyamIndexView

    storage = MemoryStorage()
    monkeypatch.setattr("kalanjiyam.utils.storage.get_storage", lambda: storage)
    monkeypatch.setattr("kalanjiyam.queries.get_session", lambda: MagicMock())

    project = SimpleNamespace(
        slug="test-proj",
        display_title="Test",
        print_title="Test",
        author="Author",
        editor="Editor",
        publisher="Pub",
        publication_year="2026",
        worldcat_link=None,
        description="Desc",
        notes="Notes",
        page_numbers=1,
        created_at=SimpleNamespace(isoformat=lambda: "2026-08-10T00:00:00Z"),
        updated_at=SimpleNamespace(isoformat=lambda: "2026-08-10T00:00:00Z"),
        genre_id=1,
        creator=SimpleNamespace(username="admin"),
        groups=[SimpleNamespace(slug="my-org")],
        board=None,
        pages=[],
    )

    page = SimpleNamespace(
        slug="1",
        order=1,
        version=1,
        page_width=100,
        page_height=200,
        status=SimpleNamespace(name="reviewed-0"),
        project=project,
        revisions=[],
    )
    project.pages.append(page)

    page_ver = SimpleNamespace(version_key="translation:nayan:sa->en", version=1)
    rev = SimpleNamespace(
        id=42,
        project=project,
        page=page,
        page_version=page_ver,
        summary="Translation: nayan sa->en",
        content="Hello",
        content_format="plain",
        author=SimpleNamespace(username="translator"),
        status=SimpleNamespace(name="reviewed-0"),
        created=SimpleNamespace(isoformat=lambda: "2026-08-10T12:00:00Z"),
        translations=[],
        document={"blocks": []},
    )
    page.revisions.append(rev)

    app = create_config_only_app("testing")
    with app.app_context():
        dummy_view = KalanjiyamIndexView()
        data = dummy_view._export_project_data(project)

    assert len(data["revisions"]) == 1
    rev_export = data["revisions"][0]
    assert rev_export["payload_filename"] == "translation-nayan_sa-en_v1.json"
    assert rev_export["translation_model"] == "nayan"
    assert rev_export["source_language"] == "sa"
    assert rev_export["target_language"] == "en"
    assert rev_export["document"]["timestamp"] == "2026-08-10T12:00:00Z"


def test_export_revision_payloads_as_plain_json(tmp_path, monkeypatch):
    import json
    from kalanjiyam.admin import _export_revision_payloads

    monkeypatch.setattr(
        "kalanjiyam.utils.document_storage.load_revision_document",
        lambda rev: {"blocks": [{"text": "Hello world"}]},
    )

    project = SimpleNamespace(slug="test-proj", pages=[])
    page = SimpleNamespace(slug="page-1", revisions=[])
    page_ver = SimpleNamespace(version_key="ocr:google", version=1)
    rev = SimpleNamespace(
        id=1,
        page_version=page_ver,
        summary="OCR run",
        author=None,
        translations=None,
        created=None,
    )
    page.revisions.append(rev)
    project.pages.append(page)

    files_dir = tmp_path / "files"
    _export_revision_payloads(project, files_dir)

    payload_file = files_dir / "revisions" / "page-1" / "ocr-google.json"
    assert payload_file.exists()
    content = json.loads(payload_file.read_text(encoding="utf-8"))
    assert content["blocks"][0]["text"] == "Hello world"

