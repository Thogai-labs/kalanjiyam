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


def test_index_sorting_and_modes(client):
    for sort in ("title", "created"):
        for order in ("asc", "desc"):
            resp = client.get(f"/proofing/?sort={sort}&order={order}")
            assert resp.status_code == 200

    for mode in ("ocr", "manual", "all"):
        resp = client.get(f"/proofing/?mode={mode}")
        assert resp.status_code == 200


def test_index_org_filter(client):
    resp = client.get("/proofing/?org=non-existent-org")
    assert resp.status_code == 200
    resp_ajax = client.get(
        "/proofing/?org=non-existent-org",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_ajax.status_code == 200
    assert resp_ajax.headers.get("X-Total-Projects") == "0"


def test_index_pagination_bounds(client):
    resp = client.get("/proofing/?page=999&per_page=10")
    assert resp.status_code == 200


def test_index_folder_and_tag_filters(client):
    # Check that query with folder works
    resp = client.get("/proofing/?folder=Philosophy/Nyaya")
    assert resp.status_code == 200

    resp_ajax = client.get(
        "/proofing/?folder=Philosophy/Nyaya",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_ajax.status_code == 200
    assert "X-Total-Projects" in resp_ajax.headers

    # Check that query with tag works
    resp_tag = client.get("/proofing/?tag=Manuscript")
    assert resp_tag.status_code == 200

    resp_tag_ajax = client.get(
        "/proofing/?tag=Manuscript",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_tag_ajax.status_code == 200
    assert "X-Total-Projects" in resp_tag_ajax.headers


def test_project_model_folder_and_tags():
    from kalanjiyam import database as db
    proj = db.Project(
        slug="folder-test-proj",
        display_title="Folder Test Project",
        folder="  Philosophy // Nyaya  ",
        tags=["Sanskrit", "logic", "Sanskrit"],
    )
    assert proj.folder_path == "Philosophy/Nyaya"
    assert proj.folder_parts == ["Philosophy", "Nyaya"]
    assert proj.tag_list == ["Sanskrit", "logic"]


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


def test_recent_changes_filters(client):
    resp = client.get(
        "/proofing/recent-changes?q=test&start_date=2026-08-01&end_date=2026-08-31&range=30d"
    )
    assert resp.status_code == 200
    assert "Recent Changes" in resp.text
    assert "Activity Stream" in resp.text


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

    with (
        patch("kalanjiyam.utils.storage.LocalStorage.save"),
        patch("kalanjiyam.tasks.projects.create_project.delay") as mock_task,
    ):
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
    assert (
        "When uploading multiple files, all files must be either all images (.jpg, .jpeg, .png, .webp) or all PDFs (.pdf)."
        in resp.text
    )


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

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_images,
    ):
        resp = client.get("/proofing/status/dummy-images-task-id")
        assert resp.status_code == 200
        assert "Starting image processing..." in resp.text
        assert "Waiting for the server to start processing your images." in resp.text
        assert "Starting PDF extraction..." not in resp.text

    # 2. Status PROGRESS for doc_type="images"
    mock_async_images.status = "PROGRESS"
    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_images,
    ):
        resp = client.get("/proofing/status/dummy-images-task-id")
        assert resp.status_code == 200
        assert "Processing Images..." in resp.text
        assert "We are optimizing and preparing your images for proofing." in resp.text
        assert "Splitting PDF..." not in resp.text

    # 3. Status PENDING for doc_type="pdf" (default)
    mock_async_pdf = Mock()
    mock_async_pdf.status = "PENDING"
    mock_async_pdf.info = {"current": 0, "total": 10, "doc_type": "pdf"}

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_pdf,
    ):
        resp = client.get("/proofing/status/dummy-pdf-task-id")
        assert resp.status_code == 200
        assert "Starting PDF extraction..." in resp.text
        assert "Waiting for the server to start processing your file." in resp.text
        assert "Starting image processing..." not in resp.text


def test_filename_to_project_title():
    assert main._filename_to_project_title("scan_01.jpg") == "Scan 01"
    assert main._filename_to_project_title("page-12-intro.png") == "Page 12 intro"
    assert main._filename_to_project_title("my_manuscript.webp") == "My manuscript"
    assert main._filename_to_project_title("1.jpg") == "1"
    assert main._filename_to_project_title(".jpg", fallback_index=3) == "Project 3"


