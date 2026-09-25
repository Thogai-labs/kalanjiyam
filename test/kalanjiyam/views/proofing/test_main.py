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


def test_index_append_ajax(client):
    resp = client.get(
        "/proofing/?append=1&page=1&per_page=10",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert "X-Total-Projects" in resp.headers
    assert "X-Total-Pages" in resp.headers
    assert "X-Current-Page" in resp.headers
    assert "Access-Control-Expose-Headers" in resp.headers
    assert "X-Total-Projects" in resp.headers["Access-Control-Expose-Headers"]
    # When append=1, only card elements are returned, not the outer folder breadcrumb or sentinel
    assert "infinite-scroll-sentinel" not in resp.text

    # Also test append=1 without X-Requested-With header (e.g. reverse proxy stripping it)
    resp_no_header = client.get("/proofing/?append=1&page=1&per_page=10")
    assert resp_no_header.status_code == 200
    assert "X-Total-Projects" in resp_no_header.headers
    assert "infinite-scroll-sentinel" not in resp_no_header.text


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


def test_admin_dashboard_moderator_required(client):
    resp = client.get("/proofing/admin/dashboard/")
    assert resp.status_code in (302, 403)


def test_admin_dashboard(moderator_client):
    resp = moderator_client.get("/proofing/admin/dashboard/")
    assert resp.status_code == 200
    assert "Proofing Analytics" in resp.text
    assert "Revisions" in resp.text
    assert "Contributors" in resp.text


def test_admin_dashboard_redis_cache(moderator_client, monkeypatch):
    import json
    from unittest.mock import MagicMock

    import redis

    mock_redis = MagicMock()
    mock_redis.get.return_value = None
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: mock_redis)

    resp = moderator_client.get("/proofing/admin/dashboard/")
    assert resp.status_code == 200
    assert mock_redis.setex.called

    # When cache is populated, it returns cached template values directly
    mock_redis.get.return_value = json.dumps(
        {
            "num_revisions_30d": 99,
            "num_contributors_30d": 42,
            "num_revisions_7d": 10,
            "num_contributors_7d": 5,
            "num_revisions_1d": 2,
            "num_contributors_1d": 1,
        }
    ).encode("utf-8")
    resp_cached = moderator_client.get("/proofing/admin/dashboard/")
    assert resp_cached.status_code == 200
    assert "99" in resp_cached.text
    assert "42" in resp_cached.text


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


def test_create_and_manage_folders(rama_client):
    """Test creating folders and subfolders via POST /proofing/folders/create."""
    from kalanjiyam import database as db
    from kalanjiyam.queries import get_session

    session = get_session()

    # 1. Create root folder "Literature"
    resp = rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "Literature", "parent_folder": ""},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["folder"] == "Literature"
    assert data["path"] == "Literature"
    assert data["name"] == "Literature"

    # Verify ProofFolder in database
    folder_rec = session.query(db.ProofFolder).filter_by(path="Literature").first()
    assert folder_rec is not None
    assert folder_rec.name == "Literature"
    assert folder_rec.parent_path == ""

    # 2. Create subfolder "Poetry" under "Literature"
    resp = rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "Poetry", "parent_folder": "Literature"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["folder"] == "Literature/Poetry"
    assert data["path"] == "Literature/Poetry"
    assert data["name"] == "Poetry"

    sub_rec = session.query(db.ProofFolder).filter_by(path="Literature/Poetry").first()
    assert sub_rec is not None
    assert sub_rec.name == "Poetry"
    assert sub_rec.parent_path == "Literature"

    # 3. Duplicate creation returns 400
    resp = rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "Poetry", "parent_folder": "Literature"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 400

    # 4. Invalid empty name returns 400
    resp = rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "   ", "parent_folder": "Literature"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 400


