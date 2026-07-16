/* Bbox-scaled replica canvas for page layout editing. */

import { scaleBoxesToImage } from './osd-overlay.js';
import { blockReplicaInnerHtml, normalizeUnicodeText, autoWrapMath } from './page-document.js';
import renderMathInElement from 'katex/dist/contrib/auto-render.js';

/* Inject styles for selection, move/resize, and toolbar custom tokens */
if (!document.getElementById('ocr-replica-styles')) {
  const style = document.createElement('style');
  style.id = 'ocr-replica-styles';
  style.textContent = `
    .ocr-replica-page {
      container-type: inline-size;
    }
    .ocr-replica-block.is-selected {
      outline: 2px solid #2563eb !important;
      z-index: 5 !important;
      overflow: visible !important; /* Show overflow when editing to make resizing easier */
    }
    .ocr-replica-block {
      font-size: clamp(10px, 1.25cqw, 24px) !important; /* Scale text proportionally relative to responsive container width, clamping to 10px minimum */
      white-space: pre-wrap;
      overflow: hidden !important; /* Remove internal scrollbars for a clean print-accurate canvas */
    }
    .ocr-replica-block h1 {
      font-size: 1.4em !important;
      font-weight: bold !important;
      margin: 0 !important;
      display: block !important;
    }
    .ocr-replica-block h2 {
      font-size: 1.2em !important;
      font-weight: bold !important;
      margin: 0 !important;
      display: block !important;
    }
    .ocr-replica-block h3 {
      font-size: 1.1em !important;
      font-weight: bold !important;
      margin: 0 !important;
      display: block !important;
    }
    .ocr-replica-block p {
      margin: 0 !important;
    }
    .ocr-replica-block table td[contenteditable="true"]:focus,
    .ocr-replica-block table th[contenteditable="true"]:focus {
      outline: 1.5px solid #2563eb !important;
      background-color: #eff6ff !important;
    }
    .ocr-replica-resize-handle {
      position: absolute;
      right: -5px;
      bottom: -5px;
      width: 12px;
      height: 12px;
      background-color: #2563eb;
      border: 1.5px solid #ffffff;
      border-radius: 50%;
      cursor: se-resize;
      z-index: 10;
      box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    }
    .ocr-replica-toolbar-btn {
      padding: 0.25rem 0.5rem;
      font-size: 0.75rem;
      font-weight: 500;
      color: #374151;
      background-color: #ffffff;
      border: 1px solid #d1d5db;
      border-radius: 0.25rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.25rem;
    }
    .ocr-replica-toolbar-btn:hover:not(:disabled) {
      background-color: #f3f4f6;
    }
    .ocr-replica-toolbar-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .ocr-replica-page.move-mode-active .ocr-replica-block {
      cursor: move !important;
    }
  `;
  document.head.appendChild(style);
}

/* Hover text: "OCR 87% · surya/0.6.1 · edited" */
export function blockProvenanceLabel(block) {
  const parts = [];
  if (block.confidence != null) {
    parts.push(`OCR ${Math.round(block.confidence * 100)}%`);
  }
  if (block.source) {
    parts.push(block.source.model || block.source.engine || '');
  }
  if (block.manually_edited) parts.push('edited');
  return parts.filter(Boolean).join(' · ');
}

export class ReplicaView {
  constructor(container, options = {}) {
    this.container = container;
    this.onChange = options.onChange || (() => {});
    this.onSelect = options.onSelect || (() => {});
    this.onTableFocus = options.onTableFocus || (() => {});
    this.document = { blocks: [], page_width: null, page_height: null };
    this.originalDocument = null;
    this.selectedId = null;
    this.copiedBlock = null;
    this.moveMode = false;
    this.isRestoredFromCache = false;
    this.zoom = parseInt(localStorage.getItem('replica-zoom') || '100');

    // Clear local storage cache when the form is submitted
    const form = document.querySelector('form.book-editor-shell');
    if (form) {
      form.addEventListener('submit', () => {
        const key = this._getStorageKey();
        if (key) localStorage.removeItem(key);
      });
    }

    // Clean up previous event listeners on document if already created
    if (window._replicaPasteHandler) {
      document.removeEventListener('paste', window._replicaPasteHandler);
    }

    // Listen to global paste events (Ctrl+V) on the document to dynamically create blocks
    this._onPaste = async (e) => {
      // Avoid acting if replica container is not currently visible
      if (!this.container.isConnected || this.container.offsetWidth === 0) {
        return;
      }

      // If user is actively typing inside an editable block and Move Mode is off, let browser handle normal text paste
      if (document.activeElement && document.activeElement.isContentEditable && !this.moveMode) {
        return;
      }

      e.preventDefault();
      e.stopPropagation();

      const items = (e.clipboardData || e.originalEvent.clipboardData).items;
      for (const item of items) {
        if (item.type.indexOf('image') === 0) {
          const file = item.getAsFile();
          if (file) {
            await this.uploadAndInsertImageFile(file);
            return;
          }
        }
      }

      const text = (e.clipboardData || window.clipboardData).getData('text');
      if (text && text.trim()) {
        this.addTextBlockWithContent(text.trim());
      }
    };

    window._replicaPasteHandler = this._onPaste;
    document.addEventListener('paste', this._onPaste);
  }

