/* PageDocument client utilities for OCR replica editing. */

export function newBlockId() {
  return `b${Math.random().toString(36).slice(2, 10)}`;
}

export function emptyDocument(pageWidth, pageHeight) {
  return {
    page_width: pageWidth || null,
    page_height: pageHeight || null,
    content_format: 'blocks',
    pipeline: 'standard',
    layout_html: null,
    blocks: [],
  };
}

export function parseDocument(raw) {
  if (!raw) return emptyDocument();
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return emptyDocument();
    }
  }
  return raw;
}

export function documentToPlainText(doc) {
  const blocks = [...(doc.blocks || [])].sort(
    (a, b) => (a.reading_order || 0) - (b.reading_order || 0),
  );
  return blocks
    .map((b) => (b.content || '').trim())
    .filter(Boolean)
    .join('\n\n');
}

export function fromOcrPayload(payload) {
  if (payload.document) {
    return parseDocument(payload.document);
  }
  if (payload.blocks && payload.blocks.length) {
    return {
      page_width: payload.page_width,
      page_height: payload.page_height,
      content_format: payload.content_format || 'blocks',
      pipeline: payload.pipeline || 'standard',
      layout_html: payload.layout_html || null,
      blocks: payload.blocks.map((b, i) => ({
        ...b,
        id: b.id || newBlockId(),
        reading_order: b.reading_order || i + 1,
      })),
    };
  }
  const text = payload.text || '';
  if (!text.trim()) return emptyDocument(payload.page_width, payload.page_height);
  return {
    page_width: payload.page_width,
    page_height: payload.page_height,
    content_format: 'blocks',
    pipeline: payload.pipeline || 'standard',
    layout_html: payload.layout_html,
    blocks: [
      {
        id: newBlockId(),
        type: 'paragraph',
        bbox: [0, 0, 0, 0],
        content: text,
        reading_order: 1,
        children: [],
      },
    ],
  };
}

export function parseBoundingBoxes(blob, engineHint) {
  if (!blob) return [];
  const trimmed = blob.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith('[')) {
    try {
      const items = JSON.parse(trimmed);
      return items.map((item) => ({
        x1: item.x1,
        y1: item.y1,
        x2: item.x2,
        y2: item.y2,
        text: item.text || '',
      }));
    } catch {
      return [];
    }
  }
  return trimmed.split('\n').flatMap((line) => {
    const parts = line.split('\t');
    if (parts.length < 5) return [];
    const x1 = parseInt(parts[0], 10);
    const y1 = parseInt(parts[1], 10);
    const x2 = parseInt(parts[2], 10);
    const y2 = parseInt(parts[3], 10);
    if (Number.isNaN(x1)) return [];
    return [{ x1, y1, x2, y2, text: parts.slice(4).join('\t') }];
  });
}

export function findBlockForBbox(blocks, bbox) {
  let best = null;
  let bestScore = 0;
  blocks.forEach((block) => {
    if (!block.bbox || block.bbox.length !== 4) return;
    const score = bboxIoU(block.bbox, bbox);
    if (score > bestScore) {
      bestScore = score;
      best = block;
    }
  });
  return bestScore > 0.1 ? best : null;
}

function bboxIoU(a, b) {
  const ix1 = Math.max(a[0], b[0]);
  const iy1 = Math.max(a[1], b[1]);
  const ix2 = Math.min(a[2], b[2]);
  const iy2 = Math.min(a[3], b[3]);
  if (ix2 <= ix1 || iy2 <= iy1) return 0;
  const inter = (ix2 - ix1) * (iy2 - iy1);
  const areaA = Math.max(0, a[2] - a[0]) * Math.max(0, a[3] - a[1]);
  const areaB = Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
  const union = areaA + areaB - inter;
  return union > 0 ? inter / union : 0;
}

export function reorderBlocks(blocks) {
  blocks.forEach((b, i) => {
    b.reading_order = i + 1;
  });
}