def test_empty_folder_rendered_in_workspace(client, rama_client):
    """Test that an empty folder appears at root for its creator/org, and is isolated from others."""
    # Create empty folder "Unpublished" as rama
    resp = rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "Unpublished", "parent_folder": ""},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    # GET root index as creator (rama)
    resp = rama_client.get("/proofing/")
    assert resp.status_code == 200
    assert "projects-results-container" in resp.text
    assert "Unpublished" in resp.text
    assert "0 projects" in resp.text
    assert resp.headers.get("Cache-Control") is not None

    # AJAX GET immediately returns the created folder HTML
    resp_ajax = rama_client.get(
        "/proofing/",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_ajax.status_code == 200
    assert "Unpublished" in resp_ajax.text
    assert "0 projects" in resp_ajax.text
    assert resp_ajax.headers.get("Cache-Control") is not None

    # GET inside the folder as creator (rama)
    resp = rama_client.get("/proofing/?folder=Unpublished")
    assert resp.status_code == 200
    assert "This folder is empty" in resp.text
    assert "Unpublished" in resp.text

    # Another user / anonymous client should NOT see rama's folder
    resp_other = client.get("/proofing/")
    assert resp_other.status_code == 200
    assert "Unpublished" not in resp_other.text


def test_rename_folder_cascade(rama_client):
    """Test renaming a folder cascades to subfolders and project records."""
    from kalanjiyam import database as db
    from kalanjiyam.queries import get_session

    session = get_session()

    # Create folder "Ancient" and subfolder "Ancient/Vedic"
    rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "Ancient", "parent_folder": ""},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "Vedic", "parent_folder": "Ancient"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    # Move test-project into "Ancient/Vedic"
    resp = rama_client.post(
        "/proofing/test-project/move-folder",
        data={"folder": "Ancient/Vedic"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    # Rename "Ancient" -> "Classical"
    resp = rama_client.post(
        "/proofing/folders/rename",
        data={"old_path": "Ancient", "new_name": "Classical"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["new_path"] == "Classical"

    session.expire_all()
    # Ancient is gone, Classical exists
    assert session.query(db.ProofFolder).filter_by(path="Ancient").first() is None
    assert session.query(db.ProofFolder).filter_by(path="Classical").first() is not None
    # Ancient/Vedic is now Classical/Vedic
    assert session.query(db.ProofFolder).filter_by(path="Ancient/Vedic").first() is None
    sub = session.query(db.ProofFolder).filter_by(path="Classical/Vedic").first()
    assert sub is not None
    assert sub.parent_path == "Classical"

    # Project folder is updated to Classical/Vedic
    project = session.query(db.Project).filter_by(slug="test-project").first()
    assert project.folder == "Classical/Vedic"


def test_delete_folder_safely_moves_manuscripts(rama_client):
    """Test deleting a folder deletes ProofFolder and safely moves manuscripts to parent."""
    from kalanjiyam import database as db
    from kalanjiyam.queries import get_session

    session = get_session()

    # Create "CategoryX/SubY"
    rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "CategoryX", "parent_folder": ""},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    rama_client.post(
        "/proofing/folders/create",
        data={"folder_name": "SubY", "parent_folder": "CategoryX"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    # Move test-project into "CategoryX/SubY"
    rama_client.post(
        "/proofing/test-project/move-folder",
        data={"folder": "CategoryX/SubY"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    # Delete "CategoryX/SubY" -> project should move to "CategoryX"
    resp = rama_client.post(
        "/proofing/folders/delete",
        data={"folder_path": "CategoryX/SubY"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["parent_folder"] == "CategoryX"

    session.expire_all()
    assert (
        session.query(db.ProofFolder).filter_by(path="CategoryX/SubY").first() is None
    )
    project = session.query(db.Project).filter_by(slug="test-project").first()
    assert project.folder == "CategoryX"

    # Delete "CategoryX" -> project should move to "" (root)
    resp = rama_client.post(
        "/proofing/folders/delete",
        data={"folder_path": "CategoryX"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["parent_folder"] == ""

    session.expire_all()
    assert session.query(db.ProofFolder).filter_by(path="CategoryX").first() is None
    project = session.query(db.Project).filter_by(slug="test-project").first()
    assert project.folder == ""


def test_workspace_pagination_at_root_and_in_folders(superadmin_client):
    """Test pagination and infinite-scroll appending works properly at root and inside folders."""
    from kalanjiyam import database as db
    from kalanjiyam.queries import get_session

    session = get_session()
    board = session.query(db.Board).first()
    board_id = board.id if board else 1

    # Create 5 root projects
    for i in range(5):
        p = db.Project(
            slug=f"root-proj-{i}",
            display_title=f"Root Book {i}",
            folder="",
            board_id=board_id,
            is_publicly_viewable=True,
        )
        session.add(p)

    # Create folder Fiction with 4 direct projects and subfolder Fiction/Fantasy with 2 projects
    superadmin_client.post(
        "/proofing/folders/create",
        json={"name": "Fiction", "parent_folder": ""},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    superadmin_client.post(
        "/proofing/folders/create",
        json={"name": "Fantasy", "parent_folder": "Fiction"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    for i in range(4):
        p = db.Project(
            slug=f"fiction-proj-{i}",
            display_title=f"Fiction Story {i}",
            folder="Fiction",
            board_id=board_id,
            is_publicly_viewable=True,
        )
        session.add(p)

    for i in range(2):
        p = db.Project(
            slug=f"fantasy-proj-{i}",
            display_title=f"Fantasy Epic {i}",
            folder="Fiction/Fantasy",
            board_id=board_id,
            is_publicly_viewable=True,
        )
        session.add(p)

    session.commit()

    # 1. Root pagination: total direct root projects should be 5 + 1 (existing test-project) = 6
    resp_root_p1 = superadmin_client.get(
        "/proofing/?per_page=2",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_root_p1.status_code == 200
    assert resp_root_p1.headers.get("X-Total-Projects") == "6"
    assert resp_root_p1.headers.get("X-Total-Pages") == "3"
    assert resp_root_p1.headers.get("X-Current-Page") == "1"
    assert "Fiction" in resp_root_p1.text  # Subfolder listed
    assert "infinite-scroll-sentinel" in resp_root_p1.text
    assert "Load more" in resp_root_p1.text

    resp_root_p2 = superadmin_client.get(
        "/proofing/?page=2&per_page=2&append=1",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_root_p2.status_code == 200
    assert resp_root_p2.headers.get("X-Current-Page") == "2"
    assert resp_root_p2.headers.get("X-Total-Projects") == "6"
    assert resp_root_p2.headers.get("X-Total-Pages") == "3"
    assert "infinite-scroll-sentinel" not in resp_root_p2.text
    assert "Root Book" in resp_root_p2.text

    # 2. Folder pagination: inside Fiction, 4 direct projects, subfolder Fantasy
    resp_fict_p1 = superadmin_client.get(
        "/proofing/?folder=Fiction&per_page=2",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_fict_p1.status_code == 200
    assert resp_fict_p1.headers.get("X-Total-Projects") == "4"
    assert resp_fict_p1.headers.get("X-Total-Pages") == "2"
    assert resp_fict_p1.headers.get("X-Current-Page") == "1"
    assert "Projects in this folder (4)" in resp_fict_p1.text
    assert "Fantasy" in resp_fict_p1.text  # Subfolder listed
    assert "infinite-scroll-sentinel" in resp_fict_p1.text

    resp_fict_p2 = superadmin_client.get(
        "/proofing/?folder=Fiction&page=2&per_page=2&append=1",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_fict_p2.status_code == 200
    assert resp_fict_p2.headers.get("X-Current-Page") == "2"
    assert resp_fict_p2.headers.get("X-Total-Projects") == "4"
    assert resp_fict_p2.headers.get("X-Total-Pages") == "2"
    assert "infinite-scroll-sentinel" not in resp_fict_p2.text
    assert "Fiction Story" in resp_fict_p2.text

    # 3. Search inside Fiction: searches Fiction + Fiction/Fantasy (spans subfolders)
    resp_search = superadmin_client.get(
        "/proofing/?folder=Fiction&q=Epic",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_search.status_code == 200
    assert resp_search.headers.get("X-Total-Projects") == "2"
    assert "Search Results (2)" in resp_search.text

    # 4. Search with no results shows friendly empty state
    resp_empty = superadmin_client.get(
        "/proofing/?q=nonexistent_query_xyz",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp_empty.status_code == 200
    assert resp_empty.headers.get("X-Total-Projects") == "0"
    assert "No matching projects" in resp_empty.text


def test_project_stats_sql_aggregation(superadmin_client):
    """Test SQL aggregation properly calculates page counts and progress without loading ORM page trees."""
    from kalanjiyam import database as db
    from kalanjiyam.queries import get_session
    from kalanjiyam.enums import SitePageStatus

    session = get_session()
    board = session.query(db.Board).first()
    board_id = board.id if board else 1

    r0_status = session.query(db.PageStatus).filter_by(name=SitePageStatus.R0.value).first()
    r1_status = session.query(db.PageStatus).filter_by(name=SitePageStatus.R1.value).first()

    # Create project with 4 pages (2 R0, 2 R1 -> 50% progress)
    p = db.Project(
        slug="stats-agg-test-project",
        display_title="Stats Agg Test Project",
        folder="StatsFolder",
        board_id=board_id,
        is_publicly_viewable=True,
    )
    session.add(p)
    session.commit()

    if r0_status and r1_status:
        page1 = db.Page(slug="p1", order=1, project_id=p.id, status_id=r0_status.id)
        page2 = db.Page(slug="p2", order=2, project_id=p.id, status_id=r0_status.id)
        page3 = db.Page(slug="p3", order=3, project_id=p.id, status_id=r1_status.id)
        page4 = db.Page(slug="p4", order=4, project_id=p.id, status_id=r1_status.id)
        session.add_all([page1, page2, page3, page4])
        session.commit()

    resp = superadmin_client.get(
        "/proofing/?folder=StatsFolder",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200
    assert "Stats Agg Test Project" in resp.text
    if r0_status and r1_status:
        assert "4 pages" in resp.text
        assert "50%" in resp.text