  _getStorageKey() {
    const pathMatch = window.location.pathname.match(/\/proofing\/([^\/]+)\/([^\/]+)/);
    if (pathMatch) {
      return `kalanjiyam-replica-doc-${pathMatch[1]}-${pathMatch[2]}`;
    }
    return null;
  }

  triggerChange() {
    const key = this._getStorageKey();
    if (key) {
      localStorage.setItem(key, JSON.stringify(this.document));
    }
    this.onChange(this.document);
  }

  discardCachedEdits() {
    const key = this._getStorageKey();
    if (key) {
      localStorage.removeItem(key);
    }
    this.isRestoredFromCache = false;
    this.document = JSON.parse(JSON.stringify(this.originalDocument));
    this.selectedId = null;
    this.triggerChange();
    this._render();
  }

  setDocument(doc) {
    if (!this.originalDocument) {
      this.originalDocument = JSON.parse(JSON.stringify(doc));
    }

    const key = this._getStorageKey();
    if (key && !this.isRestoredFromCache) {
      const cached = localStorage.getItem(key);
      if (cached) {
        try {
          const parsed = JSON.parse(cached);
          if (parsed && parsed.blocks && parsed.blocks.length > 0) {
            this.document = parsed;
            this.isRestoredFromCache = true;
            this._render();
            this.onChange(this.document);
            return;
          }
        } catch (e) {
          console.error('Error loading cached document from localStorage:', e);
        }
      }
    }

    this.document = doc;
    this._render();
  }

  highlightBlock(blockId) {
    this.selectedId = blockId;
    this._render();
  }

