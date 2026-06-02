/* Bbox-scaled replica canvas for page layout editing. */

import { scaleBoxesToImage } from './osd-overlay.js';
import { blockReplicaInnerHtml, normalizeUnicodeText } from './page-document.js';

export class ReplicaView {
  constructor(container, options = {}) {
    this.container = container;
    this.onChange = options.onChange || (() => {});
    this.onSelect = options.onSelect || (() => {});
    this.onTableFocus = options.onTableFocus || (() => {});
    this.document = { blocks: [], page_width: null, page_height: null };
    this.selectedId = null;
  }

  setDocument(doc) {
    this.document = doc;
    this._render();
  }

  highlightBlock(blockId) {
    this.selectedId = blockId;
    this._render();
  }

  _render() {
    const doc = this.document;
    const pw = doc.page_width || 1000;
    const ph = doc.page_height || 1400;
    let blocks = [...(doc.blocks || [])].sort(
      (a, b) => (a.reading_order || 0) - (b.reading_order || 0),
    );
    blocks = blocks.map((block) => {
      const [x1, y1, x2, y2] = block.bbox || [0, 0, 0, 0];
      const scaled = scaleBoxesToImage(
        [{ x1, y1, x2, y2 }],
        pw,
        ph,
      )[0];
      if (!scaled) return block;
      return { ...block, bbox: [scaled.x1, scaled.y1, scaled.x2, scaled.y2] };
    });

    this.container.innerHTML = '';
    const page = document.createElement('div');
    page.className = 'ocr-replica-page book-editor-text relative mx-auto';
    page.style.background = '#faf8f5';
    page.style.aspectRatio = `${pw} / ${ph}`;
    page.style.maxWidth = '100%';
    page.style.width = '100%';
    page.style.minHeight = '400px';

    blocks.forEach((block) => {
      const [x1, y1, x2, y2] = block.bbox || [0, 0, 0, 0];
      const isTable =
        block.type === 'table' || /<table[\s>]/i.test(String(block.content || ''));

      const conf = block.confidence;
      const confClass = conf == null ? '' : conf < 0.5 ? 'ocr-conf-low' : conf < 0.75 ? 'ocr-conf-mid' : '';
      const editedClass = block.manually_edited ? 'ocr-block-edited' : '';

      const el = document.createElement('div');
      el.className = `ocr-replica-block book-editor-text absolute overflow-auto text-base leading-relaxed p-1 ocr-replica-block--${block.type || 'paragraph'} ${confClass} ${editedClass} ${
        this.selectedId === block.id
          ? 'ring-2 ring-amber-600 z-10 bg-amber-50'
          : 'bg-white hover:bg-amber-50'
      }`;
      el.setAttribute('lang', block.language || 'und');
      el.dataset.blockId = block.id;
      el.dataset.blockType = block.type || 'paragraph';
      if (x2 > x1 && y2 > y1) {
        el.style.left = `${(100 * x1) / pw}%`;
        el.style.top = `${(100 * y1) / ph}%`;
        el.style.width = `${(100 * (x2 - x1)) / pw}%`;
        el.style.minHeight = `${(100 * (y2 - y1)) / ph}%`;
      } else {
        el.style.position = 'relative';
        el.style.width = '100%';
        el.style.marginBottom = '0.5rem';
      }
      if (isTable) {
        el.innerHTML = blockReplicaInnerHtml(block);
        // Tables can't be meaningfully edited in-place — offer flow mode instead
        const hint = document.createElement('button');
        hint.type = 'button';
        hint.className = 'ocr-table-flow-hint';
        hint.textContent = 'Edit in Flow ↗';
        hint.addEventListener('click', (e) => {
          e.stopPropagation();
          this.onTableFocus(block);
        });
        el.appendChild(hint);
      } else {
        el.contentEditable = 'true';
        el.innerText = normalizeUnicodeText(block.content || '');
        el.addEventListener('input', () => {
          block.content = normalizeUnicodeText(el.innerText);
          block.manually_edited = true;
          this.onChange(this.document);
        });
      }
      el.addEventListener('focus', () => {
        if (this.selectedId !== block.id) {
          this.selectedId = block.id;
          this.onSelect(block);
          page.querySelectorAll('.ocr-replica-block').forEach((node) => {
            const selected = node.dataset.blockId === block.id;
            node.classList.toggle('ring-2', selected);
            node.classList.toggle('ring-royalblue', selected);
            node.classList.toggle('bg-blue-50/80', selected);
            node.classList.toggle('z-10', selected);
          });
        }
      });
      page.appendChild(el);
    });

    if (!blocks.length) {
      page.innerHTML =
        '<p class="absolute inset-0 flex items-center justify-center text-slate-400 text-sm">Run OCR to see replica layout</p>';
    }

    this.container.appendChild(page);
  }
}
