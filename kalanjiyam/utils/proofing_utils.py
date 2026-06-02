from collections.abc import Iterator
from datetime import date
import json

from kalanjiyam.utils.page_document import PageDocument, document_for_revision

DOUBLE_DANDA = "\u0965"

TEI_HEADER_BOILERPLATE = """
<?xml version="1.0" encoding="UTF-8"?>
<!-- This file was automatically generated. Please review it for markup mistakes
and resolve any TODOs. -->
<TEI xml:id="{xml_id}" xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader xml:lang="en">
    <fileDesc>
      <titleStmt>
        <title type="main">{title}</title>
        <title type="sub">A machine-readable edition</title>
        <author>{author}</author>
      </titleStmt>
      <publicationStmt>
        <publisher>Kalanjiyam</publisher>
        <!-- "free" or "restricted" depending on the license-->
        <availability status="{availability_status}">
          <license>
            TODO
          </license>
        </availability>
        <date>{current_year}</date>
      </publicationStmt>
      <sourceDesc>
        <bibl>
          <title>{title}</title>
          <author>{author}</author>
          <editor>{editor}</editor>
          <publisher>{publisher}</publisher>
          <pubPlace>{publisher_location}</pubPlace>
          <date>{publication_year}</date>
        </bibl>
      </sourceDesc>
    </fileDesc>
    <encodingDesc>
      <projectDesc>
        <p>Produced through the distributed proofreading interface on Kalanjiyam.</p>
      </projectDesc>
    </encodingDesc>
    <revisionDesc>
      TODO
    </revisionDesc>
  </teiHeader>
  <text xml:lang="{text_language}">
    <body>
""".strip()

PageContent = str
Line = str


def _iter_raw_text_lines(blobs: list[PageContent]) -> Iterator[Line]:
    """Iterate over text blobs as a stream of lines."""
    for blob in blobs:
        blob = blob.strip()
        for line in blob.splitlines():
            yield line.strip()


def iter_blocks(blobs: Iterator[PageContent]) -> Iterator[list[Line]]:
    """Iterate over text blobs as a stream of blocks.

    A block is a sequence of lines separated by an empty line."""
    buf = []
    for line in _iter_raw_text_lines(blobs):
        if line:
            buf.append(line)
        elif buf:
            yield buf
            buf = []
    if buf:
        yield buf


def is_verse(lines: list[Line]) -> bool:
    """Heuristically decide whether a list of lines represents a verse."""
    return lines[-1].endswith(DOUBLE_DANDA)


def create_plain_text_block(lines: list[Line]) -> str:
    """Convert a group of lines into a well-formatted plain-text block."""
    if is_verse(lines):
        return "\n".join(lines)

    buf = []
    for line in lines:
        # Join hyphens
        if line.endswith("-"):
            buf.append(line[:-1])
        else:
            buf.append(line)
            buf.append(" ")
    return "".join(buf).strip()


def create_tei_header_boilerplate(**kw) -> str:
    # FIXME: add much more TEI boilerplate
    return TEI_HEADER_BOILERPLATE.format(**kw)


def create_xml_block(lines: list[Line]) -> str:
    """Convert a group of lines into a well-formatted TEI XML block."""
    if is_verse(lines):
        buf = ["<lg>"]
        for line in lines:
            buf.append(f"  <l>{line}</l>")
        buf.append("</lg>")
        return "\n".join(buf)

    buf = ["<p>"]
    for line in lines:
        # Join hyphens
        if line.endswith("-"):
            buf.append(line[:-1])
        else:
            buf.append(line)
            buf.append(" ")

    # Strip trailing space from the loop.
    buf[-1] = buf[-1].strip()

    buf.append("</p>")
    return "".join(buf).strip()


def to_plain_text(blobs: list[PageContent]) -> str:
    """Publish a project as plain text."""
    blocks = iter_blocks(blobs)
    return "\n\n".join(create_plain_text_block(b) for b in blocks)


def to_tei_xml(project_meta: dict[str, str], blobs: list[PageContent]) -> str:
    """Publish a project as TEI XML."""
    project_meta.update(
        {
            "xml_id": "TODO",
            "current_year": date.today().year,
            "publisher_location": "TODO",
            "text_language": "sa-Deva",
            # "free" or "restricted"
            "availability_status": "TODO",
        }
    )
    buf = [create_tei_header_boilerplate(**project_meta)]

    for i, blob in enumerate(blobs):
        page_number = i + 1
        buf.append(f'<pb n="{page_number}" />')

        # <pb> element makes it difficult to work with a stream of blobs,
        # so just process one blob at a time and stitch them together after.
        blocks = iter_blocks([blob])
        buf.append("\n\n".join(create_xml_block(b) for b in blocks))

    buf.append("</body></text></TEI>")
    return "\n\n".join(buf)


def revision_plain_content(revision) -> str:
    """Plain text for a revision, preferring structured document when present."""
    if revision is None:
        return ""
    if getattr(revision, "document", None):
        return PageDocument.from_dict(revision.document).to_plain_text()
    return revision.content or ""


def document_to_tei(doc: PageDocument) -> str:
    return doc.to_tei_fragment()


def documents_to_tei_xml(project_meta: dict[str, str], pages) -> str:
    """TEI XML from pages with structured revisions when available."""
    project_meta.update(
        {
            "xml_id": "TODO",
            "current_year": date.today().year,
            "publisher_location": "TODO",
            "text_language": "sa-Deva",
            "availability_status": "TODO",
        }
    )
    buf = [create_tei_header_boilerplate(**project_meta)]
    for i, page in enumerate(pages):
        page_number = i + 1
        buf.append(f'<pb n="{page_number}" />')
        if page.revisions:
            rev = page.revisions[-1]
            if getattr(rev, "document", None) and rev.content_format == "blocks":
                doc = PageDocument.from_dict(rev.document)
                buf.append(doc.to_tei_fragment())
            else:
                blocks = iter_blocks([rev.content or ""])
                buf.append("\n\n".join(create_xml_block(b) for b in blocks))
    buf.append("</body></text></TEI>")
    return "\n\n".join(buf)


def documents_to_html(pages, *, replica: bool = False) -> str:
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">',
        "<title>Export</title>",
        '<link rel="stylesheet" href="/static/css/style.css">',
        "</head><body class='p-8'>",
    ]
    for page in pages:
        parts.append(f'<section class="ocr-export-page" data-page="{page.slug}">')
        if page.revisions:
            rev = page.revisions[-1]
            if getattr(rev, "document", None):
                doc = document_for_revision(rev, page)
                parts.append(doc.to_html(replica=replica))
            else:
                parts.append(f"<pre>{rev.content}</pre>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "\n".join(parts)


def documents_to_json_bundle(project, pages) -> str:
    payload = {
        "format_version": "3.0",
        "project_slug": project.slug,
        "display_title": project.display_title,
        "pages": [],
    }
    for page in pages:
        entry = {
            "slug": page.slug,
            "order": page.order,
            "page_width": page.page_width,
            "page_height": page.page_height,
            "ocr_bounding_boxes": page.ocr_bounding_boxes,
        }
        if page.revisions:
            rev = page.revisions[-1]
            entry["revision"] = {
                "content": rev.content,
                "content_format": getattr(rev, "content_format", "plain"),
                "document": getattr(rev, "document", None),
            }
        payload["pages"].append(entry)
    return json.dumps(payload, ensure_ascii=False, indent=2)