  /* Select a block, scroll it into view, and focus it for editing. */
  focusBlock(blockId) {
    this.highlightBlock(blockId);
    const el = this.container.querySelector(`[data-block-id="${blockId}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (el.isContentEditable) el.focus({ preventScroll: true });
    }
  }

  addTextBlock() {
    this.addTextBlockWithContent('New Text Block');
  }

  addTextBlockWithContent(content) {
    const pw = this.document.page_width || 1000;
    const ph = this.document.page_height || 1400;

    const newBlock = {
      id: `block-${Math.random().toString(36).substr(2, 9)}`,
      type: 'paragraph',
      bbox: [
        Math.round(pw * 0.25),
        Math.round(ph * 0.25),
        Math.round(pw * 0.75),
        Math.round(ph * 0.35)
      ],
      content: content,
      reading_order: (this.document.blocks || []).length + 1,
      confidence: 1.0,
      manually_edited: true
    };

    if (!this.document.blocks) this.document.blocks = [];
    this.document.blocks.push(newBlock);
    this.selectedId = newBlock.id;
    this.triggerChange();
    this._render();
    this.focusBlock(newBlock.id);
  }

  triggerImageUpload() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      await this.uploadAndInsertImageFile(file);
    });
    input.click();
  }

  async uploadAndInsertImageFile(file) {
    const formData = new FormData();
    formData.append('image', file);

    const url = window.location.pathname.replace('/proofing/', '/api/upload-image/');

    try {
      const response = await fetch(url, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('Image upload failed');
      }

      const result = await response.json();
      if (result.success && result.url) {
        const pw = this.document.page_width || 1000;
        const ph = this.document.page_height || 1400;

        const newBlock = {
          id: `block-${Math.random().toString(36).substr(2, 9)}`,
          type: 'paragraph', // Inline <img> tag inside a paragraph block
          bbox: [
            Math.round(pw * 0.25),
            Math.round(ph * 0.25),
            Math.round(pw * 0.75),
            Math.round(ph * 0.60)
          ],
          content: `<img class="max-w-full h-auto rounded-lg" src="${result.url}">`,
          reading_order: (this.document.blocks || []).length + 1,
          confidence: 1.0,
          manually_edited: true
        };

        if (!this.document.blocks) this.document.blocks = [];
        this.document.blocks.push(newBlock);
        this.selectedId = newBlock.id;
        this.triggerChange();
        this._render();
        this.focusBlock(newBlock.id);
      }
    } catch (err) {
      console.error(err);
      alert('Failed to upload image: ' + err.message);
    }
  }

  addTableBlock() {
    const rowsInput = prompt('Enter number of rows:', '3');
    if (rowsInput === null) return;
    const colsInput = prompt('Enter number of columns:', '3');
    if (colsInput === null) return;

    const rows = parseInt(rowsInput) || 3;
    const cols = parseInt(colsInput) || 3;

    let tableHtml = '<table style="border-collapse: collapse; width: 100%; border: 1px solid #cbd5e1; text-align: left; font-size: 0.875rem;">';
    for (let r = 0; r < rows; r++) {
      tableHtml += '<tr>';
      for (let c = 0; c < cols; c++) {
        if (r === 0) {
          tableHtml += '<th style="border: 1px solid #cbd5e1; padding: 8px; background-color: #f8fafc; font-weight: bold;">Header</th>';
        } else {
          tableHtml += '<td style="border: 1px solid #cbd5e1; padding: 8px;">Cell</td>';
        }
      }
      tableHtml += '</tr>';
    }
    tableHtml += '</table>';

    const pw = this.document.page_width || 1000;
    const ph = this.document.page_height || 1400;

    const newBlock = {
      id: `block-${Math.random().toString(36).substr(2, 9)}`,
      type: 'table',
      bbox: [
        Math.round(pw * 0.20),
        Math.round(ph * 0.25),
        Math.round(pw * 0.80),
        Math.round(ph * 0.50)
      ],
      content: tableHtml,
      reading_order: (this.document.blocks || []).length + 1,
      confidence: 1.0,
      manually_edited: true
    };

    if (!this.document.blocks) this.document.blocks = [];
    this.document.blocks.push(newBlock);
    this.selectedId = newBlock.id;
    this.triggerChange();
    this._render();
    this.focusBlock(newBlock.id);
  }

  copySelectedBlock() {
    if (!this.selectedId) return;
    const block = this.document.blocks.find(b => b.id === this.selectedId);
    if (block) {
      this.copiedBlock = JSON.parse(JSON.stringify(block));
      this._render();
    }
  }

  pasteBlock() {
    if (!this.copiedBlock) return;
    const pw = this.document.page_width || 1000;
    const ph = this.document.page_height || 1400;

    const bbox = this.copiedBlock.bbox || [100, 100, 300, 200];
    const width = bbox[2] - bbox[0];
    const height = bbox[3] - bbox[1];
    
    const newX1 = Math.round(Math.min(pw - width, bbox[0] + pw * 0.05));
    const newY1 = Math.round(Math.min(ph - height, bbox[1] + ph * 0.05));

    const pasted = {
      ...this.copiedBlock,
      id: `block-${Math.random().toString(36).substr(2, 9)}`,
      bbox: [newX1, newY1, newX1 + width, newY1 + height],
      reading_order: (this.document.blocks || []).length + 1,
      manually_edited: true
    };

    this.document.blocks.push(pasted);
    this.selectedId = pasted.id;
    this.triggerChange();
    this._render();
    this.focusBlock(pasted.id);
  }

  deleteSelectedBlock() {
    if (!this.selectedId) return;
    if (confirm('Are you sure you want to delete the selected block?')) {
      this.document.blocks = (this.document.blocks || []).filter(b => b.id !== this.selectedId);
      this.selectedId = null;
      this.triggerChange();
      this._render();
    }
  }

  adjustBlockHeightToContent(blockId) {
    const el = this.container.querySelector(`[data-block-id="${blockId}"]`);
    if (!el) return;

    const page = this.container.querySelector('.ocr-replica-page');
    if (!page) return;

    const pageH = page.clientHeight;
    const doc = this.document;
    const ph = doc.page_height || 1400;

    const currentHeightPx = el.clientHeight;
    const contentHeightPx = el.scrollHeight;

    // Automatically expand the box height if text overflows the current bounds
    if (contentHeightPx > currentHeightPx) {
      const topPct = parseFloat(el.style.top) || 0;
      const newHeightPct = (contentHeightPx / pageH) * 100;
      
      el.style.height = `${newHeightPct.toFixed(2)}%`;

      const rx1 = (parseFloat(el.style.left) / 100) * (doc.page_width || 1000);
      const ry1 = (topPct / 100) * ph;
      const rx2 = rx1 + (parseFloat(el.style.width) / 100) * (doc.page_width || 1000);
      const ry2 = ry1 + (newHeightPct / 100) * ph;

      const originalBlock = this.document.blocks.find(b => b.id === blockId);
      if (originalBlock) {
        originalBlock.bbox = [Math.round(rx1), Math.round(ry1), Math.round(rx2), Math.round(ry2)];
        originalBlock.manually_edited = true;
      }
    }
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

    // Create layout toolbar
    const toolbar = document.createElement('div');
    toolbar.className = 'ocr-replica-toolbar flex flex-wrap gap-2 mb-3 p-2 bg-slate-50 rounded border border-slate-200 items-center shadow-sm';
    toolbar.style.position = 'sticky';
    toolbar.style.top = '0';
    toolbar.style.left = '0';
    toolbar.style.zIndex = '20';
    toolbar.style.width = `${10000 / this.zoom}%`;
    
    // Add Text Block button
    const addTextBtn = document.createElement('button');
    addTextBtn.type = 'button';
    addTextBtn.className = 'ocr-replica-toolbar-btn';
    addTextBtn.title = 'Add Text';
    addTextBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    `;
    addTextBtn.addEventListener('click', () => this.addTextBlock());
    toolbar.appendChild(addTextBtn);

    // Add Image Block button
    const addImageBtn = document.createElement('button');
    addImageBtn.type = 'button';
    addImageBtn.className = 'ocr-replica-toolbar-btn';
    addImageBtn.title = 'Add Image';
    addImageBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path>
      </svg>
    `;
    addImageBtn.addEventListener('click', () => this.triggerImageUpload());
    toolbar.appendChild(addImageBtn);

    // Add Table Block button
    const addTableBtn = document.createElement('button');
    addTableBtn.type = 'button';
    addTableBtn.className = 'ocr-replica-toolbar-btn';
    addTableBtn.title = 'Add Table';
    addTableBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 10h18M3 14h18m-9-4v8m-7 0h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"></path>
      </svg>
    `;
    addTableBtn.addEventListener('click', () => this.addTableBlock());
    toolbar.appendChild(addTableBtn);

    // Copy Block button
    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'ocr-replica-toolbar-btn';
    copyBtn.disabled = !this.selectedId;
    copyBtn.title = 'Copy';
    copyBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M8 7v8a2 2 0 002 2h6M8 7V5a2 2 0 012-2h4.586a1 1 0 01.707.293l4.414 4.414a1 1 0 01.293.707V15a2 2 0 01-2 2h-2M8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h8a2 2 0 002-2v-2"></path>
      </svg>
    `;
    copyBtn.addEventListener('click', () => this.copySelectedBlock());
    toolbar.appendChild(copyBtn);

    // Paste Block button
    const pasteBtn = document.createElement('button');
    pasteBtn.type = 'button';
    pasteBtn.className = 'ocr-replica-toolbar-btn';
    pasteBtn.disabled = !this.copiedBlock;
    pasteBtn.title = 'Paste';
    pasteBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path>
      </svg>
    `;
    pasteBtn.addEventListener('click', () => this.pasteBlock());
    toolbar.appendChild(pasteBtn);

    // Separator helper
    const createSeparator = () => {
      const sep = document.createElement('span');
      sep.className = 'w-px h-5 bg-slate-200 mx-1';
      return sep;
    };

    // Add Separator
    toolbar.appendChild(createSeparator());

    // Text formatting function helper
    const createFormatBtn = (labelOrHtml, command, value = null, isItalic = false, title = '') => {
      const btn = document.createElement('button');
      btn.type = 'button';
      if (title) btn.title = title;
      btn.className = `ocr-replica-toolbar-btn px-2.5 min-w-[28px] text-center justify-center font-bold ${isItalic ? 'italic' : ''}`;
      btn.disabled = !this.selectedId;
      
      if (labelOrHtml.startsWith('<svg')) {
        btn.innerHTML = labelOrHtml;
      } else {
        btn.textContent = labelOrHtml;
      }

      btn.addEventListener('mousedown', (e) => {
        e.preventDefault(); // Retain focus in contentEditable block
      });
      btn.addEventListener('click', () => {
        if (!this.selectedId) return;
        const el = this.container.querySelector(`[data-block-id="${this.selectedId}"]`);
        if (el && el.isContentEditable) {
          el.focus();
          document.execCommand(command, false, value);
          el.dispatchEvent(new Event('input', { bubbles: true }));
        }
      });
      return btn;
    };

    // Add format buttons
    toolbar.appendChild(createFormatBtn('B', 'bold', null, false, 'Bold'));
    toolbar.appendChild(createFormatBtn('I', 'italic', null, true, 'Italic'));
    toolbar.appendChild(createFormatBtn('H1', 'formatBlock', '<h1>', false, 'Heading 1'));
    toolbar.appendChild(createFormatBtn('H2', 'formatBlock', '<h2>', false, 'Heading 2'));
    toolbar.appendChild(createFormatBtn('H3', 'formatBlock', '<h3>', false, 'Heading 3'));
    toolbar.appendChild(createFormatBtn('P', 'formatBlock', '<p>', false, 'Paragraph'));

    // Add Separator
    toolbar.appendChild(createSeparator());

    // SVG icons for alignments
    const svgLeft = `<svg class="w-3.5 h-3.5 text-slate-600" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M4 12h10M4 18h14"></path></svg>`;
    const svgCenter = `<svg class="w-3.5 h-3.5 text-slate-600" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M7 12h10M6 18h12"></path></svg>`;
    const svgRight = `<svg class="w-3.5 h-3.5 text-slate-600" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" d="M4 6h16M10 12h10M6 18h14"></path></svg>`;

    // Add Alignment buttons (Left, Center, Right) using SVGs
    toolbar.appendChild(createFormatBtn(svgLeft, 'justifyLeft', null, false, 'Align Left'));
    toolbar.appendChild(createFormatBtn(svgCenter, 'justifyCenter', null, false, 'Align Center'));
    toolbar.appendChild(createFormatBtn(svgRight, 'justifyRight', null, false, 'Align Right'));

    // Add Separator
    toolbar.appendChild(createSeparator());

    // Toggle Move Mode button
    const moveModeBtn = document.createElement('button');
    moveModeBtn.type = 'button';
    moveModeBtn.className = `ocr-replica-toolbar-btn ${this.moveMode ? 'bg-blue-600 text-white border-blue-700 font-semibold shadow-inner hover:bg-blue-700' : ''}`;
    moveModeBtn.title = `Move Mode: ${this.moveMode ? 'ON' : 'OFF'}`;
    moveModeBtn.innerHTML = `
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="w-3.5 h-3.5">
        <path d="M12 2v20"/>
        <path d="m15 19-3 3-3-3"/>
        <path d="m19 9 3 3-3 3"/>
        <path d="M2 12h20"/>
        <path d="m5 9-3 3 3 3"/>
        <path d="m9 5 3-3 3 3"/>
      </svg>
    `;
    moveModeBtn.addEventListener('click', () => {
      this.moveMode = !this.moveMode;
      this._render();
    });
    toolbar.appendChild(moveModeBtn);

    // Math/Formula Editor button
    const mathEditorBtn = document.createElement('button');
    mathEditorBtn.type = 'button';
    mathEditorBtn.className = 'ocr-replica-toolbar-btn';
    mathEditorBtn.disabled = !this.selectedId;
    mathEditorBtn.title = 'Math Editor';
    mathEditorBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"></path>
      </svg>
    `;
    mathEditorBtn.addEventListener('click', () => {
      if (!this.selectedId) return;
      const el = this.container.querySelector(`[data-block-id="${this.selectedId}"]`);
      if (el) {
        let selText = '';
        const selection = window.getSelection();
        if (selection.rangeCount > 0 && el.contains(selection.anchorNode)) {
          selText = selection.toString();
        }
        if (!selText) {
          selText = el.innerText || el.textContent || '';
        }
        
        const initialMathText = selText.replace(/^\$\$?|\$\$?$/g, '').trim();
        
        if (window.openMathEditorModal) {
          window.openMathEditorModal(initialMathText, (latex) => {
            if (latex && latex.trim()) {
              const mathString = `$${latex.trim()}$`;
              
              if (selection.rangeCount > 0 && el.contains(selection.anchorNode) && selection.toString()) {
                const range = selection.getRangeAt(0);
                range.deleteContents();
                range.insertNode(document.createTextNode(mathString));
              } else {
                el.innerText = mathString;
              }
              
              el.dispatchEvent(new Event('input', { bubbles: true }));
              
              const originalBlock = this.document.blocks.find(b => b.id === this.selectedId);
              if (originalBlock) {
                originalBlock.content = el.innerHTML;
                originalBlock.manually_edited = true;
              }
              this.triggerChange();
              this._render();
            }
          });
        }
      }
    });
    toolbar.appendChild(mathEditorBtn);

    // Delete Block button
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'ocr-replica-toolbar-btn text-rose-600 border-rose-200 hover:bg-rose-50';
    deleteBtn.disabled = !this.selectedId;
    deleteBtn.title = 'Delete';
    deleteBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
      </svg>
    `;
    deleteBtn.addEventListener('click', () => this.deleteSelectedBlock());
    toolbar.appendChild(deleteBtn);

    // Add Separator
    toolbar.appendChild(createSeparator());

    // Zoom Out button
    const zoomOutBtn = document.createElement('button');
    zoomOutBtn.type = 'button';
    zoomOutBtn.className = 'ocr-replica-toolbar-btn px-2';
    zoomOutBtn.title = 'Zoom Out';
    zoomOutBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M20 12H4"></path>
      </svg>
    `;
    zoomOutBtn.disabled = this.zoom <= 50;
    zoomOutBtn.addEventListener('click', () => {
      this.zoom = Math.max(50, this.zoom - 25);
      localStorage.setItem('replica-zoom', this.zoom.toString());
      this._render();
    });
    toolbar.appendChild(zoomOutBtn);

    const zoomVal = document.createElement('span');
    zoomVal.className = 'text-xs font-semibold text-slate-600 min-w-[36px] text-center';
    zoomVal.textContent = `${this.zoom}%`;
    toolbar.appendChild(zoomVal);

    // Zoom In button
    const zoomInBtn = document.createElement('button');
    zoomInBtn.type = 'button';
    zoomInBtn.className = 'ocr-replica-toolbar-btn px-2';
    zoomInBtn.title = 'Zoom In';
    zoomInBtn.innerHTML = `
      <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"></path>
      </svg>
    `;
    zoomInBtn.disabled = this.zoom >= 300;
    zoomInBtn.addEventListener('click', () => {
      this.zoom = Math.min(300, this.zoom + 25);
      localStorage.setItem('replica-zoom', this.zoom.toString());
      this._render();
    });
    toolbar.appendChild(zoomInBtn);

    // Render Cache alert message if restored from localStorage
    if (this.isRestoredFromCache) {
      const cacheSeparator = document.createElement('span');
      cacheSeparator.className = 'w-px h-5 bg-slate-200 mx-1';
      toolbar.appendChild(cacheSeparator);

      const cacheAlert = document.createElement('span');
      cacheAlert.className = 'text-amber-600 text-xs font-medium ml-1 flex items-center gap-2';
      cacheAlert.innerHTML = `
        <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
        </svg>
        <span>Unsaved edits restored</span>
      `;
      toolbar.appendChild(cacheAlert);

      const discardBtn = document.createElement('button');
      discardBtn.type = 'button';
      discardBtn.className = 'ocr-replica-toolbar-btn text-slate-500 border-slate-200 hover:bg-slate-50';
      discardBtn.textContent = 'Discard';
      discardBtn.addEventListener('click', () => this.discardCachedEdits());
      toolbar.appendChild(discardBtn);
    }

    const toolbarContainer = document.getElementById('ocr-replica-toolbar-container');
    if (toolbarContainer) {
      toolbarContainer.innerHTML = '';
      toolbarContainer.appendChild(toolbar);
      
      // Reset position/sticky styles since the container sits outside the scroll area
      toolbar.style.position = '';
      toolbar.style.top = '';
      toolbar.style.left = '';
      toolbar.style.width = '100%';
      
      // Strip outer framing classes to merge seamlessly as a clean ribbon bar
      toolbar.classList.remove('mb-3', 'border', 'rounded', 'shadow-sm');
      toolbar.classList.add('border-0', 'shadow-none', 'm-0', 'bg-transparent');
    } else {
      this.container.appendChild(toolbar);
    }

    if (this.moveMode) {
      const moveBanner = document.createElement('div');
      moveBanner.className = 'w-full bg-blue-50 text-blue-700 text-xs font-semibold py-2 px-3.5 mb-3 rounded-xl border border-blue-200 flex items-center gap-2 shrink-0 shadow-sm transition-all duration-300';
      moveBanner.innerHTML = `
        <svg class="w-4 h-4 text-blue-500 animate-pulse shrink-0" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"></path>
        </svg>
        <span>Move Mode Active: Drag blocks to reposition, or drag the bottom-right handle to resize them. Click the move icon in the toolbar again to exit.</span>
      `;
      if (toolbarContainer) {
        toolbarContainer.appendChild(moveBanner);
      } else {
        this.container.appendChild(moveBanner);
      }
    }

    const page = document.createElement('div');
    page.className = 'ocr-replica-page book-editor-text relative mx-auto';
    if (this.moveMode) {
      page.classList.add('move-mode-active');
    }
    page.style.background = '#faf8f5';
    page.style.aspectRatio = `${pw} / ${ph}`;
    if (this.zoom > 100) {
      page.style.maxWidth = 'none';
      page.style.width = `${this.zoom}%`;
    } else {
      page.style.maxWidth = '100%';
      page.style.width = `${this.zoom}%`;
    }
    page.style.minHeight = '400px';

    blocks.forEach((block) => {
      const [x1, y1, x2, y2] = block.bbox || [0, 0, 0, 0];
      const isTable =
        block.type === 'table' || /<table[\s>]/i.test(String(block.content || ''));
      const isImageBlock = /<img[\s>]/i.test(String(block.content || ''));

      const conf = block.confidence;
      const confClass = (conf == null || block.manually_edited) ? ''
        : conf < 0.5 ? 'ocr-conf-low' : conf < 0.75 ? 'ocr-conf-mid' : '';
      const editedClass = block.manually_edited ? 'ocr-block-edited' : '';

      const isSelected = this.selectedId === block.id;

      const el = document.createElement('div');
      el.title = blockProvenanceLabel(block);
      el.className = `ocr-replica-block book-editor-text absolute overflow-auto text-base leading-relaxed p-1 ocr-replica-block--${block.type || 'paragraph'} ${confClass} ${editedClass} ${
        isSelected
          ? 'is-selected ring-2 ring-amber-600 z-10 bg-amber-50'
          : 'bg-white hover:bg-amber-50'
      }`;
      el.setAttribute('lang', block.language || 'und');
      el.dataset.blockId = block.id;
      el.dataset.blockType = block.type || 'paragraph';
      if (x2 > x1 && y2 > y1) {
        el.style.left = `${(100 * x1) / pw}%`;
        el.style.top = `${(100 * y1) / ph}%`;
        el.style.width = `${(100 * (x2 - x1)) / pw}%`;
        el.style.height = `${(100 * (y2 - y1)) / ph}%`;
      } else {
        el.style.position = 'relative';
        el.style.width = '100%';
        el.style.marginBottom = '0.5rem';
      }
      if (isTable) {
        el.innerHTML = blockReplicaInnerHtml(block);

        // Make table cells contentEditable so users can edit cell values directly in replica layout!
        el.querySelectorAll('td, th').forEach((cell) => {
          cell.contentEditable = 'true';
          cell.addEventListener('input', () => {
            const originalBlock = this.document.blocks.find(b => b.id === block.id);
            if (originalBlock) {
              // Read updated HTML (remove the "Edit in Flow" button before saving)
              const clone = el.cloneNode(true);
              const hintBtn = clone.querySelector('.ocr-table-flow-hint');
              if (hintBtn) hintBtn.remove();
              originalBlock.content = clone.innerHTML;
              originalBlock.manually_edited = true;
            }
            this.triggerChange();
          });
        });

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
        // If Move Mode or Image Block is active, disable contentEditable so text editing doesn't block moves
        el.contentEditable = (this.moveMode || isImageBlock) ? 'false' : 'true';
        
        // Render block content as HTML always to preserve bold/italic/headings formattings from user or OCR engine
        // Render raw LaTeX in edit/focused mode to prevent KaTeX HTML corruption, and compiled KaTeX in view mode.
        el.innerHTML = isSelected ? (block.content || '') : autoWrapMath(block.content || '');
        el.addEventListener('input', () => {
          const originalBlock = this.document.blocks.find(b => b.id === block.id);
          if (originalBlock) {
            originalBlock.content = el.innerHTML;
            originalBlock.manually_edited = true;
          }
          this.adjustBlockHeightToContent(block.id);
          this.triggerChange();
        });
      }

      if (isSelected) {
        const handle = document.createElement('div');
        handle.className = 'ocr-replica-resize-handle';
        el.appendChild(handle);
      }

      // Selection and focus listener
      el.addEventListener('focus', () => {
        if (this.selectedId !== block.id) {
          this.selectedId = block.id;
          this.onSelect(block);
          this._render();
        }
      });

      // Mouse drag-to-move / drag-to-resize listener
      el.addEventListener('mousedown', (e) => {
        const isResizeHandle = e.target.classList.contains('ocr-replica-resize-handle');
        
        if (this.selectedId !== block.id) {
          return;
        }

        // Avoid blocking text selection in editable blocks unless Alt key is held, resizing, Move Mode, or Image Block is active
        if (!this.moveMode && block.type !== 'figure' && block.type !== 'table' && !isImageBlock && el.contentEditable === 'true' && !e.altKey && !isResizeHandle) {
          return;
        }

        e.preventDefault();
        e.stopPropagation();

        const startX = e.clientX;
        const startY = e.clientY;
        const startLeft = parseFloat(el.style.left) || 0;
        const startTop = parseFloat(el.style.top) || 0;
        const startWidth = parseFloat(el.style.width) || 0;
        const startHeight = parseFloat(el.style.height) || 0;

        let isDragging = !isResizeHandle;
        let isResizing = isResizeHandle;

        const onMouseMove = (moveEvent) => {
          const dx = moveEvent.clientX - startX;
          const dy = moveEvent.clientY - startY;

          const pageW = page.clientWidth;
          const pageH = page.clientHeight;

          const deltaLeftPct = (dx / pageW) * 100;
          const deltaTopPct = (dy / pageH) * 100;

          if (isDragging) {
            let newLeft = startLeft + deltaLeftPct;
            let newTop = startTop + deltaTopPct;
            newLeft = Math.max(0, Math.min(100 - startWidth, newLeft));
            newTop = Math.max(0, Math.min(100 - startHeight, newTop));

            el.style.left = `${newLeft.toFixed(2)}%`;
            el.style.top = `${newTop.toFixed(2)}%`;
          } else if (isResizing) {
            let newWidth = startWidth + deltaLeftPct;
            let newHeight = startHeight + deltaTopPct;
            newWidth = Math.max(2, Math.min(100 - startLeft, newWidth));
            newHeight = Math.max(2, Math.min(100 - startTop, newHeight));

            el.style.width = `${newWidth.toFixed(2)}%`;
            el.style.height = `${newHeight.toFixed(2)}%`;
          }
        };

        const onMouseUp = () => {
          document.removeEventListener('mousemove', onMouseMove);
          document.removeEventListener('mouseup', onMouseUp);

          if (isDragging || isResizing) {
            isDragging = false;
            isResizing = false;

            const newLeft = parseFloat(el.style.left);
            const newTop = parseFloat(el.style.top);
            const newWidth = parseFloat(el.style.width);
            const newHeight = parseFloat(el.style.height);

            const rx1 = (newLeft / 100) * pw;
            const ry1 = (newTop / 100) * ph;
            const rx2 = rx1 + (newWidth / 100) * pw;
            const ry2 = ry1 + (newHeight / 100) * ph;

            const originalBlock = this.document.blocks.find(b => b.id === block.id);
            if (originalBlock) {
              originalBlock.bbox = [Math.round(rx1), Math.round(ry1), Math.round(rx2), Math.round(ry2)];
              originalBlock.manually_edited = true;
            }
            this.triggerChange();
          }
        };

        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
      });

      page.appendChild(el);
    });

    if (!blocks.length) {
      page.innerHTML =
        '<p class="absolute inset-0 flex items-center justify-center text-slate-400 text-sm">Run OCR to see replica layout</p>';
    }

    this.container.appendChild(page);

    try {
      renderMathInElement(page, {
        delimiters: [
          { left: '$$', right: '$$', display: true },
          { left: '$', right: '$', display: false },
          { left: '\\(', right: '\\)', display: false },
          { left: '\\[', right: '\\[', display: true },
        ],
        ignoredClasses: ['is-selected'],
        throwOnError: false,
      });
    } catch (e) {
      console.warn('KaTeX render failed:', e);
    }

    // Automatically expand heights for text boxes on initial load if text exceeds default OCR dimensions
    requestAnimationFrame(() => {
      blocks.forEach((block) => {
        if (block.type !== 'figure' && block.type !== 'table' && !/img/i.test(block.content)) {
          this.adjustBlockHeightToContent(block.id);
        }
      });
    });
  }
}
