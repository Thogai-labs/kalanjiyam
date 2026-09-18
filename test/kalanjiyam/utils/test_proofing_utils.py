from kalanjiyam.utils import proofing_utils as pu


def test_iter_blocks():
    blobs = [
        "\n".join(
            [
                "Here",
                "  are",
                " ",
                "three  ",
                "blocks",
            ]
        ),
        "\n".join(["   over", "three", "", "", "pages!"]),
    ]
    assert list(pu.iter_blocks(blobs)) == [
        ["Here", "are"],
        ["three", "blocks", "over", "three"],
        ["pages!"],
    ]


def test_create_plain_text_block():
    lines = ["An", "exam-", "ple", "paragraph."]
    assert pu.create_plain_text_block(lines) == "An example paragraph."


def test_create_plain_text_block__verse():
    lines = ["An", "exam-", "ple", "verse. ॥"]
    assert pu.create_plain_text_block(lines) == "An\nexam-\nple\nverse. ॥"


def test_create_xml_block__paragraph():
    lines = ["An", "exam-", "ple", "paragraph."]
    assert pu.create_xml_block(lines) == "<p>An example paragraph.</p>"


def test_create_xml_block__verse():
    lines = ["An", "example", "verse ॥"]
    assert pu.create_xml_block(lines) == "\n".join(
        [
            "<lg>",
            "  <l>An</l>",
            "  <l>example</l>",
            "  <l>verse ॥</l>",
            "</lg>",
        ]
    )


def test_is_valid_bbox():
    assert not pu._is_valid_bbox(None)
    assert not pu._is_valid_bbox([])
    assert not pu._is_valid_bbox([0, 0, 0])
    assert not pu._is_valid_bbox([0, 0, 0, 0])
    assert not pu._is_valid_bbox([100, 200, 100, 300])  # width is 0
    assert not pu._is_valid_bbox([100, 200, 200, 200])  # height is 0
    assert not pu._is_valid_bbox(["a", "b", "c", "d"])
    assert pu._is_valid_bbox([50, 100, 500, 700])
    assert pu._is_valid_bbox([500, 700, 50, 100])  # inverted coordinates still have positive delta


def test_documents_to_pdf__zero_bbox(monkeypatch):
    from unittest.mock import MagicMock
    import fitz
    from kalanjiyam.utils.page_document import PageDocument, Block

    project = MagicMock()
    project.slug = "1901-09425v1"

    page = MagicMock()
    page.slug = "2"
    page.page_width = 800
    page.page_height = 1000

    rev = MagicMock()
    rev.content = "Sample page content"
    b1 = Block(id="b1", type="heading", bbox=[0, 0, 0, 0], content="Heading Title", reading_order=1)
    b2 = Block(id="b2", type="paragraph", bbox=[0, 0, 0, 0], content="Paragraph text here.", reading_order=2)
    doc_obj = PageDocument(
        page_width=800,
        page_height=1000,
        content_format="blocks",
        pipeline="test",
        layout_html=None,
        blocks=[b1, b2],
    )
    page.revisions = [rev]

    monkeypatch.setattr(
        "kalanjiyam.utils.document_storage.load_revision_document",
        lambda r: {"blocks": [b1.to_dict(), b2.to_dict()]},
    )
    monkeypatch.setattr(
        "kalanjiyam.utils.page_document.document_for_revision",
        lambda r, p: doc_obj,
    )

    pdf_bytes = pu.documents_to_pdf(project, [page])
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0

    # Ensure generated PDF is valid and readable
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) == 1
    text = doc[0].get_text()
    assert "Heading Title" in text or "Paragraph text" in text
    doc.close()


def test_documents_to_pdf__full_project_mixed_bboxes(monkeypatch):
    from unittest.mock import MagicMock
    import fitz
    from kalanjiyam.utils.page_document import PageDocument, Block

    project = MagicMock()
    project.slug = "proj-multi"

    # Page 1: normal coordinates
    page1 = MagicMock()
    page1.slug = "1"
    page1.page_width = 600
    page1.page_height = 800
    rev1 = MagicMock()
    rev1.content = "Page 1 content"
    b1 = Block(id="b1", type="paragraph", bbox=[50, 50, 500, 200], content="Page 1 Text", reading_order=1)
    doc1 = PageDocument(
        page_width=600,
        page_height=800,
        content_format="blocks",
        pipeline="test",
        layout_html=None,
        blocks=[b1],
    )
    page1.revisions = [rev1]

    # Page 2: mixed coordinates (one valid, one [0, 0, 0, 0], one table with small bbox, one figure)
    page2 = MagicMock()
    page2.slug = "2"
    page2.page_width = 600
    page2.page_height = 800
    rev2 = MagicMock()
    rev2.content = "Page 2 content"
    b2_1 = Block(id="b2_1", type="paragraph", bbox=[50, 50, 500, 150], content="Page 2 Block 1", reading_order=1)
    b2_2 = Block(id="b2_2", type="paragraph", bbox=[0, 0, 0, 0], content="Page 2 Added Block", reading_order=2)
    b2_3 = Block(id="b2_3", type="table", bbox=[50, 200, 55, 205], content="Small Table", reading_order=3)
    b2_4 = Block(id="b2_4", type="figure", bbox=[0, 0, 0, 0], content="Figure Block", reading_order=4)
    doc2 = PageDocument(
        page_width=600,
        page_height=800,
        content_format="blocks",
        pipeline="test",
        layout_html=None,
        blocks=[b2_1, b2_2, b2_3, b2_4],
    )
    page2.revisions = [rev2]

    # Page 3: fallback to raw content (no structured doc)
    page3 = MagicMock()
    page3.slug = "3"
    page3.page_width = 600
    page3.page_height = 800
    rev3 = MagicMock()
    rev3.content = "Raw page 3 content"
    page3.revisions = [rev3]

    docs_map = {"1": doc1, "2": doc2}

    monkeypatch.setattr(
        "kalanjiyam.utils.document_storage.load_revision_document",
        lambda r: {"has_doc": True} if r in (rev1, rev2) else None,
    )
    monkeypatch.setattr(
        "kalanjiyam.utils.page_document.document_for_revision",
        lambda r, p: docs_map[p.slug],
    )

    pdf_bytes = pu.documents_to_pdf(project, [page1, page2, page3])
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) == 3
    doc.close()


def test_documents_to_pdf__empty_pages():
    import fitz
    from unittest.mock import MagicMock

    project = MagicMock()
    project.slug = "empty-proj"

    # Calling with empty list should return a valid PDF with 1 blank page rather than crashing
    pdf_bytes = pu.documents_to_pdf(project, [])
    assert isinstance(pdf_bytes, bytes)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) == 1
    doc.close()

