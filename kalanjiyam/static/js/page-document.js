/* PageDocument client utilities for OCR replica editing. */

export function normalizeUnicodeText(text) {
  if (text == null || text === '') return '';
  let value = String(text);
  if (/\\u[0-9a-fA-F]{4}/.test(value)) {
    try {
      value = JSON.parse(`"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`);
    } catch {
      try {
        value = value.replace(/\\u([0-9a-fA-F]{4})/g, (_, hex) =>
          String.fromCharCode(parseInt(hex, 16)),
        );
      } catch {
        /* keep original */
      }
    }
  }
  return value.normalize ? value.normalize('NFC') : value;
}

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
  let data = raw;
  if (typeof raw === 'string') {
    try {
      data = JSON.parse(raw);
    } catch {
      return emptyDocument();
    }
  }
  if (data && Array.isArray(data.blocks)) {
    data = {
      ...data,
      blocks: data.blocks.map((b) => ({
        ...b,
        content: normalizeUnicodeText(b.content || ''),
      })),
    };
  }
  return data;
}

function stripHtml(html) {
  if (!html) return '';
  return html.replace(/<[^>]*>/g, '');
}

export function documentToPlainText(doc) {
  if (doc.content_format === 'html') {
    return (doc.blocks && doc.blocks[0]?.content) || '';
  }
  const blocks = [...(doc.blocks || [])].sort(
    (a, b) => (a.reading_order || 0) - (b.reading_order || 0),
  );
  return blocks
    .map((b) => stripHtml(b.content || '').trim())
    .filter(Boolean)
    .join('\n\n');
}

const SKIP_BLOCK_TYPES = new Set(['running-header', 'page-number', 'figure']);
const BLOCK_TYPE_TO_TAG = {
  heading: 'h2',
  subheading: 'h3',
  footnote: 'p',
  caption: 'p',
  'column-header': 'p',
  equation: 'p',
  paragraph: 'p',
};

export function autoWrapMath(text) {
  if (!text) return '';
  if (text.includes('$')) return text;

  // Check if there are any Devanagari characters: [\u0900-\u097F]
  const hasDevanagari = /[\u0900-\u097F]/.test(text);

  if (!hasDevanagari) {
    const hasLatex = /\\(begin|end|Delta|times|therefore|vmatrix|frac|alpha|beta|gamma|theta|approx|neq|pm|lambda|sigma|pi|phi|omega|sqrt|partial|nabla|int|sum|prod|cup|cap|in|subset|infty|left|right|vmatrix|matrix|align)/.test(text);
    const hasMathSubSuper = /[a-zA-Z0-9]+_[a-zA-Z0-9]+|[a-zA-Z0-9]+\^[a-zA-Z0-9]+/.test(text);
    const hasMathEquation = /[a-zA-Z0-9]+\s*[\+\-\*\/=]\s*[a-zA-Z0-9]+/.test(text);

    if (hasLatex || hasMathSubSuper || hasMathEquation) {
      if (text.includes('\\begin') || text.includes('\n')) {
        return `$$${text}$$`;
      }
      return `$${text}$`;
    }
    return text;
  } else {
    let result = text;
    const envPattern = /(\\begin\{[a-zA-Z]+\}[\s\S]*?\\end\{[a-zA-Z]+\})/g;
    result = result.replace(envPattern, '$$$1$$');

    // Matches math patterns with optional leading variables (like I = \frac...)
    const mathPattern = /((?:[a-zA-Z0-9\(\)\[\]\s=\+\-\*\/]*?)(?:(?:\\[a-zA-Z]+)|(?:[a-zA-Z0-9]+(?:_[a-zA-Z0-9]+|\^[a-zA-Z0-9]+)))(?:[a-zA-Z0-9\+\-\*\/=\(\)\[\]_\^\\\{\}\:\.,\s\times\therefore]|(?:\\[a-zA-Z]+)|(?:[a-zA-Z0-9]+(?:_[a-zA-Z0-9]+|\^[a-zA-Z0-9]+)))*)/g;
    result = result.replace(mathPattern, (match) => {
      const trimmed = match.trim();
      if (!trimmed) return match;
      if (trimmed.startsWith('$') || trimmed.endsWith('$')) return match;
      if (/^[a-zA-Z]$/.test(trimmed)) return match;
      if (/^\d+$/.test(trimmed)) return match;
      
      const leadingSpace = match.match(/^\s*/)[0];
      const trailingSpace = match.match(/\s*$/)[0];
      return `${leadingSpace}$${trimmed}$${trailingSpace}`;
    });
    return result;
  }
}

