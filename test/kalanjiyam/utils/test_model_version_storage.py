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
        data = dummy_view._export_project_data(project, include_pages=True)

    assert len(data["revisions"]) == 1
    rev_export = data["revisions"][0]
    assert rev_export["payload_filename"] == "translation-nayan_sa-en_v1.json"
    assert rev_export["translation_model"] == "nayan"
    assert rev_export["source_language"] == "sa"
    assert rev_export["target_language"] == "en"
    assert rev_export["document"]["timestamp"] == "2026-08-10T12:00:00Z"


def test_export_project_data_metadata_only(monkeypatch):
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

    project.extracted_metadata = {
        "content": {"summary": "Extracted summary of book"},
        "provenance": {"model": "gemini-1.5-pro", "status": "OK"},
    }
    mock_run = SimpleNamespace(
        id=1,
        status="COMPLETED",
        engine="gemini",
        model_name="gemini-1.5-pro",
        model_version="001",
        taxonomy_version="1.0",
        contract_version="1.0",
        windows_total=2,
        windows_completed=2,
        windows_failed=0,
        pages_total=1,
        pages_read=1,
        fields_filled=10,
        fields_total=15,
        avg_field_confidence=0.92,
        min_field_confidence=0.85,
        low_conf_field_count=0,
        evidence_spans=5,
        evidence_verified=5,
        evidence_verified_rate=1.0,
        avg_source_ocr_confidence=0.98,
        pages_without_confidence=0,
        total_prompt_tokens=1500,
        total_completion_tokens=400,
        total_engine_latency_ms=1200.0,
        total_extraction_latency_ms=1500.0,
        metadata_data_size_bytes=2048,
        error_message=None,
        created_at=SimpleNamespace(isoformat=lambda: "2026-08-10T01:00:00Z"),
        completed_at=SimpleNamespace(isoformat=lambda: "2026-08-10T01:02:00Z"),
    )
    project.metadata_extraction_runs = [mock_run]

    app = create_config_only_app("testing")
    with app.app_context():
        dummy_view = KalanjiyamIndexView()
        data = dummy_view._export_project_data(project)

    assert "metadata" in data
    assert data["metadata"]["slug"] == "test-proj"
    assert data["format_version"] == "3.0"
    assert data["organization_slug"] == "my-org"
    assert data["extracted_metadata"]["content"]["summary"] == "Extracted summary of book"
    assert len(data["metadata_extraction_runs"]) == 1
    assert data["metadata_extraction_runs"][0]["model_name"] == "gemini-1.5-pro"
    assert "pages" not in data
    assert "revisions" not in data
    assert "translations" not in data


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


def test_extract_and_import_project_metadata_only_with_files(tmp_path, monkeypatch):
    import zipfile
    import json
    import kalanjiyam.database as db
    from kalanjiyam.admin import KalanjiyamIndexView

    storage = MemoryStorage()
    monkeypatch.setattr("kalanjiyam.utils.storage.get_storage", lambda: storage)

    # Create export directory structure
    export_dir = tmp_path / "export_test"
    export_dir.mkdir()
    files_dir = export_dir / "files"
    files_dir.mkdir()
    pages_dir = files_dir / "pages"
    pages_dir.mkdir()
    revisions_dir = files_dir / "revisions" / "1"
    revisions_dir.mkdir(parents=True)

    # project_data.json with metadata and extracted metadata details (no pages, no revisions, no translations)
    project_data = {
        "format_version": "3.0",
        "organization_slug": "test-org",
        "metadata": {
            "slug": "imported-proj",
            "display_title": "Imported Project",
            "print_title": "Imported Project",
            "author": "Author",
            "editor": "Editor",
            "publisher": "Pub",
            "publication_year": "2026",
            "worldcat_link": None,
            "description": "Desc",
            "notes": "Notes",
            "page_numbers": 1,
            "created_at": "2026-08-10T00:00:00",
            "updated_at": "2026-08-10T00:00:00",
            "genre_id": None,
            "creator_username": None,
        },
        "extracted_metadata": {
            "content": {"summary": "Imported extracted summary"},
            "provenance": {"model": "gemini-1.5-pro", "status": "OK"},
        },
        "metadata_extraction_runs": [
            {
                "status": "COMPLETED",
                "engine": "gemini",
                "model_name": "gemini-1.5-pro",
                "created_at": "2026-08-10T01:00:00",
                "completed_at": "2026-08-10T01:02:00",
            }
        ],
    }
    (export_dir / "project_data.json").write_text(json.dumps(project_data), encoding="utf-8")
    (pages_dir / "1.jpg").write_bytes(b"dummy image data")
    (revisions_dir / "ocr-tesseract.json").write_text(
        json.dumps({"blocks": [{"text": "Page 1 OCR"}], "timestamp": "2026-08-10T12:00:00"}),
        encoding="utf-8",
    )

    zip_path = tmp_path / "test_export.zip"
    with zipfile.ZipFile(zip_path, "w") as zipf:
        zipf.write(export_dir / "project_data.json", "project_data.json")
        zipf.write(pages_dir / "1.jpg", "files/pages/1.jpg")
        zipf.write(revisions_dir / "ocr-tesseract.json", "files/revisions/1/ocr-tesseract.json")

    # Mock DB session
    session = MagicMock()
    # query().filter_by().first() returns None so project doesn't exist yet
    session.query.return_value.filter_by.return_value.first.return_value = None
    session.query.return_value.filter_by.return_value.all.return_value = []

    from config import create_config_only_app
    from kalanjiyam.models.archival import MetadataExtractionRun

    view = KalanjiyamIndexView()
    monkeypatch.setattr(view, "_get_or_create_page_status", lambda s, name: SimpleNamespace(id=1, name=name))
    monkeypatch.setattr("kalanjiyam.queries.organization_by_slug", lambda slug: None)

    app = create_config_only_app("testing")
    with app.app_context():
        result = view._extract_and_import_project(zip_path, session)

    assert result["metadata"]["slug"] == "imported-proj"
    # Project added to session with extracted_metadata
    added_objects = [call[0][0] for call in session.add.call_args_list]
    created_projects = [obj for obj in added_objects if isinstance(obj, db.Project)]
    assert len(created_projects) == 1
    assert created_projects[0].slug == "imported-proj"
    assert created_projects[0].extracted_metadata["content"]["summary"] == "Imported extracted summary"

    # MetadataExtractionRun added to session
    created_runs = [obj for obj in added_objects if isinstance(obj, MetadataExtractionRun)]
    assert len(created_runs) == 1
    assert created_runs[0].model_name == "gemini-1.5-pro"

    # Pages created from files/pages
    created_pages = [obj for obj in added_objects if isinstance(obj, db.Page)]
    assert len(created_pages) == 1
    assert created_pages[0].slug == "1"

    # Revisions created from files/revisions
    created_revs = [obj for obj in added_objects if isinstance(obj, db.Revision)]
    assert len(created_revs) == 1
    assert created_revs[0].summary == "Exported ocr-tesseract"


