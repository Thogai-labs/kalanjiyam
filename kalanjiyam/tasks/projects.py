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
    creator_id: int | None,
    require_org: bool,
    fingerprint_id: str | None = None,
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

    project = db.Project(
        slug=slug,
        display_title=display_title,
        creator_id=creator_id,
        fingerprint_id=fingerprint_id,
    )
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
    creator = session.query(db.User).filter_by(id=creator_id).first() if creator_id else None
    # Auto-assign projects to the creator's organization for tenant isolation.
    from kalanjiyam.utils.org_access import user_organization_id
    creator_org_id = user_organization_id(creator) if creator else None
    if creator_org_id:
        session.add(db.ProjectGroups(group_id=creator_org_id, project_id=project.id))
    elif not creator_org_id and fingerprint_id:
        # Guests default to the open-tenant workspace
        try:
            open_tenant = q.get_or_create_open_tenant()
            session.add(db.ProjectGroups(group_id=open_tenant.id, project_id=project.id))
        except Exception:
            pass
    elif creator and require_org:
        raise ValueError("Project creator must belong to an organization.")
    session.commit()


def _extract_docx_images(doc, project_slug, storage) -> dict:
    image_mapping = {}
    for r_id, rel in doc.part.relations.items():
        if "image" in rel.target_ref:
            try:
                img_bytes = rel.target_part.blob
                ext = rel.target_ref.split(".")[-1]
                filename = f"image_{r_id}.{ext}"
                from kalanjiyam.utils.storage import editor_image_key
                key = editor_image_key(project_slug, filename)
                storage.save(key, img_bytes)
                image_mapping[r_id] = filename
            except Exception as ex:
                logging.warning(f"Failed to extract image relation {r_id}: {ex}")
    return image_mapping


def _parse_paragraph_to_html(p, project_slug, image_mapping) -> str | tuple:
    import html
    from docx.oxml.ns import qn
    from docx.text.run import Run
    from lxml import etree

    html_runs = []
    for child in p._p.iterchildren():
        tag = child.tag
        if tag.endswith('r'):
            run = Run(child, p)
            run_html = run.text
            if not run_html:
                if "w:drawing" in child.xml:
                    for blip in child.xpath('.//a:blip'):
                        embed_id = blip.get(qn('r:embed'))
                        if embed_id in image_mapping:
                            filename = image_mapping[embed_id]
                            run_html = f'<img src="/static/uploads/{project_slug}/images/{filename}" alt="Image" />'
                elif "w:br" in child.xml:
                    run_html = '<br/>'
            else:
                run_html = html.escape(run_html)
                if run.bold:
                    run_html = f'<strong>{run_html}</strong>'
                if run.italic:
                    run_html = f'<em>{run_html}</em>'
                if run.underline:
                    run_html = f'<u>{run_html}</u>'
            if run_html:
                html_runs.append(run_html)
        elif tag.endswith('hyperlink'):
            link_text = child.text or ""
            r_id = child.get(qn('r:id'))
            href = ""
            if r_id and r_id in p.part.relations:
                href = p.part.relations[r_id].target_ref
            html_runs.append(f'<a href="{html.escape(href)}">{html.escape(link_text)}</a>')
        elif tag.endswith('oMath'):
            oMath_xml = etree.tostring(child, encoding='utf-8').decode('utf-8')
            html_runs.append(f'<span class="math-placeholder" data-xml="{html.escape(oMath_xml)}">[Equation]</span>')

    style_name = p.style.name if p.style else ""
    content = "".join(html_runs)

    if style_name.startswith("Heading"):
        try:
            level = int(style_name.split()[-1])
            if 1 <= level <= 6:
                return f'<h{level}>{content}</h{level}>'
        except Exception:
            pass
        return f'<h1>{content}</h1>'
    elif "List" in style_name:
        is_bullet = "Bullet" in style_name or "List" in style_name and not "Number" in style_name
        li_type = "bullet" if is_bullet else "ordered"
        return ("li", li_type, content)

    return f'<p>{content}</p>'


def _parse_table_to_html(table, project_slug, image_mapping) -> str:
    html_rows = []
    for row in table.rows:
        html_cells = []
        for cell in row.cells:
            cell_html = []
            for p in cell.paragraphs:
                res = _parse_paragraph_to_html(p, project_slug, image_mapping)
                if isinstance(res, tuple):
                    cell_html.append(f'<p>{res[2]}</p>')
                else:
                    cell_html.append(res)
            html_cells.append(f'<td>{"".join(cell_html)}</td>')
        html_rows.append(f'<tr>{"".join(html_cells)}</tr>')
    return f'<table class="border-collapse border border-teal-300 w-full">{"".join(html_rows)}</table>'


