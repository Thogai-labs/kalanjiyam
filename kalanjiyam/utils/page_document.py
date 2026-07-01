"""Canonical page document model for OCR replica editing."""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from kalanjiyam.utils.ocr_types import OcrResponse, post_process
from kalanjiyam.utils.text_utils import normalize_unicode_text

BLOCK_TYPES = frozenset({
    "paragraph", "heading", "subheading", "verse",
    "table", "figure", "caption", "footnote",
    "running-header", "page-number", "column-header",
    "equation", "list_item",
})

# Types the UI skips in flow mode
DECORATIVE_BLOCK_TYPES = frozenset({"running-header", "page-number", "figure"})

# Legacy type aliases: normalize on ingest
_BLOCK_TYPE_ALIASES: dict[str, str] = {
    "h3": "subheading",
    "list_item": "paragraph",
    "verse": "paragraph",
}


@dataclass
class Block:
    id: str
    type: str
    bbox: list[int]
    content: str
    reading_order: int
    children: list[dict[str, Any]] = field(default_factory=list)
    confidence: float | None = None
    language: str | None = None
    #: Word/line-level spans: [{"text", "confidence", "bbox"?}, ...].
    #: See docs/ocr-service-contract.rst.
    words: list[dict[str, Any]] | None = None
    #: True once a human has changed this block's content.
    manually_edited: bool = False
    #: Provenance stamped at ingestion: {"engine", "model"?, "ocr_at"}.
    source: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "bbox": self.bbox,
            "content": self.content,
            "reading_order": self.reading_order,
            "children": self.children,
        }
        if self.confidence is not None:
            d["confidence"] = self.confidence
        if self.language is not None:
            d["language"] = self.language
        if self.words:
            d["words"] = self.words
        if self.manually_edited:
            d["manually_edited"] = True
        if self.source is not None:
            d["source"] = self.source
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Block:
        raw_type = str(data.get("type") or "paragraph")
        block_type = _BLOCK_TYPE_ALIASES.get(raw_type, raw_type)
        if block_type not in BLOCK_TYPES:
            block_type = "paragraph"
        bbox = data.get("bbox") or [0, 0, 0, 0]
        if len(bbox) != 4:
            bbox = [0, 0, 0, 0]
        conf = _clamp_confidence(data.get("confidence"))
        words = data.get("words")
        if not isinstance(words, list) or not words:
            words = None
        source = data.get("source")
        if not isinstance(source, dict):
            source = None
        return cls(
            id=str(data.get("id") or _new_block_id()),
            type=block_type,
            bbox=[int(x) for x in bbox],
            content=str(normalize_unicode_text(data.get("content") or "")),
            reading_order=int(data.get("reading_order") or 0),
            children=list(data.get("children") or []),
            confidence=conf,
            language=str(data["language"]) if data.get("language") else None,
            words=words,
            manually_edited=bool(data.get("manually_edited")),
            source=source,
        )


