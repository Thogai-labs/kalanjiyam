import tempfile

import fitz

import kalanjiyam.queries as q
import kalanjiyam.tasks.projects as projects
import kalanjiyam.tasks.utils
from kalanjiyam.utils.storage import get_storage, page_image_key, pdf_key


def _create_sample_pdf(output_path: str, num_pages: int):
    """Create a toy PDF with 10 pages."""
    doc = fitz.open()
    for i in range(1, num_pages + 1):
        page = doc.new_page()
        where = fitz.Point(50, 50)
        page.insert_text(where, f"This is page {i}", fontsize=30)
    doc.save(output_path)


def test_create_project_inner(flask_app):
    with flask_app.app_context():
        project = q.project("cool-project")
        assert project is None

        f = tempfile.NamedTemporaryFile()
        _create_sample_pdf(f.name, num_pages=10)

        source_pdf_key = pdf_key("cool-project")
        get_storage().save(source_pdf_key, f.name)

        projects.create_project_inner(
            display_title="Cool project",
            pdf_key=source_pdf_key,
            app_environment=flask_app.config["KALANJIYAM_ENVIRONMENT"],
            creator_id=1,
            task_status=kalanjiyam.tasks.utils.LocalTaskStatus(),
        )

        project = q.project("cool-project")
        assert project
        assert len(project.pages) == 10
        # Page images land in storage under the project's key prefix.
        storage = get_storage()
        assert storage.exists(page_image_key("cool-project", "1"))
        assert storage.exists(page_image_key("cool-project", "10"))
        # Source PDF is deleted after page extraction to save space, and its size is saved in DB
        assert not storage.exists(source_pdf_key)
        assert project.extracted_metadata is not None
        assert project.extracted_metadata["source_file"]["size_bytes"] > 0
        assert project.extracted_metadata["source_file"]["deleted_after_extraction"] is True
