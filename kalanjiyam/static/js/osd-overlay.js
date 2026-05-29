/* OpenSeadragon bounding-box overlay for OCR editing. */

import { findBlockForBbox } from './page-document.js';

function parseCoord(value) {
  const n = parseFloat(value);
  return Number.isFinite(n) ? n : NaN;
}

/** Scale boxes to image pixels when OCR returns normalized 0–1 coords. */
export function scaleBoxesToImage(boxes, imageWidth, imageHeight) {
  if (!boxes.length || !imageWidth || !imageHeight) {
    return boxes;
  }
  const maxCoord = boxes.reduce(
    (max, b) => Math.max(max, b.x1, b.y1, b.x2, b.y2),
    0,
  );
  // Normalized 0–1 (allow slight float overshoot)
  if (maxCoord > 0 && maxCoord <= 1.5) {
    return boxes.map((b) => ({
      ...b,
      x1: b.x1 * imageWidth,
      y1: b.y1 * imageHeight,
      x2: b.x2 * imageWidth,
      y2: b.y2 * imageHeight,
    }));
  }
  return boxes;
}

export class OsdBboxOverlay {
  constructor(viewer, options = {}) {
    this.viewer = viewer;
    this.onBoxClick = options.onBoxClick || (() => {});
    this.boxes = [];
    this.highlightedId = null;
    this._blocks = [];
    this._svg = null;
    this._group = null;
    this._handlers = [];
    this._install();
  }

  _install() {
    this._svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    this._svg.setAttribute('class', 'ocr-osd-overlay');
    this._svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
    this._svg.style.position = 'absolute';
    this._svg.style.left = '0';
    this._svg.style.top = '0';
    this._svg.style.width = '100%';
    this._svg.style.height = '100%';
    this._svg.style.pointerEvents = 'none';
    this._svg.style.zIndex = '10';

    // Coordinates from imageToViewerElementCoordinates are relative to viewer.element,
    // not viewer.canvas (which has its own transform stack).
    const container = this.viewer.element;
    if (getComputedStyle(container).position === 'static') {
      container.style.position = 'relative';
    }
    container.appendChild(this._svg);

    const redraw = () => this._redraw();
    this._handlers.push(['open', redraw]);
    this._handlers.push(['animation', redraw]);
    this._handlers.push(['animation-finish', redraw]);
    this._handlers.push(['resize', redraw]);
    this._handlers.push(['rotate', redraw]);
    this._handlers.push(['zoom', redraw]);
    this._handlers.forEach(([evt, fn]) => this.viewer.addHandler(evt, fn));
    redraw();
  }

  _getImageSize() {
    const item = this.viewer.world?.getItemAt(0);
    if (!item?.sourceDimensions) return null;
    return {
      width: item.sourceDimensions.x,
      height: item.sourceDimensions.y,
    };
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

  _scaledBoxes() {
    const size = this._getImageSize();
    if (!size) return this.boxes;
    return scaleBoxesToImage(this.boxes, size.width, size.height);
  }

  _redraw() {
    if (!this._svg || !this.viewer.viewport || !this.viewer.isOpen()) return;

    const container = this.viewer.element;
    const width = container.clientWidth;
    const height = container.clientHeight;
    this._svg.setAttribute('width', String(width));
    this._svg.setAttribute('height', String(height));
    this._svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

    while (this._svg.firstChild) {
      this._svg.removeChild(this._svg.firstChild);
    }
    this._group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    this._svg.appendChild(this._group);

    this._scaledBoxes().forEach((box) => {
      const rect = this._boxToViewerRect(box);
      if (!rect || rect.width < 1 || rect.height < 1) return;
      const el = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      el.setAttribute('x', rect.x);
      el.setAttribute('y', rect.y);
      el.setAttribute('width', rect.width);
      el.setAttribute('height', rect.height);
      el.setAttribute('fill', 'rgba(37, 99, 235, 0.15)');
      el.setAttribute('stroke', '#2563eb');
      el.setAttribute('stroke-width', '2');
      el.setAttribute('vector-effect', 'non-scaling-stroke');
      el.style.pointerEvents = 'all';
      el.style.cursor = 'pointer';
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        const bbox = [box.x1, box.y1, box.x2, box.y2];
        this.onBoxClick({
          bbox,
          box,
          block: findBlockForBbox(this._blocks || [], bbox),
        });
      });
      this._group.appendChild(el);
    });
  }

  setBlocksForMatching(blocks) {
    this._blocks = blocks;
  }

  _boxToViewerRect(box) {
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