@dataclass
class PageDocument:
    page_width: int | None
    page_height: int | None
    content_format: str
    pipeline: str
    layout_html: str | None
    blocks: list[Block]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_width": self.page_width,
            "page_height": self.page_height,
            "content_format": self.content_format,
            "pipeline": self.pipeline,
            "layout_html": self.layout_html,
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PageDocument:
        if not data:
            return cls.empty()
        blocks = [Block.from_dict(b) for b in data.get("blocks") or []]
        blocks.sort(key=lambda b: b.reading_order)
        return cls(
            page_width=data.get("page_width"),
            page_height=data.get("page_height"),
            content_format=data.get("content_format") or "plain",
            pipeline=data.get("pipeline") or "standard",
            layout_html=data.get("layout_html"),
            blocks=blocks,
        )

    @classmethod
    def empty(cls) -> PageDocument:
        return cls(
            page_width=None,
            page_height=None,
            content_format="plain",
            pipeline="standard",
            layout_html=None,
            blocks=[],
        )

    def to_plain_text(self) -> str:
        if not self.blocks:
            return ""
        parts = []
        for block in sorted(self.blocks, key=lambda b: b.reading_order):
            text = _strip_html_tags(block.content).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def to_html(self, *, replica: bool = False) -> str:
        if self.layout_html and not replica:
            return self.layout_html
        if not self.blocks:
            return ""
        if replica and self.page_width and self.page_height:
            return _blocks_to_replica_html(self.blocks, self.page_width, self.page_height, content_format=self.content_format)
        return _blocks_to_flow_html(self.blocks, content_format=self.content_format)

    def to_tei_fragment(self) -> str:
        parts = []
        for block in sorted(self.blocks, key=lambda b: b.reading_order):
            text = html.escape(block.content.strip())
            if not text:
                continue
            if block.type == "verse":
                lines = [line.strip() for line in block.content.splitlines() if line.strip()]
                if lines:
                    parts.append("<lg>")
                    for line in lines:
                        parts.append(f"  <l>{html.escape(line)}</l>")
                    parts.append("</lg>")
            elif block.type == "heading":
                parts.append(f"<head>{text}</head>")
            elif block.type == "table":
                parts.append(f"<p><!-- table --></p><p>{text}</p>")
            else:
                parts.append(f"<p>{text}</p>")
        return "\n\n".join(parts)

    def merge_blocks(self, other_blocks: list[Block]) -> None:
        existing_ids = {b.id for b in self.blocks}
        for block in other_blocks:
            if block.id not in existing_ids:
                self.blocks.append(block)
                existing_ids.add(block.id)
        self.blocks.sort(key=lambda b: b.reading_order)

    @classmethod
    def from_ocr_response(
        cls,
        ocr: OcrResponse,
        *,
        image_width: int | None = None,
        image_height: int | None = None,
    ) -> PageDocument:
        boxes, blocks_data, pw, ph = normalize_geometry(
            ocr.bounding_boxes,
            ocr.blocks,
            ocr_width=ocr.page_width,
            ocr_height=ocr.page_height,
            image_width=image_width,
            image_height=image_height,
            coordinate_space=ocr.coordinate_space,
        )
        if blocks_data:
            blocks = [Block.from_dict(b) for b in blocks_data]
            for i, block in enumerate(blocks):
                block.content = post_process(block.content)
                if not block.reading_order:
                    block.reading_order = i + 1
            blocks.sort(key=lambda b: b.reading_order)
            if _blocks_look_like_lines(blocks, ph):
                line_boxes = boxes or _boxes_from_blocks(blocks)
                if line_boxes:
                    rebuilt = _blocks_from_bounding_boxes(line_boxes)
                    if rebuilt:
                        blocks = rebuilt
            return cls(
                page_width=pw,
                page_height=ph,
                content_format=ocr.content_format or "blocks",
                pipeline=ocr.pipeline or "standard",
                layout_html=ocr.layout_html,
                blocks=blocks,
            )

        blocks = _blocks_from_bounding_boxes(boxes)
        if not blocks and ocr.text_content.strip():
            blocks = [
                Block(
                    id=_new_block_id(),
                    type="paragraph",
                    bbox=[0, 0, 0, 0],
                    content=post_process(ocr.text_content.strip()),
                    reading_order=1,
                )
            ]
        return cls(
            page_width=pw,
            page_height=ph,
            content_format="blocks" if blocks else "plain",
            pipeline=ocr.pipeline or "standard",
            layout_html=ocr.layout_html,
            blocks=blocks,
        )

    @classmethod
    def from_legacy_content(
        cls,
        content: str,
        *,
        page_width: int | None = None,
        page_height: int | None = None,
        content_format: str = "plain",
    ) -> PageDocument:
        text = (content or "").strip()
        if not text:
            return cls(
                page_width=page_width,
                page_height=page_height,
                content_format=content_format,
                pipeline="legacy",
                layout_html=None,
                blocks=[],
            )
        return cls(
            page_width=page_width,
            page_height=page_height,
            content_format=content_format if content_format != "plain" else "blocks",
            pipeline="legacy",
            layout_html=None,
            blocks=[
                Block(
                    id=_new_block_id(),
                    type="paragraph",
                    bbox=[0, 0, 0, 0],
                    content=text,
                    reading_order=1,
                )
            ],
        )


def _new_block_id() -> str:
    return f"b{uuid.uuid4().hex[:8]}"


