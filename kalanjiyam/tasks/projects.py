"""Background tasks for proofing projects."""

import logging
import tempfile
from pathlib import Path

# NOTE: `fitz` is the internal package name for PyMuPDF. PyPI hosts another
# package called `fitz` (https://pypi.org/project/fitz/) that is completely
# unrelated to PDF parsing.
import fitz
from slugify import slugify

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.tasks import app
from kalanjiyam.tasks.utils import CeleryTaskStatus, TaskStatus
from kalanjiyam.utils.quotas import add_storage_usage_for_project
from kalanjiyam.utils.storage import Storage, get_storage, page_image_key
from config import create_config_only_app


def _split_pdf_into_pages(
    pdf_path: Path, slug: str, storage: Storage, task_status: TaskStatus
) -> int:
    """Split the given PDF into N .jpg images, one image per page.

    Each page image is saved to `storage` as it is rendered.

    :param pdf_path: local filesystem path to the PDF we should process.
    :param slug: the project slug, which determines the storage keys.
    :param storage: the storage backend to save page images to.
    :return: the page count, which we use downstream.
    """
    doc = fitz.open(pdf_path)
    task_status.progress(0, doc.page_count)
    with tempfile.TemporaryDirectory() as tmp_dir:
        for page in doc:
            n = page.number + 1
            pix = page.get_pixmap(dpi=200)
            tmp_path = Path(tmp_dir) / f"{n}.jpg"
            pix.pil_save(tmp_path, optimize=True)
            storage.save(page_image_key(slug, str(n)), tmp_path)
            tmp_path.unlink()
            task_status.progress(n, doc.page_count)
    return doc.page_count


def _add_project_to_database(
    display_title: str,
    slug: str,
    num_pages: int,
    creator_id: int,
    require_org: bool,
):
    """Create a project on the database.

    :param display_title: the project title
    :param num_pages: the number of pages in the project
    """

    logging.info(f"Creating project (slug = {slug}) ...")
    session = q.get_session()
    board = db.Board(title=f"{slug} discussion board")
    session.add(board)
    session.flush()

    project = db.Project(slug=slug, display_title=display_title, creator_id=creator_id)
    project.board_id = board.id
    session.add(project)
    session.flush()

    logging.info(f"Fetching project and status (slug = {slug}) ...")
    unreviewed = session.query(db.PageStatus).filter_by(name="reviewed-0").one()

    logging.info(f"Creating {num_pages} Page entries (slug = {slug}) ...")
    for n in range(1, num_pages + 1):
        session.add(
            db.Page(
                project_id=project.id,
                slug=str(n),
                order=n,
                status_id=unreviewed.id,
            )
        )
    creator = session.query(db.User).filter_by(id=creator_id).first()
    # Auto-assign projects to the creator's organization for tenant isolation.
    if creator and creator.organization_id:
        session.add(db.ProjectGroups(group_id=creator.organization_id, project_id=project.id))
    elif creator and require_org:
        raise ValueError("Project creator must belong to an organization.")
    session.commit()


def create_project_inner(
    *,
    display_title: str,
    pdf_key: str,
    app_environment: str,
    creator_id: int,
    task_status: TaskStatus,
):
    """Split the given PDF into pages and register the project on the database.

    We separate this function from `create_project` so that we can run this
    function in a non-Celery context (for example, in `cli.py`).

    :param display_title: the project's title.
    :param pdf_key: storage key of the source PDF.
    :param app_environment: the app environment, e.g. `"development"`.
    :param creator_id: the user that created this project.
    :param task_status: tracks progress on the task.
    """
    logging.info(f'Received upload task "{display_title}" for key {pdf_key}.')

    # Tasks must be idempotent. Exit if the project already exists.
    app = create_config_only_app(app_environment)
    with app.app_context():
        session = q.get_session()
        slug = slugify(display_title)
        project = session.query(db.Project).filter_by(slug=slug).first()

        if project:
            raise ValueError(
                f'Project "{display_title}" already exists. Please choose a different title.'
            )

        # The worker fetches the PDF from storage rather than from a shared
        # filesystem, so web and worker can run on different machines.
        storage = get_storage()
        pdf_path = storage.local_copy(pdf_key)
        if not pdf_path.exists():
            raise ValueError(f'Source PDF not found in storage: "{pdf_key}".')

        num_pages = _split_pdf_into_pages(pdf_path, slug, storage, task_status)
        require_org = bool(app.config.get("DEFAULT_PROJECT_REQUIRES_ORG", True))
        _add_project_to_database(
            display_title=display_title,
            slug=slug,
            num_pages=num_pages,
            creator_id=creator_id,
            require_org=require_org,
        )
        add_storage_usage_for_project(slug)

    task_status.success(num_pages, slug)


@app.task(bind=True)
def create_project(
    self,
    *,
    display_title: str,
    pdf_key: str,
    app_environment: str,
    creator_id: int,
):
    """Split the given PDF into pages and register the project on the database.

    For argument details, see `create_project_inner`.
    """
    task_status = CeleryTaskStatus(self)
    create_project_inner(
        display_title=display_title,
        pdf_key=pdf_key,
        app_environment=app_environment,
        creator_id=creator_id,
        task_status=task_status,
    )