export function documentToFlowHtml(doc) {
  const isRichFormat = doc.content_format === 'blocks' || doc.content_format === 'html';
  const blocks = [...(doc.blocks || [])].sort(
    (a, b) => (a.reading_order || 0) - (b.reading_order || 0),
  );
  const parts = [];
  blocks.forEach((block) => {
    if (SKIP_BLOCK_TYPES.has(block.type)) return;
    const content = String(block.content || '').trim();
    if (!content && block.type !== 'table') return;
    if (doc.content_format === 'html') {
      parts.push(content);
      return;
    }
    if (block.type === 'table' || /<table[\s>]/i.test(content)) {
      parts.push(
        `<div class="ocr-detected-table-wrap" data-block-id="${block.id}">${blockReplicaInnerHtml(block)}</div>`,
      );
      return;
    }
    const tag = BLOCK_TYPE_TO_TAG[block.type] ?? 'p';
    const wrappedContent = autoWrapMath(content);
    const text = isRichFormat
      ? wrappedContent.replace(/\n/g, '<br>')
      : wrappedContent
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/\n/g, '<br>');
    parts.push(`<${tag} data-block-id="${block.id}">${text}</${tag}>`);
  });
  return parts.join('');
}


/* Extract block text, keeping <br> as newlines (textContent drops them). */
function elementText(el) {
  const clone = el.cloneNode(true);
  clone.querySelectorAll('br').forEach((br) => {
    br.replaceWith(document.createTextNode('\n'));
  });
  return clone.textContent.trim();
}

/* Carry identity/geometry/provenance from a previous block onto a flow-edited
 * one. Word spans are dropped on edit — they no longer align with the text. */
function inheritBlock(prev, content, type, order) {
  const edited = prev.manually_edited || normalizeUnicodeText(prev.content || '').trim() !== content;
  return {
    ...prev,
    type: prev.type === 'table' ? 'table' : type,
    reading_order: order,
    content,
    manually_edited: edited,
    words: edited ? null : (prev.words ?? null),
    children: prev.children || [],
  };
}

export function blocksFromFlowHtml(html, previousBlocks = [], format = 'blocks') {
  if (!html) return [];
  if (format === 'html') {
    return [{
      id: (previousBlocks && previousBlocks[0]?.id) || 'b1',
      type: 'paragraph',
      bbox: [0, 0, 0, 0],
      content: html,
      reading_order: 1,
      manually_edited: true,
      children: []
    }];
  }
  const container = document.createElement('div');
  container.innerHTML = html;
  const prevById = new Map((previousBlocks || []).map((b) => [b.id, b]));
  const matchedIds = new Set();

  // First pass: parse elements and match blocks by data-block-id.
  const parsed = [];
  Array.from(container.children).forEach((el) => {
    const tag = el.tagName.toLowerCase();
    // Unwrap table-wrap divs
    const tableEl = tag === 'table' ? el : el.querySelector('table');
    const blockId = el.getAttribute('data-block-id')
      || (tableEl && tableEl.getAttribute('data-block-id'));
    if (tableEl) {
      parsed.push({ kind: 'table', content: tableEl.outerHTML, blockId });
      return;
    }
    const hasImage = el.querySelector('img') || tag === 'img';
    const plainText = elementText(el);
    if (!plainText && !hasImage) return;
    const content = tag === 'img' ? el.outerHTML : el.innerHTML;
    const type = (tag === 'h1' || tag === 'h2') ? 'heading' : tag === 'h3' ? 'subheading' : 'paragraph';
    parsed.push({ kind: type, content: content, blockId });
  });
  parsed.forEach((p) => {
    if (p.blockId && prevById.has(p.blockId)) matchedIds.add(p.blockId);
  });

  // Second pass: align unmatched elements to unmatched previous blocks in
  // order, so geometry survives even when ids were stripped.
  const unmatchedPrev = (previousBlocks || []).filter((b) => !matchedIds.has(b.id));
  let cursor = 0;
  const blocks = [];
  let order = 1;
  parsed.forEach((p) => {
    let prev = p.blockId ? prevById.get(p.blockId) : null;
    if (!prev && cursor < unmatchedPrev.length) {
      prev = unmatchedPrev[cursor];
      cursor += 1;
    }
    const type = p.kind === 'table' ? 'table' : p.kind;
    if (prev) {
      blocks.push(inheritBlock(prev, p.content, type, order));
    } else {
      blocks.push({
        id: newBlockId(),
        type,
        bbox: [0, 0, 0, 0],
        reading_order: order,
        content: p.content,
        manually_edited: true,
        children: [],
      });
    }
    order += 1;
  });
  return blocks;
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
        content: normalizeUnicodeText(b.content || ''),
        confidence: b.confidence ?? null,
        language: b.language ?? null,
        manually_edited: false,
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
        content: normalizeUnicodeText(text),
        reading_order: 1,
        children: [],
      },
    ],
  };
}