def _strip_html_tags(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"<[^>]*>", "", text)


def _clamp_confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return min(1.0, max(0.0, score))


def _coords_are_normalized(
    bbox: list[float] | list[int],
    *,
    coordinate_space: str = "pixel",
) -> bool:
    if coordinate_space == "normalized":
        return True
    if not bbox or len(bbox) != 4:
        return False
    max_coord = max(float(x) for x in bbox)
    return max_coord > 0 and max_coord <= 1.5


def _scale_boxes_to_image(
    boxes: list[tuple[float, float, float, float, str]],
    width: int | None,
    height: int | None,
    *,
    coordinate_space: str = "pixel",
) -> list[tuple[float, float, float, float, str]]:
    if not boxes or not width or not height:
        return boxes
    if not _coords_are_normalized(list(boxes[0][:4]), coordinate_space=coordinate_space):
        return boxes
    return [
        (b[0] * width, b[1] * height, b[2] * width, b[3] * height, b[4])
        for b in boxes
    ]


def _scale_bbox(
    bbox: list[int] | list[float],
    sx: float,
    sy: float,
) -> list[int]:
    if len(bbox) != 4:
        return [0, 0, 0, 0]
    return [int(bbox[0] * sx), int(bbox[1] * sy), int(bbox[2] * sx), int(bbox[3] * sy)]


def _boxes_from_blocks(
    blocks: list[Block],
) -> list[tuple[float, float, float, float, str]]:
    """Derive line boxes from block bboxes when the service omits bounding_boxes."""
    derived: list[tuple[float, float, float, float, str]] = []
    for block in blocks:
        if not block.bbox or block.bbox == [0, 0, 0, 0]:
            continue
        x1, y1, x2, y2 = block.bbox
        if x2 <= x1 or y2 <= y1:
            continue
        derived.append((float(x1), float(y1), float(x2), float(y2), block.content or ""))
    return derived


def normalize_geometry(
    boxes: list[tuple[float, float, float, float, str]],
    blocks: list[dict[str, Any]] | None,
    *,
    ocr_width: int | None,
    ocr_height: int | None,
    image_width: int | None,
    image_height: int | None,
    coordinate_space: str = "pixel",
) -> tuple[
    list[tuple[float, float, float, float, str]],
    list[dict[str, Any]] | None,
    int | None,
    int | None,
]:
    """Scale OCR boxes/blocks to match the actual page image pixel grid."""
    ref_w = ocr_width or image_width
    ref_h = ocr_height or image_height
    out_w = image_width or ocr_width
    out_h = image_height or ocr_height

    scaled = _scale_boxes_to_image(
        list(boxes), ref_w, ref_h, coordinate_space=coordinate_space
    )

    sx = sy = 1.0
    if ref_w and out_w and ref_w > 0:
        sx = out_w / ref_w
    if ref_h and out_h and ref_h > 0:
        sy = out_h / ref_h

    if scaled and out_w and out_h:
        max_x = max(b[2] for b in scaled)
        max_y = max(b[3] for b in scaled)
        if max_x > out_w * 1.02:
            sx *= out_w / max_x
        if max_y > out_h * 1.02:
            sy *= out_h / max_y

    if sx != 1.0 or sy != 1.0:
        scaled = [
            (b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy, b[4]) for b in scaled
        ]

    normalized_blocks = blocks
    if blocks:
        normalized_blocks = []
        for block in blocks:
            item = dict(block)
            bbox = item.get("bbox")
            if bbox and len(bbox) == 4:
                if _coords_are_normalized(bbox, coordinate_space=coordinate_space):
                    if ref_w and ref_h:
                        bbox = [
                            bbox[0] * ref_w,
                            bbox[1] * ref_h,
                            bbox[2] * ref_w,
                            bbox[3] * ref_h,
                        ]
                if sx != 1.0 or sy != 1.0:
                    bbox = _scale_bbox(bbox, sx, sy)
                item["bbox"] = [int(x) for x in bbox]
            words = item.get("words")
            if isinstance(words, list):
                scaled_words = []
                for word in words:
                    if isinstance(word, dict) and word.get("bbox"):
                        wb = word["bbox"]
                        if _coords_are_normalized(wb, coordinate_space=coordinate_space):
                            if ref_w and ref_h:
                                wb = [
                                    wb[0] * ref_w,
                                    wb[1] * ref_h,
                                    wb[2] * ref_w,
                                    wb[3] * ref_h,
                                ]
                        if sx != 1.0 or sy != 1.0:
                            wb = _scale_bbox(wb, sx, sy)
                        word = {**word, "bbox": [int(x) for x in wb]}
                    scaled_words.append(word)
                item["words"] = scaled_words
            normalized_blocks.append(item)

    return scaled, normalized_blocks, out_w, out_h


