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
    for r_id, rel in doc.part.rels.items():
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


def _parse_run_to_html(run, child, project_slug, image_mapping) -> str:
    import html
    from docx.oxml.ns import qn
    
    text = run.text or ""
    if not text:
        if "w:drawing" in child.xml:
            for blip in child.xpath('.//a:blip'):
                embed_id = blip.get(qn('r:embed'))
                if embed_id in image_mapping:
                    filename = image_mapping[embed_id]
                    return f'<img src="/static/uploads/{project_slug}/images/{filename}" alt="Image" />'
        elif "w:br" in child.xml:
            return '<br/>'
        return ""
        
    run_html = html.escape(text)
    if run.bold:
        run_html = f'<strong>{run_html}</strong>'
    if run.italic:
        run_html = f'<em>{run_html}</em>'
    if run.underline:
        run_html = f'<u>{run_html}</u>'
    if run.font and run.font.strike:
        run_html = f'<s>{run_html}</s>'
    return run_html


def _parse_paragraph_to_html(p, project_slug, image_mapping) -> str | tuple:
    import html
    from docx.oxml.ns import qn
    from docx.text.run import Run
    from lxml import etree

    html_runs = []
    
    for child in p._p.iterchildren():
        tag = child.tag
        if tag.endswith('pPr'):
            continue
            
        elif tag.endswith('r'):
            run = Run(child, p)
            run_html = _parse_run_to_html(run, child, project_slug, image_mapping)
            if run_html:
                html_runs.append(run_html)
                
        elif tag.endswith('hyperlink'):
            r_id = child.get(qn('r:id'))
            href = ""
            if r_id and r_id in p.part.rels:
                href = p.part.rels[r_id].target_ref
            
            link_runs = []
            for sub_child in child.iterchildren():
                if sub_child.tag.endswith('r'):
                    run = Run(sub_child, p)
                    run_html = _parse_run_to_html(run, sub_child, project_slug, image_mapping)
                    if run_html:
                        link_runs.append(run_html)
            
            link_content = "".join(link_runs)
            html_runs.append(f'<a href="{html.escape(href)}">{link_content}</a>')
            
        elif tag.endswith('oMath') or tag.endswith('oMathPara'):
            oMath_xml = etree.tostring(child, encoding='utf-8').decode('utf-8')
            html_runs.append(f'<span class="math-placeholder" data-xml="{html.escape(oMath_xml)}">[Equation]</span>')

    content = "".join(html_runs)
    if not content.strip():
        content = "&nbsp;"

    style_attrs = []
    if p.alignment is not None:
        align_val = p.alignment
        if align_val == 1:
            style_attrs.append("text-align: center;")
        elif align_val == 2:
            style_attrs.append("text-align: right;")
        elif align_val == 3:
            style_attrs.append("text-align: justify;")
    style_str = " ".join(style_attrs)

    style_name = (p.style.name or "").lower() if p.style else ""
    numPr = p._p.pPr.find(qn('w:numPr')) if (p._p.pPr is not None) else None
    
    is_list = numPr is not None or "list" in style_name or style_name.startswith("bullet") or style_name.startswith("numbered")
    if is_list:
        is_bullet = "bullet" in style_name or "number" not in style_name
        list_type = "bullet" if is_bullet else "ordered"
        return ("li", list_type, content, style_str)

    if style_name.startswith("heading") or "heading" in style_name:
        try:
            import re
            m = re.search(r'\d+', style_name)
            level = int(m.group(0)) if m else 1
            if 1 <= level <= 6:
                tag = f"h{level}"
            else:
                tag = "h1"
        except Exception:
            tag = "h1"
        tag_style = f' style="{style_str}"' if style_str else ""
        return f'<{tag}{tag_style}>{content}</{tag}>'

    tag_style = f' style="{style_str}"' if style_str else ""
    return f'<p{tag_style}>{content}</p>'


def _parse_table_to_html(table, project_slug, image_mapping) -> str:
    from docx.oxml.ns import qn
    
    rows = table.rows
    if not rows:
        return ""
        
    num_rows = len(rows)
    num_cols = max(len(row.cells) for row in rows) if num_rows > 0 else 0
    
    html_rows = []
    
    for r_idx, row in enumerate(rows):
        html_cells = []
        for c_idx, cell in enumerate(row.cells):
            coord = (r_idx, c_idx)
            cell_element = cell._tc
            
            first_coord = None
            for tr_idx, trow in enumerate(rows):
                for tc_idx, tcell in enumerate(trow.cells):
                    if tcell._tc is cell_element:
                        first_coord = (tr_idx, tc_idx)
                        break
                if first_coord:
                    break
            
            if first_coord != coord:
                continue
                
            colspan = 1
            tcPr = cell_element.get_or_add_tcPr()
            gridSpan = tcPr.find(qn('w:gridSpan'))
            if gridSpan is not None:
                val = gridSpan.get(qn('w:val'))
                if val:
                    try:
                        colspan = int(val)
                    except Exception:
                        pass
                    
            rowspan = 1
            next_r = r_idx + 1
            while next_r < num_rows:
                if c_idx < len(rows[next_r].cells) and rows[next_r].cells[c_idx]._tc is cell_element:
                    rowspan += 1
                    next_r += 1
                else:
                    break
            
            cell_html = []
            for p in cell.paragraphs:
                res = _parse_paragraph_to_html(p, project_slug, image_mapping)
                if isinstance(res, tuple):
                    cell_style = f' style="{res[3]}"' if res[3] else ""
                    cell_html.append(f'<p{cell_style}>{res[2]}</p>')
                else:
                    cell_html.append(res)
            
            td_attrs = []
            if colspan > 1:
                td_attrs.append(f'colspan="{colspan}"')
            if rowspan > 1:
                td_attrs.append(f'rowspan="{rowspan}"')
                
            td_attr_str = " ".join(td_attrs)
            td_tag = f'<td {td_attr_str}>' if td_attr_str else '<td>'
            
            html_cells.append(f'{td_tag}{"".join(cell_html)}</td>')
            
        html_rows.append(f'<tr>{"".join(html_cells)}</tr>')
        
    return f'<table class="border-collapse border border-teal-300 w-full">{"".join(html_rows)}</table>'


def _compile_elements_to_html(elements) -> str:
    grouped_html = []
    list_group = []
    list_type = None

    for item in elements:
        if isinstance(item, tuple) and item[0] == "li":
            _, curr_type, content, style_attr = item
            if list_group and list_type != curr_type:
                tag = "ul" if list_type == "bullet" else "ol"
                grouped_html.append(f'<{tag}>{"".join(list_group)}</{tag}>')
                list_group = []
            list_type = curr_type
            li_style = f' style="{style_attr}"' if style_attr else ""
            list_group.append(f'<li{li_style}>{content}</li>')
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

    return "".join(grouped_html)


def _segment_docx(doc, slug, image_mapping) -> list[tuple[str, str]]:
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    current_page_html = []
    current_page_text = []
    pages = []

    def flush_page():
        if current_page_html:
            page_html = _compile_elements_to_html(current_page_html)
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
