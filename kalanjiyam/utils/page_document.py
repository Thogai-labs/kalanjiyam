"""Canonical page document model for OCR replica editing."""

from __future__ import annotations

import html
import uuid
from dataclasses import dataclass, field
from typing import Any

from kalanjiyam.utils.ocr_types import OcrResponse, post_process

BLOCK_TYPES = frozenset(
    {"paragraph", "heading", "verse", "table", "figure", "list_item"}
)


@dataclass
class Block:
    id: str
    type: str
    bbox: list[int]
    content: str
    reading_order: int
    children: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "bbox": self.bbox,
            "content": self.content,
            "reading_order": self.reading_order,
            "children": self.children,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Block:
        block_type = data.get("type", "paragraph")
        if block_type not in BLOCK_TYPES:
            block_type = "paragraph"
        bbox = data.get("bbox") or [0, 0, 0, 0]
        if len(bbox) != 4:
            bbox = [0, 0, 0, 0]
        return cls(
            id=str(data.get("id") or _new_block_id()),
            type=block_type,
            bbox=[int(x) for x in bbox],
            content=str(data.get("content") or ""),
            reading_order=int(data.get("reading_order") or 0),
            children=list(data.get("children") or []),
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
            text = block.content.strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def to_html(self, *, replica: bool = False) -> str:
        if self.layout_html and not replica:
            return self.layout_html
        if not self.blocks:
            return ""
        if replica and self.page_width and self.page_height:
            return _blocks_to_replica_html(self.blocks, self.page_width, self.page_height)
        return _blocks_to_flow_html(self.blocks)

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
    def from_ocr_response(cls, ocr: OcrResponse) -> PageDocument:
        blocks_data = ocr.blocks
        if blocks_data:
            blocks = [Block.from_dict(b) for b in blocks_data]
            for i, block in enumerate(blocks):
                block.content = post_process(block.content)
                if not block.reading_order:
                    block.reading_order = i + 1
            blocks.sort(key=lambda b: b.reading_order)
            return cls(
                page_width=ocr.page_width,
                page_height=ocr.page_height,
                content_format=ocr.content_format or "blocks",
                pipeline=ocr.pipeline or "standard",
                layout_html=ocr.layout_html,
                blocks=blocks,
            )

        blocks = _blocks_from_bounding_boxes(ocr.bounding_boxes)
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
            page_width=ocr.page_width,
            page_height=ocr.page_height,
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


def _blocks_from_bounding_boxes(
    boxes: list[tuple[int, int, int, int, str]],
) -> list[Block]:
    if not boxes:
        return []

    lines: dict[tuple[int, int], list[tuple[int, int, int, int, str]]] = {}
    for x1, y1, x2, y2, text in boxes:
        if not text.strip():
            continue
        key = (y1 // 20, x1)
        lines.setdefault(key, []).append((x1, y1, x2, y2, text))

    blocks: list[Block] = []
    order = 1
    for key in sorted(lines.keys()):
        row_boxes = sorted(lines[key], key=lambda b: b[0])
        texts = [post_process(b[4]) for b in row_boxes]
        content = " ".join(texts).strip()
        if not content:
            continue
        x1 = min(b[0] for b in row_boxes)
        y1 = min(b[1] for b in row_boxes)
        x2 = max(b[2] for b in row_boxes)
        y2 = max(b[3] for b in row_boxes)
        block_type = "verse" if content.endswith("॥") else "paragraph"
        blocks.append(
            Block(
                id=_new_block_id(),
                type=block_type,
                bbox=[x1, y1, x2, y2],
                content=content,
                reading_order=order,
            )
        )
        order += 1
    return blocks


def _blocks_to_flow_html(blocks: list[Block]) -> str:
    parts = []
    for block in sorted(blocks, key=lambda b: b.reading_order):
        text = html.escape(block.content).replace("\n", "<br>")
        if not text:
            continue
        tag = {
            "heading": "h2",
            "verse": "div",
            "table": "div",
            "figure": "div",
        }.get(block.type, "p")
        cls = ' class="ocr-verse"' if block.type == "verse" else ""
        parts.append(f"<{tag}{cls} data-block-id=\"{block.id}\">{text}</{tag}>")
    return "\n".join(parts)


def _blocks_to_replica_html(
    blocks: list[Block], page_width: int, page_height: int
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
        text = html.escape(block.content).replace("\n", "<br>")
        inner.append(
            f'<div class="ocr-replica-block" data-block-id="{block.id}" '
            f'data-block-type="{block.type}" '
            f'style="left:{left:.2f}%;top:{top:.2f}%;width:{width:.2f}%;'
            f'min-height:{height:.2f}%;">{text}</div>'
        )
    return (
        f'<div class="ocr-replica-page" '
        f'style="aspect-ratio:{page_width}/{page_height};">'
        f'{"".join(inner)}</div>'
    )


def document_for_revision(
    revision: Any,
    page: Any | None = None,
) -> PageDocument:
    """Load PageDocument from revision, with legacy fallback."""
    if revision is None:
        return PageDocument.empty()
    doc = getattr(revision, "document", None)
    if doc:
        return PageDocument.from_dict(doc)
    page_width = getattr(page, "page_width", None) if page else None
    page_height = getattr(page, "page_height", None) if page else None
    fmt = getattr(revision, "content_format", None) or "plain"
    return PageDocument.from_legacy_content(
        revision.content,
        page_width=page_width,
        page_height=page_height,
        content_format=fmt,
    )


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