STRUCTURED_BLOCK_TYPES = frozenset({"table", "figure"})


def _has_structured_blocks(blocks: list[Block]) -> bool:
    """True when OCR already classified layout blocks we must not line-merge away."""
    for block in blocks:
        if block.type in STRUCTURED_BLOCK_TYPES:
            return True
        if block.children:
            return True
        content = (block.content or "").strip()
        if block.type == "table" or "<table" in content.lower():
            return True
        # Tab characters indicate TSV table rows — treat as structured.
        if "\t" in content:
            return True
    return False


def _blocks_look_like_lines(blocks: list[Block], page_height: int | None) -> bool:
    if _has_structured_blocks(blocks):
        return False
    if len(blocks) < 4:
        return False
    spatial = [b for b in blocks if b.bbox and b.bbox != [0, 0, 0, 0]]
    if len(spatial) < 4:
        return False
    heights = [b.bbox[3] - b.bbox[1] for b in spatial]
    avg_h = sum(heights) / len(heights)
    if page_height and avg_h < page_height * 0.045:
        return True
    short_lines = sum(
        1 for b in blocks if "\n" not in b.content and len(b.content.strip()) < 120
    )
    return short_lines >= max(6, int(len(blocks) * 0.75))


def _group_boxes_into_lines(
    boxes: list[tuple[float, float, float, float, str]],
) -> list[list[tuple[float, float, float, float, str]]]:
    if not boxes:
        return []
    sorted_boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for box in sorted_boxes:
        x1, y1, x2, y2, text = box
        if not text.strip():
            continue
        center_y = (y1 + y2) / 2
        placed = False
        for line in lines:
            ref = line[0]
            ref_center = (ref[1] + ref[3]) / 2
            line_h = max(ref[3] - ref[1], y2 - y1, 8)
            if abs(center_y - ref_center) <= line_h * 0.6:
                line.append(box)
                placed = True
                break
        if not placed:
            lines.append([box])
    for line in lines:
        line.sort(key=lambda b: b[0])
    lines.sort(key=lambda line: min(b[1] for b in line))
    return lines


def _merge_line_boxes(
    row_boxes: list[tuple[float, float, float, float, str]],
) -> tuple[int, int, int, int, str]:
    x1 = min(b[0] for b in row_boxes)
    y1 = min(b[1] for b in row_boxes)
    x2 = max(b[2] for b in row_boxes)
    y2 = max(b[3] for b in row_boxes)
    texts = [post_process(normalize_unicode_text(b[4])) for b in row_boxes]
    content = " ".join(t for t in texts if t.strip()).strip()
    return int(x1), int(y1), int(x2), int(y2), content