def test_is_group_images_enabled():
    # 1. group_images explicitly passed
    assert main.is_group_images_enabled({"group_images": "1"}) is True
    assert main.is_group_images_enabled({"group_images": "true"}) is True
    assert main.is_group_images_enabled({"group_images": "0"}) is False
    assert main.is_group_images_enabled({"group_images": "false"}) is False

    # 2. Form submitted with unchecked checkbox (group_images_submitted present, group_images absent)
    assert main.is_group_images_enabled({"group_images_submitted": "1"}) is False

    # 3. Neither present (backward compatibility / default)
    assert main.is_group_images_enabled({}) is True


def test_create_project_with_images_post_separate_projects(rama_client):
    """Test project creation with multiple JPG images when group_images is unchecked."""
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
        "license": "public",
        "group_images_submitted": "1",  # Checkbox was unchecked
        "local_file": [
            (io.BytesIO(b"dummy image 1"), "chapter_one.jpg"),
            (io.BytesIO(b"dummy image 2"), "chapter_two.jpg"),
        ],
    }

    with (
        patch("kalanjiyam.utils.storage.LocalStorage.save"),
        patch(
            "kalanjiyam.tasks.projects.create_batch_image_projects.apply_async"
        ) as mock_batch_task,
    ):
        mock_batch_task.return_value = Mock(id="mock-batch-task-id", status="PENDING")

        resp = rama_client.post(
            "/proofing/create-project",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200

        mock_batch_task.assert_called_once()
        call_kwargs = mock_batch_task.call_args.kwargs
        assert call_kwargs["priority"] == 3
        projects_data = call_kwargs["kwargs"]["projects_data"]
        assert len(projects_data) == 2
        assert projects_data[0]["display_title"] == "Chapter one"
        assert projects_data[0]["slug"] == "chapter-one"
        assert projects_data[1]["display_title"] == "Chapter two"
        assert projects_data[1]["slug"] == "chapter-two"


def test_create_project_with_images_post_duplicate_slugs_fails(rama_client):
    """Test that uploading multiple images producing duplicate project slugs fails validation."""
    import io

    data = {
        "pdf_source": "local",
        "license": "public",
        "group_images_submitted": "1",  # Checkbox unchecked
        "local_file": [
            (io.BytesIO(b"img 1"), "scan_01.jpg"),
            (io.BytesIO(b"img 2"), "scan-01.png"),  # Both slugify to 'scan-01'
        ],
    }

    resp = rama_client.post(
        "/proofing/create-project",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "Cannot create projects due to naming conflicts" in resp.text


def test_create_project_with_multiple_pdfs_post(rama_client):
    """Test that uploading multiple PDFs creates separate projects and dispatches Celery task with PRIORITY_BATCH."""
    import io
    from unittest.mock import Mock, patch

    import kalanjiyam.database as db
    import kalanjiyam.queries as q
    from kalanjiyam.tasks import PRIORITY_BATCH

    session = q.get_session()
    tenant = q.get_or_create_open_tenant()
    user = session.query(db.User).filter_by(username="u-basic").first()
    user.organization_id = tenant.id
    session.commit()

    data = {
        "pdf_source": "local",
        "license": "public",
        "local_file": [
            (io.BytesIO(b"%PDF-1.4 dummy 1"), "volume_one.pdf"),
            (io.BytesIO(b"%PDF-1.4 dummy 2"), "volume_two.pdf"),
        ],
    }

    with (
        patch("kalanjiyam.utils.storage.LocalStorage.save"),
        patch(
            "kalanjiyam.tasks.projects.create_batch_pdf_projects.apply_async"
        ) as mock_batch_task,
    ):
        mock_batch_task.return_value = Mock(id="mock-batch-pdf-id", status="PENDING")

        resp = rama_client.post(
            "/proofing/create-project",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200

        mock_batch_task.assert_called_once()
        call_kwargs = mock_batch_task.call_args.kwargs
        assert call_kwargs["priority"] == PRIORITY_BATCH
        projects_data = call_kwargs["kwargs"]["projects_data"]
        assert len(projects_data) == 2
        assert projects_data[0]["display_title"] == "Volume one"
        assert projects_data[0]["slug"] == "volume-one"
        assert projects_data[1]["display_title"] == "Volume two"
        assert projects_data[1]["slug"] == "volume-two"


def test_create_project_status_batch_images(client):
    """Test create_project_status renders batch_images specific text."""
    from unittest.mock import Mock, patch

    # 1. Status PROGRESS for doc_type="batch_images"
    mock_async_batch = Mock()
    mock_async_batch.status = "PROGRESS"
    mock_async_batch.info = {"current": 2, "total": 5, "doc_type": "batch_images"}

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_batch,
    ):
        resp = client.get("/proofing/status/dummy-batch-task-id")
        assert resp.status_code == 200
        assert "Creating Projects..." in resp.text
        assert (
            'Created <span class="text-slate-900">2</span> of <span class="text-slate-900">5</span> projects'
            in resp.text
        )

    # 2. Status SUCCESS for doc_type="batch_images"
    mock_async_batch.status = "SUCCESS"
    mock_async_batch.info = {
        "current": 5,
        "total": 5,
        "slug": None,
        "doc_type": "batch_images",
    }

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_batch,
    ):
        resp = client.get("/proofing/status/dummy-batch-task-id")
        assert resp.status_code == 200
        assert "Projects Created!" in resp.text
        assert (
            "All 5 projects have been created and are ready for proofreading."
            in resp.text
        )
        assert "View Projects on Dashboard" in resp.text


def test_create_project_status_batch_pdfs(client):
    """Test create_project_status renders batch_pdfs specific text."""
    from unittest.mock import Mock, patch

    # 1. Status PROGRESS for doc_type="batch_pdfs"
    mock_async_batch = Mock()
    mock_async_batch.status = "PROGRESS"
    mock_async_batch.info = {"current": 2, "total": 5, "doc_type": "batch_pdfs"}

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_batch,
    ):
        resp = client.get("/proofing/status/dummy-batch-pdf-id")
        assert resp.status_code == 200
        assert "Processing PDFs..." in resp.text
        assert (
            "We are converting PDF pages to images and creating projects." in resp.text
        )
        assert (
            'Created <span class="text-slate-900">2</span> of <span class="text-slate-900">5</span> projects'
            in resp.text
        )

    # 2. Status SUCCESS for doc_type="batch_pdfs"
    mock_async_batch.status = "SUCCESS"
    mock_async_batch.info = {
        "current": 5,
        "total": 5,
        "slug": None,
        "doc_type": "batch_pdfs",
    }

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_batch,
    ):
        resp = client.get("/proofing/status/dummy-batch-pdf-id")
        assert resp.status_code == 200
        assert "Projects Created!" in resp.text
        assert (
            "All 5 projects have been created and are ready for proofreading."
            in resp.text
        )
        assert "View Projects on Dashboard" in resp.text

    # 3. Status PENDING for doc_type="batch_pdfs"
    mock_async_batch.status = "PENDING"
    mock_async_batch.info = {"doc_type": "batch_pdfs"}

    with patch(
        "kalanjiyam.tasks.projects.create_project.AsyncResult",
        return_value=mock_async_batch,
    ):
        resp = client.get("/proofing/status/dummy-batch-pdf-id")
        assert resp.status_code == 200
        assert "Queueing Task..." in resp.text
        assert "Waiting for the server to start processing your PDFs." in resp.text
        assert "Starting batch PDF processing..." in resp.text


def test_move_folder__ajax_and_redirect(rama_client):
    """Test move_folder endpoint with AJAX and normal POST."""
    resp = rama_client.post(
        "/proofing/test-project/move-folder",
        data={"folder": "Manuscripts/Tamil"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["folder"] == "Manuscripts/Tamil"
    assert data["slug"] == "test-project"

    # Move back to root
    resp = rama_client.post(
        "/proofing/test-project/move-folder",
        data={"folder": ""},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["folder"] == ""

    # Unknown slug returns 404
    resp = rama_client.post(
        "/proofing/nonexistent-project-slug/move-folder",
        data={"folder": "SomeFolder"},
    )
    assert resp.status_code == 404


def test_index_folder_and_projects_layout(client):
    """Test index template renders horizontal items layout, 3-dots context menu, and move modal."""
    resp = client.get("/proofing/")
    assert resp.status_code == 200
    assert "openMoveModal" in resp.text
    assert "Move to folder…" in resp.text
    assert "showMoveModal" in resp.text

