import pytest

from kalanjiyam.views.proofing import main


@pytest.mark.parametrize(
    "path,expected",
    [
        ("book.pdf", True),
        ("book.PDF", True),
        ("book.docx", True),
        ("book.doc", True),
        ("scan.jpg", True),
        ("scan.JPG", True),
        ("scan.jpeg", True),
        ("scan.JPEG", True),
        ("scan.png", True),
        ("scan.webp", True),
        ("book.djvu", False),
        ("book.epub", False),
        ("archive.zip", False),
        ("script.sh", False),
    ],
)
def test_is_allowed_document_file(path, expected):
    assert main._is_allowed_document_file(path) == expected


def test_natural_sort_key():
    files = ["page_10.jpg", "page_1.jpg", "page_2.jpg", "page_20.jpg"]
    sorted_files = sorted(files, key=main._natural_sort_key)
    assert sorted_files == ["page_1.jpg", "page_2.jpg", "page_10.jpg", "page_20.jpg"]


def test_index(client):
    resp = client.get("/proofing/")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_index_pagination(client):
    resp = client.get("/proofing/?page=1&per_page=5")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_index_search(client):
    resp = client.get("/proofing/?q=test&sort=title&order=asc")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_index_ajax_xhr(client):
    resp = client.get("/proofing/", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert "X-Total-Projects" in resp.headers


def test_index_ajax_search(client):
    resp = client.get(
        "/proofing/?q=test&sort=title&order=asc",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert "X-Total-Projects" in resp.headers


def test_index_ajax_param(client):
    resp = client.get("/proofing/?ajax=1&page=1&per_page=10")
    assert resp.status_code == 200
    assert "X-Total-Projects" in resp.headers


def test_index_issue_filter(client):
    resp = client.get("/proofing/?issue=Blurry&issue=Torn")
    assert resp.status_code == 200
    assert ">Proofing<" in resp.text


def test_index_issue_filter_ajax(client):
    resp = client.get(
        "/proofing/?issue=Shmushing",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert "X-Total-Projects" in resp.headers


def test_beginners_guide(client):
    resp = client.get("/proofing/help/beginners-guide")
    assert "Beginner's Guide" in resp.text


def test_complete_guide(client):
    resp = client.get("/proofing/help/complete-guide")
    assert "Complete Proofing Guide" in resp.text


def test_editor_guide(client):
    resp = client.get("/proofing/help/editor-guide")
    assert "Editor Manual" in resp.text


def test_recent_changes(client):
    resp = client.get("/proofing/recent-changes")
    assert "Recent changes" in resp.text


def test_recent_changes_pagination(client):
    resp = client.get("/proofing/recent-changes?page=1&per_page=10")
    assert resp.status_code == 200
    assert "Recent Changes" in resp.text


def test_recent_changes_date_filter(client):
    resp = client.get("/proofing/recent-changes?date=2026-08-15")
    assert resp.status_code == 200
    assert "Filtered by date" in resp.text
    assert "2026-08-15" in resp.text


def test_create_project__unauth(client):
    resp = client.get("/proofing/create-project")
    assert resp.status_code in (200, 302)


def test_create_project__auth(rama_client):
    resp = rama_client.get("/proofing/create-project")
    assert resp.status_code == 200


def test_create_project_with_images_post(rama_client):
    """Test project creation with multiple JPG images."""
    import io
    from unittest.mock import Mock, patch

    import kalanjiyam.database as db
    import kalanjiyam.queries as q

    session = q.get_session()
    tenant = q.get_or_create_open_tenant()
    user = session.query(db.User).filter_by(username="u-basic").first()
    user.organization_id = tenant.id
    session.commit()

    data = {
        "pdf_source": "local",
        "local_title": "Folio Manuscript",
        "license": "public",
        "local_file": [
            (io.BytesIO(b"dummy image 1"), "page_02.jpg"),
            (io.BytesIO(b"dummy image 2"), "page_01.jpg"),
        ],
    }

    with patch("kalanjiyam.utils.storage.LocalStorage.save"), \
         patch("kalanjiyam.tasks.projects.create_project.delay") as mock_task:
        mock_task.return_value = Mock(id="mock-create-task-id", status="PENDING")

        resp = rama_client.post(
            "/proofing/create-project",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200

        mock_task.assert_called_once()
        kwargs = mock_task.call_args[1]
        assert kwargs["display_title"] == "Folio Manuscript"
        assert kwargs["pdf_key"] is None
        assert kwargs["docx_key"] is None
        assert kwargs["image_keys"] is not None
        assert len(kwargs["image_keys"]) == 2
        # Natural sorting ensures page_01.jpg is processed before page_02.jpg
        assert "1.jpg" in kwargs["image_keys"][0]
        assert "2.jpg" in kwargs["image_keys"][1]


def test_create_project_with_mixed_files_fails(rama_client):
    """Test that mixing PDF with JPG images fails validation."""
    import io

    data = {
        "pdf_source": "local",
        "local_title": "Invalid Mixed Project",
        "license": "public",
        "local_file": [
            (io.BytesIO(b"pdf content"), "book.pdf"),
            (io.BytesIO(b"image content"), "page.jpg"),
        ],
    }

    resp = rama_client.post(
        "/proofing/create-project",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "When uploading multiple files, all files must be images" in resp.text


def test_talk(client):
    resp = client.get("/proofing/talk")
    assert "Talk" in resp.text


def test_help_index(client):
    resp = client.get("/proofing/help")
    assert resp.status_code == 200
    assert "How can we help you?" in resp.text


def test_create_project_status_images_and_pdf(client):
    """Test create_project_status renders doc_type specific text for images and pdf."""
    from unittest.mock import Mock, patch

    # 1. Status PENDING for doc_type="images"
    mock_async_images = Mock()
    mock_async_images.status = "PENDING"
    mock_async_images.info = {"current": 0, "total": 2, "doc_type": "images"}

    with patch("kalanjiyam.tasks.projects.create_project.AsyncResult", return_value=mock_async_images):
        resp = client.get("/proofing/status/dummy-images-task-id")
        assert resp.status_code == 200
        assert "Starting image processing..." in resp.text
        assert "Waiting for the server to start processing your images." in resp.text
        assert "Starting PDF extraction..." not in resp.text

    # 2. Status PROGRESS for doc_type="images"
    mock_async_images.status = "PROGRESS"
    with patch("kalanjiyam.tasks.projects.create_project.AsyncResult", return_value=mock_async_images):
        resp = client.get("/proofing/status/dummy-images-task-id")
        assert resp.status_code == 200
        assert "Processing Images..." in resp.text
        assert "We are optimizing and preparing your images for proofing." in resp.text
        assert "Splitting PDF..." not in resp.text

    # 3. Status PENDING for doc_type="pdf" (default)
    mock_async_pdf = Mock()
    mock_async_pdf.status = "PENDING"
    mock_async_pdf.info = {"current": 0, "total": 10, "doc_type": "pdf"}

    with patch("kalanjiyam.tasks.projects.create_project.AsyncResult", return_value=mock_async_pdf):
        resp = client.get("/proofing/status/dummy-pdf-task-id")
        assert resp.status_code == 200
        assert "Starting PDF extraction..." in resp.text
        assert "Waiting for the server to start processing your file." in resp.text
        assert "Starting image processing..." not in resp.text