def _blocks_from_bounding_boxes(
    boxes: list[tuple[float, float, float, float, str]],
) -> list[Block]:
    if not boxes:
        return []

    lines = _group_boxes_into_lines(boxes)
    line_records: list[tuple[int, int, int, int, str]] = []
    for line in lines:
        merged = _merge_line_boxes(line)
        if merged[4]:
            line_records.append(merged)

    if not line_records:
        return []

    blocks: list[Block] = []
    order = 1
    para_x1 = para_y1 = para_x2 = para_y2 = 0
    para_texts: list[str] = []

    def flush_paragraph() -> None:
        nonlocal order, para_texts, para_x1, para_y1, para_x2, para_y2
        if not para_texts:
            return
        content = " ".join(para_texts).strip()
        if not content:
            para_texts = []
            return
        block_type = "verse" if content.endswith("॥") else "paragraph"
        blocks.append(
            Block(
                id=_new_block_id(),
                type=block_type,
                bbox=[para_x1, para_y1, para_x2, para_y2],
                content=content,
                reading_order=order,
            )
        )
        order += 1
        para_texts = []

    prev_bottom: int | None = None
    for x1, y1, x2, y2, content in line_records:
        line_h = max(y2 - y1, 12)
        if prev_bottom is not None:
            gap = y1 - prev_bottom
            if gap > max(28, int(line_h * 1.6)):
                flush_paragraph()
                para_x1, para_y1, para_x2, para_y2 = x1, y1, x2, y2
                para_texts = [content]
                prev_bottom = y2
                continue
        if not para_texts:
            para_x1, para_y1, para_x2, para_y2 = x1, y1, x2, y2
            para_texts = [content]
        else:
            para_x1 = min(para_x1, x1)
            para_y1 = min(para_y1, y1)
            para_x2 = max(para_x2, x2)
            para_y2 = max(para_y2, y2)
            para_texts.append(content)
        prev_bottom = y2

    flush_paragraph()
    return blocks


def _block_replica_inner_html(block: Block, content_format: str = "plain") -> str:
    """HTML inside a replica block (tables as grids, not escaped plain text)."""
    content = (block.content or "").strip()
    if block.type == "table" or "<table" in content.lower():
        if "<table" in content.lower():
            return content
        return _plain_text_to_html_table(content)
    if content_format == "blocks":
        return block.content.replace("\n", "<br>")
    return html.escape(block.content).replace("\n", "<br>")