function boxFromItem(item) {
  if (!item || typeof item !== 'object') return null;
  const text = normalizeUnicodeText(item.text || item.label || '');
  if (item.x1 != null && item.y1 != null && item.x2 != null && item.y2 != null) {
    const x1 = parseCoord(item.x1);
    const y1 = parseCoord(item.y1);
    const x2 = parseCoord(item.x2);
    const y2 = parseCoord(item.y2);
    if (Number.isNaN(x1)) return null;
    return { x1, y1, x2, y2, text };
  }
  const bbox = item.bbox;
  if (Array.isArray(bbox) && bbox.length >= 4) {
    const x1 = parseCoord(bbox[0]);
    const y1 = parseCoord(bbox[1]);
    const x2 = parseCoord(bbox[2]);
    const y2 = parseCoord(bbox[3]);
    if (Number.isNaN(x1)) return null;
    return { x1, y1, x2, y2, text };
  }
  const polygon = item.polygon;
  if (Array.isArray(polygon) && polygon.length >= 4) {
    const xs = polygon.filter((_, i) => i % 2 === 0);
    const ys = polygon.filter((_, i) => i % 2 === 1);
    if (!xs.length || !ys.length) return null;
    return {
      x1: Math.min(...xs.map(parseCoord)),
      y1: Math.min(...ys.map(parseCoord)),
      x2: Math.max(...xs.map(parseCoord)),
      y2: Math.max(...ys.map(parseCoord)),
      text,
    };
  }
  return null;
}

export function parseBoundingBoxes(blob) {
  if (!blob) return [];
  if (Array.isArray(blob)) {
    return blob.map(boxFromItem).filter(Boolean);
  }
  const trimmed = String(blob).trim();
  if (!trimmed) return [];
  if (trimmed.startsWith('[')) {
    try {
      const items = JSON.parse(trimmed);
      if (!Array.isArray(items)) return [];
      return items.map(boxFromItem).filter(Boolean);
    } catch {
      return [];
    }
  }
  return trimmed.split('\n').flatMap((line) => {
    const parts = line.split('\t');
    if (parts.length < 5) return [];
    const x1 = parseCoord(parts[0]);
    const y1 = parseCoord(parts[1]);
    const x2 = parseCoord(parts[2]);
    const y2 = parseCoord(parts[3]);
    if (Number.isNaN(x1)) return [];
    return [{ x1, y1, x2, y2, text: parts.slice(4).join('\t') }];
  });
}

export function boxesFromDocumentBlocks(blocks) {
  if (!blocks || !blocks.length) return [];
  return blocks
    .map((block) => {
      const bbox = block.bbox;
      if (!bbox || bbox.length !== 4 || bbox[2] <= bbox[0] || bbox[3] <= bbox[1]) {
        return null;
      }
      return {
        x1: bbox[0],
        y1: bbox[1],
        x2: bbox[2],
        y2: bbox[3],
        text: block.content || '',
        blockId: block.id,
        blockType: block.type || 'paragraph',
        confidence: block.confidence ?? null,
        manuallyEdited: !!block.manually_edited,
      };
    })
    .filter(Boolean);
}

export function overlayBoxesFromPayload(payload, pageDocument) {
  const fromBlocks = boxesFromDocumentBlocks(pageDocument?.blocks);
  if (fromBlocks.length) return fromBlocks;
  const lines = parseBoundingBoxes(payload?.bounding_boxes);
  return clusterBoxesToParagraphs(lines);
}

const STRUCTURED_BLOCK_TYPES = new Set(['table', 'figure']);

export function hasStructuredBlocks(blocks) {
  if (!blocks?.length) return false;
  return blocks.some((b) => {
    if (STRUCTURED_BLOCK_TYPES.has(b.type)) return true;
    if (b.children?.length) return true;
    const content = String(b.content || '');
    if (b.type === 'table' || /<table[\s>]/i.test(content)) return true;
    // Tab characters indicate TSV table rows — treat as structured.
    return content.includes('\t');
  });
}

export function blocksLookLikeLines(blocks, pageHeight) {
  if (hasStructuredBlocks(blocks)) return false;
  if (!blocks || blocks.length < 4) return false;
  const spatial = blocks.filter(
    (b) => b.bbox && b.bbox.length === 4 && b.bbox[2] > b.bbox[0] && b.bbox[3] > b.bbox[1],
  );
  if (spatial.length < 4) return false;
  const heights = spatial.map((b) => b.bbox[3] - b.bbox[1]);
  const avgH = heights.reduce((a, b) => a + b, 0) / heights.length;
  if (pageHeight && avgH < pageHeight * 0.045) return true;
  const shortLines = blocks.filter(
    (b) => !String(b.content || '').includes('\n') && String(b.content || '').trim().length < 120,
  ).length;
  return shortLines >= Math.max(6, Math.floor(blocks.length * 0.75));
}