def _segment_docx(doc, slug, image_mapping) -> list[tuple[str, str]]:
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    current_page_html = []
    current_page_text = []
    pages = []

    def flush_page():
        if current_page_html:
            grouped_html = []
            list_group = []
            list_type = None

            for item in current_page_html:
                if isinstance(item, tuple) and item[0] == "li":
                    _, curr_type, content = item
                    if list_group and list_type != curr_type:
                        tag = "ul" if list_type == "bullet" else "ol"
                        grouped_html.append(f'<{tag}>{"".join(list_group)}</{tag}>')
                        list_group = []
                    list_type = curr_type
                    list_group.append(f'<li>{content}</li>')
                else:
                    if list_group:
                        tag = "ul" if list_type == "bullet" else "ol"
                        grouped_html.append(f'<{tag}>{"".join(list_group)}</{tag}>')
                        list_group = []
                        list_type = None
                    grouped_html.append(item)

            if list_group:
                tag = "ul" if list_type == "bullet" else "ol"
                grouped_html.append(f'<{tag}>{"".join(list_group)}</{tag}>')

            page_html = "".join(grouped_html)
            page_text = "\n".join(current_page_text)
            pages.append((page_text, page_html))
            current_page_html.clear()
            current_page_text.clear()

    def is_break_para(p):
        p_xml = p._p.xml
        if 'w:br' in p_xml and 'w:type="page"' in p_xml:
            return True
        if 'w:lastRenderedPageBreak' in p_xml:
            return True
        if 'w:sectPr' in p_xml:
            return True
        return False

    body_elm = doc.element.body
    for child in body_elm.iterchildren():
        if child.tag.endswith('p'):
            p = Paragraph(child, doc)
            res = _parse_paragraph_to_html(p, slug, image_mapping)
            current_page_html.append(res)
            current_page_text.append(p.text)
            if is_break_para(p) or len("".join(current_page_text)) > 1500:
                flush_page()
        elif child.tag.endswith('tbl'):
            table = Table(child, doc)
            res = _parse_table_to_html(table, slug, image_mapping)
            current_page_html.append(res)
            table_text = " ".join(pt.text for row in table.rows for cell in row.cells for pt in cell.paragraphs)
            current_page_text.append(table_text)
            if len("".join(current_page_text)) > 1500:
                flush_page()

    flush_page()
    # Handle empty document case
    if not pages:
        pages.append(("", "<p></p>"))
    return pages


def create_project_inner(
    *,
    display_title: str,
    pdf_key: str | None = None,
    docx_key: str | None = None,
    app_environment: str,
    creator_id: int | None,
    fingerprint_id: str | None = None,
    task_status: TaskStatus,
):
    """Split the given PDF or DOCX into pages and register the project on the database."""
    logging.info(f'Received upload task "{display_title}" for key {pdf_key or docx_key}.')

    app = create_config_only_app(app_environment)
    with app.app_context():
        session = q.get_session()
        slug = slugify(display_title)
        project = session.query(db.Project).filter_by(slug=slug).first()

        if project:
            raise ValueError(
                f'Project "{display_title}" already exists. Please choose a different title.'
            )

        storage = get_storage()

        if docx_key:
            docx_path = storage.local_copy(docx_key)
            if not docx_path.exists():
                raise ValueError(f'Source DOCX not found in storage: "{docx_key}".')

            from docx import Document
            doc = Document(docx_path)
            image_mapping = _extract_docx_images(doc, slug, storage)
            pages_list = _segment_docx(doc, slug, image_mapping)
            num_pages = len(pages_list)

            require_org = bool(app.config.get("DEFAULT_PROJECT_REQUIRES_ORG", True))
            _add_project_to_database(
                display_title=display_title,
                slug=slug,
                num_pages=num_pages,
                creator_id=creator_id,
                require_org=require_org,
                fingerprint_id=fingerprint_id,
            )

            db_project = session.query(db.Project).filter_by(slug=slug).one()
            unreviewed = session.query(db.PageStatus).filter_by(name="reviewed-0").one()
            import uuid

            for idx, (page_text, page_html) in enumerate(pages_list):
                page_slug = str(idx + 1)
                db_page = session.query(db.Page).filter_by(project_id=db_project.id, slug=page_slug).one()

                # Original track
                pv_orig = db.PageVersion(page_id=db_page.id, version_key="original")
                session.add(pv_orig)
                session.flush()

                doc_dict = {
                    "content_format": "html",
                    "blocks": [{
                        "id": f"b{uuid.uuid4().hex[:8]}",
                        "type": "paragraph",
                        "bbox": [0, 0, 0, 0],
                        "content": page_html,
                        "reading_order": 1
                    }]
                }

                rev_orig = db.Revision(
                    project_id=db_project.id,
                    page_id=db_page.id,
                    page_version_id=pv_orig.id,
                    status_id=unreviewed.id,
                    content=page_text,
                    content_format="html",
                    document=doc_dict
                )
                session.add(rev_orig)

                # Editable active track
                pv_target = db.PageVersion(page_id=db_page.id, version_key="role:p1")
                session.add(pv_target)
                session.flush()

                rev_target = db.Revision(
                    project_id=db_project.id,
                    page_id=db_page.id,
                    page_version_id=pv_target.id,
                    status_id=unreviewed.id,
                    content=page_text,
                    content_format="html",
                    document=doc_dict
                )
                session.add(rev_target)

            session.commit()
            add_storage_usage_for_project(slug)
        else:
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
                fingerprint_id=fingerprint_id,
            )
            add_storage_usage_for_project(slug)

    task_status.success(num_pages, slug)


@app.task(bind=True)
def create_project(
    self,
    *,
    display_title: str,
    pdf_key: str | None = None,
    docx_key: str | None = None,
    app_environment: str,
    creator_id: int | None,
    fingerprint_id: str | None = None,
):
    """Split the given PDF or DOCX into pages and register the project on the database."""
    task_status = CeleryTaskStatus(self)
    create_project_inner(
        display_title=display_title,
        pdf_key=pdf_key,
        docx_key=docx_key,
        app_environment=app_environment,
        creator_id=creator_id,
        fingerprint_id=fingerprint_id,
        task_status=task_status,
    )