def _plain_text_to_html_table(text: str) -> str:
    """Turn TSV / pipe-grid plain text into a simple HTML table."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
        elif "\t" in line:
            cells = [c.strip() for c in line.split("\t")]
        else:
            cells = [line]
        if cells and not all(c.replace("-", "").strip() == "" for c in cells):
            rows.append(cells)
    if not rows:
        return html.escape(text).replace("\n", "<br>")
    col_count = max(len(r) for r in rows)
    parts = ['<table class="ocr-detected-table">']
    for row in rows:
        parts.append("<tr>")
        for i in range(col_count):
            cell = html.escape(row[i] if i < len(row) else "")
            parts.append(f"<td>{cell}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


_BLOCK_TYPE_TO_HTML_TAG: dict[str, str] = {
    "heading": "h2",
    "subheading": "h3",
    "footnote": "p",
    "caption": "p",
    "column-header": "p",
    "equation": "p",
    "paragraph": "p",
}


def _blocks_to_flow_html(blocks: list[Block], content_format: str = "plain") -> str:
    parts = []
    for block in sorted(blocks, key=lambda b: b.reading_order):
        if block.type in DECORATIVE_BLOCK_TYPES:
            continue
        if not block.content.strip() and block.type != "table":
            continue
        if block.type == "table" or "<table" in block.content.lower():
            inner = _block_replica_inner_html(block)
            parts.append(
                f'<div class="ocr-detected-table-wrap" data-block-id="{block.id}">'
                f"{inner}</div>"
            )
            continue
        if content_format == "blocks":
            text = block.content.replace("\n", "<br>")
        else:
            text = html.escape(block.content).replace("\n", "<br>")
        tag = _BLOCK_TYPE_TO_HTML_TAG.get(block.type, "p")
        parts.append(f'<{tag} data-block-id="{block.id}">{text}</{tag}>')
    return "\n".join(parts)


def _blocks_to_replica_html(
    blocks: list[Block], page_width: int, page_height: int, content_format: str = "plain"
) -> str:
    inner = []
    for block in sorted(blocks, key=lambda b: b.reading_order):
        x1, y1, x2, y2 = block.bbox
        if x2 <= x1 or y2 <= y1:
            left, top, width, height = 0, 0, 100, 5
        else:
            left = 100 * x1 / page_width
            top = 100 * y1 / page_height
            width = 100 * (x2 - x1) / page_width
            height = 100 * (y2 - y1) / page_height
        inner_html = _block_replica_inner_html(block, content_format=content_format)
        inner.append(
            f'<div class="ocr-replica-block ocr-replica-block--{block.type}" '
            f'data-block-id="{block.id}" '
            f'data-block-type="{block.type}" '
            f'style="position: absolute; left:{left:.2f}%;top:{top:.2f}%;width:{width:.2f}%;'
            f'min-height:{height:.2f}%;">{inner_html}</div>'
        )
    return (
        f'<div class="ocr-replica-page" '
        f'style="position: relative; aspect-ratio:{page_width}/{page_height};">'
        f'{"".join(inner)}</div>'
    )


def _blocks_lack_spatial_bboxes(blocks: list[Block]) -> bool:
    if not blocks:
        return True
    return all(not b.bbox or b.bbox == [0, 0, 0, 0] for b in blocks)


def enrich_document_from_page_ocr(
    doc: PageDocument,
    page: Any | None,
    *,
    engine: str = "surya",
    rebuild_blocks: bool = True,
) -> PageDocument:
    """Fill dimensions and spatial blocks from stored page OCR boxes (Surya JSON)."""
    if page is None:
        return doc
    raw_boxes = getattr(page, "ocr_bounding_boxes", None)
    if not raw_boxes:
        return doc
    from kalanjiyam.utils.ocr_client import _parse_bounding_boxes

    boxes = _parse_bounding_boxes(raw_boxes, engine)
    if not boxes:
        return doc

    image_w = image_h = None
    project = getattr(page, "project", None)
    if project is not None:
        try:
            from kalanjiyam.utils.assets import get_page_image_filepath
            from kalanjiyam.utils.ocr_persist import image_size

            size = image_size(get_page_image_filepath(project.slug, page.slug))
            if size:
                image_w, image_h = size
        except Exception:
            pass

    ocr_w = doc.page_width or getattr(page, "page_width", None)
    ocr_h = doc.page_height or getattr(page, "page_height", None)
    scaled, norm_block_dicts, pw, ph = normalize_geometry(
        boxes,
        [b.to_dict() for b in doc.blocks] if doc.blocks else None,
        ocr_width=ocr_w,
        ocr_height=ocr_h,
        image_width=image_w,
        image_height=image_h,
        coordinate_space="pixel",
    )
    if pw:
        doc.page_width = int(pw)
    if ph:
        doc.page_height = int(ph)

    need_rebuild = rebuild_blocks and (
        _blocks_lack_spatial_bboxes(doc.blocks) or _blocks_look_like_lines(
            doc.blocks, doc.page_height
        )
    )
    if need_rebuild:
        built = _blocks_from_bounding_boxes(scaled)
        if built:
            doc.blocks = built
            doc.content_format = "blocks"
    elif norm_block_dicts and norm_block_dicts != [b.to_dict() for b in doc.blocks]:
        doc.blocks = [Block.from_dict(b) for b in norm_block_dicts]
    return doc


def document_for_revision(
    revision: Any,
    page: Any | None = None,
) -> PageDocument:
    """Load PageDocument from revision, with legacy fallback."""
    if revision is None:
        doc = PageDocument.empty()
        return enrich_document_from_page_ocr(doc, page)
    doc_data = getattr(revision, "document", None)
    if doc_data:
        doc = PageDocument.from_dict(doc_data)
        if doc.blocks:
            # Blocks were explicitly saved — only update page dimensions, never
            # rebuild from raw OCR bboxes (which carry pre-edit content).
            return enrich_document_from_page_ocr(doc, page, rebuild_blocks=False)
    else:
        page_width = getattr(page, "page_width", None) if page else None
        page_height = getattr(page, "page_height", None) if page else None
        fmt = getattr(revision, "content_format", None) or "plain"
        doc = PageDocument.from_legacy_content(
            revision.content,
            page_width=page_width,
            page_height=page_height,
            content_format=fmt,
        )
    return enrich_document_from_page_ocr(doc, page)


def iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def find_block_for_bbox(blocks: list[Block], bbox: list[int]) -> Block | None:
    best: Block | None = None
    best_score = 0.0
    for block in blocks:
        if not block.bbox or block.bbox == [0, 0, 0, 0]:
            continue
        score = iou(block.bbox, bbox)
        if score > best_score:
            best_score = score
            best = block
    return best if best_score > 0.1 else None