export function reclusterDocumentBlocks(doc) {
  if (
    !doc?.blocks?.length
    || hasStructuredBlocks(doc.blocks)
    || !blocksLookLikeLines(doc.blocks, doc.page_height)
  ) {
    return doc;
  }
  const boxes = doc.blocks
    .map((b) => ({
      x1: b.bbox[0],
      y1: b.bbox[1],
      x2: b.bbox[2],
      y2: b.bbox[3],
      text: b.content || '',
    }))
    .filter((b) => b.x2 > b.x1 && b.y2 > b.y1);
  // If some blocks have no spatial bbox, reclustering would silently drop their content.
  if (boxes.length < doc.blocks.length) return doc;
  const paras = clusterBoxesToParagraphs(boxes);
  if (!paras.length || paras.length >= doc.blocks.length) return doc;
  return {
    ...doc,
    content_format: 'blocks',
    blocks: paras.map((p, i) => ({
      id: newBlockId(),
      type: 'paragraph',
      bbox: [Math.round(p.x1), Math.round(p.y1), Math.round(p.x2), Math.round(p.y2)],
      content: p.text,
      reading_order: i + 1,
      children: [],
    })),
  };
}

function clusterBoxesToParagraphs(boxes) {
  if (!boxes.length) return [];
  const sorted = [...boxes].sort((a, b) => a.y1 - b.y1 || a.x1 - b.x1);
  const lines = [];
  sorted.forEach((box) => {
    if (!box.text || !String(box.text).trim()) return;
    const centerY = (box.y1 + box.y2) / 2;
    let placed = false;
    for (const line of lines) {
      const ref = line[0];
      const refCenter = (ref.y1 + ref.y2) / 2;
      const lineH = Math.max(ref.y2 - ref.y1, box.y2 - box.y1, 8);
      if (Math.abs(centerY - refCenter) <= lineH * 0.6) {
        line.push(box);
        placed = true;
        break;
      }
    }
    if (!placed) lines.push([box]);
  });

  const lineRecords = lines
    .map((line) => {
      line.sort((a, b) => a.x1 - b.x1);
      const x1 = Math.min(...line.map((b) => b.x1));
      const y1 = Math.min(...line.map((b) => b.y1));
      const x2 = Math.max(...line.map((b) => b.x2));
      const y2 = Math.max(...line.map((b) => b.y2));
      const text = line.map((b) => b.text).filter(Boolean).join(' ').trim();
      return { x1, y1, x2, y2, text };
    })
    .filter((line) => line.text)
    .sort((a, b) => a.y1 - b.y1);

  const paragraphs = [];
  let para = null;
  let prevBottom = null;
  lineRecords.forEach((line) => {
    const lineH = Math.max(line.y2 - line.y1, 12);
    if (prevBottom !== null) {
      const gap = line.y1 - prevBottom;
      if (gap > Math.max(28, lineH * 1.6)) {
        if (para) paragraphs.push(para);
        para = { ...line };
        prevBottom = line.y2;
        return;
      }
    }
    if (!para) {
      para = { ...line };
    } else {
      para.x1 = Math.min(para.x1, line.x1);
      para.y1 = Math.min(para.y1, line.y1);
      para.x2 = Math.max(para.x2, line.x2);
      para.y2 = Math.max(para.y2, line.y2);
      para.text = `${para.text} ${line.text}`.trim();
    }
    prevBottom = line.y2;
  });
  if (para) paragraphs.push(para);
  return paragraphs;
}

function parseCoord(value) {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : NaN;
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

export function blockReplicaInnerHtml(block) {
  const content = String(block.content || '').trim();
  if (block.type === 'table' || /<table[\s>]/i.test(content)) {
    if (/<table[\s>]/i.test(content)) return content;
    return plainTextToHtmlTable(content);
  }
  const wrapped = autoWrapMath(content);
  return wrapped
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
}

function plainTextToHtmlTable(text) {
  const rows = [];
  text.split('\n').forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let cells;
    if (trimmed.includes('|')) {
      cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
    } else if (trimmed.includes('\t')) {
      cells = trimmed.split('\t').map((c) => c.trim());
    } else {
      cells = [trimmed];
    }
    if (cells.length && !cells.every((c) => /^-+$/.test(c))) {
      rows.push(cells);
    }
  });
  if (!rows.length) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br>');
  }
  const cols = Math.max(...rows.map((r) => r.length));
  const esc = (s) =>
    String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  let html = '<table class="ocr-detected-table">';
  rows.forEach((row) => {
    html += '<tr>';
    for (let i = 0; i < cols; i += 1) {
      html += `<td>${esc(row[i] || '')}</td>`;
    }
    html += '</tr>';
  });
  html += '</table>';
  return html;
}
