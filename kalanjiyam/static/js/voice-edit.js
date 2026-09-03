/**
 * Applying voice edit operations to a PageDocument.
 *
 * Deliberately pure: no DOM, no Alpine, no network. This is the code that
 * decides whether someone's manuscript text changes, so it has to be
 * exhaustively testable without a browser.
 *
 * Every operation is *verified before it is applied*. The service already
 * validates shape and block ids, but only the client holds the live text, so
 * only the client can confirm that a `find` string still matches. An operation
 * that fails verification is recorded in `rejected` with a reason and shown to
 * the user -- never silently dropped, because a correction the user believes
 * was made is worse than one they can see failed.
 *
 * See docs/voice-edit-service-contract.rst.
 */

/** Operations we know how to apply. Anything else is rejected. */
export const KNOWN_OPS = [
  'replace',
  'replace_block',
  'append',
  'insert_after',
  'insert_before',
  'delete_block',
  'set_language',
];

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function newBlockId() {
  return `block-${Math.random().toString(36).slice(2, 11)}`;
}

/** Index of the nth (1-based) occurrence of `needle`, or -1. */
function nthIndexOf(haystack, needle, occurrence) {
  let index = -1;
  for (let i = 0; i < occurrence; i += 1) {
    index = haystack.indexOf(needle, index + (i === 0 ? 0 : 1));
    if (index === -1) return -1;
  }
  return index;
}

/**
 * Build a new block, copying page geometry from a sibling so it renders in a
 * sensible place in replica view rather than at the origin.
 */
function makeBlock(sibling, content, readingOrder) {
  return {
    id: newBlockId(),
    type: 'paragraph',
    bbox: sibling && sibling.bbox ? sibling.bbox.slice() : [0, 0, 0, 0],
    content,
    reading_order: readingOrder,
    children: [],
    manually_edited: true,
  };
}

/** Renumber reading_order densely from 1 after an insert or delete. */
function renumber(blocks) {
  blocks
    .slice()
    .sort((a, b) => (a.reading_order || 0) - (b.reading_order || 0))
    .forEach((block, i) => {
      block.reading_order = i + 1;
    });
}

/**
 * Apply a list of operations to a document.
 *
 * Operates on a deep clone: the caller's document is never mutated, so a run
 * that ends in rejections leaves the editor exactly as it was.
 *
 * @param {object} doc  a PageDocument ({page_width, page_height, blocks, ...})
 * @param {Array<object>} ops
 * @returns {{doc: object, applied: Array, rejected: Array}}
 *   `applied` entries are {op, block_id, before, after} -- enough to highlight
 *   the change and to describe it in the review list.
 */
export function applyOps(doc, ops) {
  const next = clone(doc || {});
  if (!Array.isArray(next.blocks)) next.blocks = [];

  const applied = [];
  const rejected = [];

  const reject = (op, reason) => rejected.push({ op, reason });

  (Array.isArray(ops) ? ops : []).forEach((op) => {
    if (!op || typeof op !== 'object') {
      reject(op, 'malformed operation');
      return;
    }
    if (!KNOWN_OPS.includes(op.op)) {
      reject(op, `unknown operation "${op.op}"`);
      return;
    }

    const index = next.blocks.findIndex((b) => b.id === op.block_id);
    if (index === -1) {
      reject(op, `no block "${op.block_id}"`);
      return;
    }
    const block = next.blocks[index];
    const before = block.content || '';

    switch (op.op) {
      case 'replace': {
        const find = String(op.find == null ? '' : op.find);
        if (!find) {
          reject(op, 'empty search text');
          return;
        }
        const occurrence = Math.max(1, parseInt(op.occurrence, 10) || 1);
        const at = nthIndexOf(before, find, occurrence);
        if (at === -1) {
          // Rule 1 of the contract. Usually means the model normalised
          // diacritics or whitespace instead of copying the text verbatim.
          const count = before.split(find).length - 1;
          reject(
            op,
            count === 0
              ? `"${find}" is not in this block`
              : `"${find}" occurs ${count} time(s), not ${occurrence}`,
          );
          return;
        }
        const replacement = String(op.replace == null ? '' : op.replace);
        block.content = before.slice(0, at) + replacement + before.slice(at + find.length);
        break;
      }

      case 'replace_block':
        block.content = String(op.content == null ? '' : op.content);
        break;

      case 'append': {
        const addition = String(op.content == null ? '' : op.content);
        // Join with a space unless the block is empty or already ends in
        // whitespace -- dictation should not fuse words together.
        const needsSpace = before.length > 0 && !/\s$/.test(before);
        block.content = before + (needsSpace ? ' ' : '') + addition;
        break;
      }

      case 'insert_after':
      case 'insert_before': {
        const content = String(op.content == null ? '' : op.content);
        const created = makeBlock(block, content, block.reading_order || 0);
        const at = op.op === 'insert_after' ? index + 1 : index;
        next.blocks.splice(at, 0, created);
        renumber(next.blocks);
        applied.push({
          op, block_id: created.id, before: null, after: content, created: true,
        });
        return;
      }

      case 'delete_block':
        next.blocks.splice(index, 1);
        renumber(next.blocks);
        applied.push({
          op, block_id: op.block_id, before, after: null, deleted: true,
        });
        return;

      case 'set_language':
        block.language = String(op.language == null ? '' : op.language) || null;
        applied.push({
          op, block_id: block.id, before, after: before, language: block.language,
        });
        return;

      default:
        reject(op, `unknown operation "${op.op}"`);
        return;
    }

    // Mark the block as human-touched. Voice edits are the user's edits: they
    // spoke them, reviewed them, and own them.
    block.manually_edited = true;
    applied.push({
      op, block_id: block.id, before, after: block.content,
    });
  });

  return { doc: next, applied, rejected };
}

/**
 * A bounded stack of document snapshots.
 *
 * The proofing editor has no app-level undo today -- only TipTap's, in flow
 * mode. A feature that rewrites text on the user's behalf cannot ship without
 * one, so voice mode brings its own.
 */
export class UndoStack {
  constructor(limit = 20) {
    this.limit = limit;
    this.entries = [];
  }

  /** Snapshot before a change. `label` is shown in the review list. */
  push(doc, label = '') {
    this.entries.push({ doc: clone(doc), label });
    if (this.entries.length > this.limit) this.entries.shift();
  }

  /** Most recent snapshot, or null. */
  pop() {
    const entry = this.entries.pop();
    return entry ? entry.doc : null;
  }

  get canUndo() {
    return this.entries.length > 0;
  }

  clear() {
    this.entries = [];
  }
}

/** Short human description of an operation, for the review list. */
export function describeOp(op) {
  switch (op && op.op) {
    case 'replace':
      return `"${op.find}" → "${op.replace}"`;
    case 'replace_block':
      return 'rewrote block';
    case 'append':
      return 'added text';
    case 'insert_after':
    case 'insert_before':
      return 'new block';
    case 'delete_block':
      return 'deleted block';
    case 'set_language':
      return `language → ${op.language}`;
    default:
      return 'edit';
  }
}
