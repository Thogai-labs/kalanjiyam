/* OpenSeadragon bounding-box overlay for OCR editing. */

import { findBlockForBbox } from './page-document.js';

export class OsdBboxOverlay {
  constructor(viewer, options = {}) {
    this.viewer = viewer;
    this.onBoxClick = options.onBoxClick || (() => {});
    this.boxes = [];
    this.highlightedId = null;
    this._svg = null;
    this._group = null;
    this._handlers = [];
    this._install();
  }

  _install() {
    this._svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    this._svg.setAttribute('class', 'ocr-osd-overlay');
    this._svg.style.position = 'absolute';
    this._svg.style.left = '0';
    this._svg.style.top = '0';
    this._svg.style.width = '100%';
    this._svg.style.height = '100%';
    this._svg.style.pointerEvents = 'none';

    this.viewer.canvas.appendChild(this._svg);
    const redraw = () => this._redraw();
    this._handlers.push(['open', redraw]);
    this._handlers.push(['animation', redraw]);
    this._handlers.push(['resize', redraw]);
    this._handlers.push(['rotate', redraw]);
    this._handlers.forEach(([evt, fn]) => this.viewer.addHandler(evt, fn));
    redraw();
  }

  setBoxes(boxes) {
    this.boxes = boxes || [];
    this._redraw();
  }

  highlightBlockId(blockId) {
    this.highlightedId = blockId;
    this._redraw();
  }

  destroy() {
    this._handlers.forEach(([evt, fn]) => this.viewer.removeHandler(evt, fn));
    if (this._svg && this._svg.parentNode) {
      this._svg.parentNode.removeChild(this._svg);
    }
  }

  _redraw() {
    if (!this._svg || !this.viewer.viewport) return;
    while (this._svg.firstChild) {
      this._svg.removeChild(this._svg.firstChild);
    }
    this._group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    this._svg.appendChild(this._group);

    this.boxes.forEach((box) => {
      const rect = this._boxToViewportRect(box);
      if (!rect) return;
      const el = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      el.setAttribute('x', rect.x);
      el.setAttribute('y', rect.y);
      el.setAttribute('width', rect.width);
      el.setAttribute('height', rect.height);
      el.setAttribute('fill', 'rgba(37, 99, 235, 0.12)');
      el.setAttribute('stroke', '#2563eb');
      el.setAttribute('stroke-width', '1.5');
      el.setAttribute('vector-effect', 'non-scaling-stroke');
      el.style.pointerEvents = 'all';
      el.style.cursor = 'pointer';
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const bbox = [box.x1, box.y1, box.x2, box.y2];
        this.onBoxClick({ bbox, box, block: findBlockForBbox(this._blocks || [], bbox) });
      });
      this._group.appendChild(el);
    });
  }

  setBlocksForMatching(blocks) {
    this._blocks = blocks;
  }

  _boxToViewportRect(box) {
    try {
      const vp = this.viewer.viewport;
      const p1 = vp.imageToViewerElementCoordinates(
        new OpenSeadragon.Point(box.x1, box.y1),
      );
      const p2 = vp.imageToViewerElementCoordinates(
        new OpenSeadragon.Point(box.x2, box.y2),
      );
      return {
        x: Math.min(p1.x, p2.x),
        y: Math.min(p1.y, p2.y),
        width: Math.abs(p2.x - p1.x),
        height: Math.abs(p2.y - p1.y),
      };
    } catch {
      return null;
    }
  }
}
