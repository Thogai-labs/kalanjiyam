/* Block list editor panel. */

import { newBlockId, reorderBlocks } from './page-document.js';

export class BlockEditor {
  constructor(container, options = {}) {
    this.container = container;
    this.onChange = options.onChange || (() => {});
    this.onSelect = options.onSelect || (() => {});
    this.document = { blocks: [] };
    this.selectedId = null;
    this._render();
  }

  setDocument(doc) {
    this.document = doc;
    this._render();
  }

  getDocument() {
    return this.document;
  }

  highlightBlock(blockId) {
    this.selectedId = blockId;
    this._render();
    const row = this.container.querySelector(`[data-block-id="${blockId}"]`);
    if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  _render() {
    const blocks = [...(this.document.blocks || [])].sort(
      (a, b) => (a.reading_order || 0) - (b.reading_order || 0),
    );
    this.container.innerHTML = '';
    if (!blocks.length) {
      this.container.innerHTML =
        '<p class="text-sm text-slate-400 p-4">No blocks yet. Run OCR to populate.</p>';
      return;
    }

    blocks.forEach((block, index) => {
      const row = document.createElement('div');
      row.className = `ocr-block-row border rounded-lg p-3 mb-2 ${
        this.selectedId === block.id
          ? 'border-royalblue bg-blue-50'
          : 'border-slate-200 bg-white'
      }`;
      row.dataset.blockId = block.id;

      const header = document.createElement('div');
      header.className = 'flex items-center gap-2 mb-2';
      header.innerHTML = `
        <select class="text-xs border rounded px-1 py-0.5 ocr-block-type">
          <option value="paragraph">Paragraph</option>
          <option value="heading">Heading</option>
          <option value="verse">Verse</option>
          <option value="table">Table</option>
          <option value="list_item">List</option>
        </select>
        <span class="text-xs text-slate-400">#${index + 1}</span>
        <div class="ml-auto flex gap-1">
          <button type="button" class="text-xs px-2 py-0.5 border rounded ocr-block-up" title="Move up">↑</button>
          <button type="button" class="text-xs px-2 py-0.5 border rounded ocr-block-down" title="Move down">↓</button>
        </div>
      `;
      const typeSelect = header.querySelector('.ocr-block-type');
      typeSelect.value = block.type || 'paragraph';
      typeSelect.addEventListener('change', () => {
        block.type = typeSelect.value;
        this._emitChange();
      });
      header.querySelector('.ocr-block-up').addEventListener('click', (e) => {
        e.preventDefault();
        this._moveBlock(block.id, -1);
      });
      header.querySelector('.ocr-block-down').addEventListener('click', (e) => {
        e.preventDefault();
        this._moveBlock(block.id, 1);
      });

      const textarea = document.createElement('textarea');
      textarea.className =
        'w-full text-sm border border-slate-200 rounded p-2 min-h-[4rem] font-sans';
      textarea.value = block.content || '';
      textarea.addEventListener('input', () => {
        block.content = textarea.value;
        this._emitChange();
      });
      textarea.addEventListener('focus', () => {
        this.selectedId = block.id;
        this.onSelect(block);
        this._render();
      });

      row.appendChild(header);
      row.appendChild(textarea);
      row.addEventListener('click', () => {
        this.selectedId = block.id;
        this.onSelect(block);
        this._render();
      });
      this.container.appendChild(row);
    });
  }

  _moveBlock(id, delta) {
    const blocks = [...(this.document.blocks || [])].sort(
      (a, b) => (a.reading_order || 0) - (b.reading_order || 0),
    );
    const idx = blocks.findIndex((b) => b.id === id);
    if (idx < 0) return;
    const next = idx + delta;
    if (next < 0 || next >= blocks.length) return;
    [blocks[idx], blocks[next]] = [blocks[next], blocks[idx]];
    reorderBlocks(blocks);
    this.document.blocks = blocks;
    this._emitChange();
    this._render();
  }

  _emitChange() {
    this.onChange(this.document);
  }
}

export function addEmptyBlock(doc) {
  const block = {
    id: newBlockId(),
    type: 'paragraph',
    bbox: [0, 0, 0, 0],
    content: '',
    reading_order: (doc.blocks?.length || 0) + 1,
    children: [],
  };
  doc.blocks = doc.blocks || [];
  doc.blocks.push(block);
  return block;
}
