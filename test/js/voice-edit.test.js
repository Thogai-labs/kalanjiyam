import { applyOps, UndoStack, describeOp } from '@/voice-edit';

/* A small two-block page, in Tamil, mirroring what replica mode holds. */
function makeDoc() {
  return {
    page_width: 1000,
    page_height: 1400,
    content_format: 'blocks',
    blocks: [
      {
        id: 'block-a',
        type: 'paragraph',
        bbox: [10, 10, 500, 60],
        content: 'சித்த மருத்துவம் rama',
        reading_order: 1,
        children: [],
      },
      {
        id: 'block-b',
        type: 'paragraph',
        bbox: [10, 80, 500, 130],
        content: 'rama dasa and rama deva',
        reading_order: 2,
        children: [],
      },
    ],
  };
}

const blockById = (doc, id) => doc.blocks.find((b) => b.id === id);

describe('applyOps — replace', () => {
  test('replaces an exact match and stamps manually_edited', () => {
    const doc = makeDoc();
    const { doc: next, applied, rejected } = applyOps(doc, [
      { op: 'replace', block_id: 'block-a', find: 'rama', replace: 'rāma' },
    ]);

    expect(rejected).toHaveLength(0);
    expect(applied).toHaveLength(1);
    expect(blockById(next, 'block-a').content).toBe('சித்த மருத்துவம் rāma');
    expect(blockById(next, 'block-a').manually_edited).toBe(true);
    expect(applied[0]).toMatchObject({
      block_id: 'block-a',
      before: 'சித்த மருத்துவம் rama',
      after: 'சித்த மருத்துவம் rāma',
    });
  });

  test('does not mutate the input document', () => {
    const doc = makeDoc();
    const snapshot = JSON.stringify(doc);
    applyOps(doc, [{ op: 'replace', block_id: 'block-a', find: 'rama', replace: 'rāma' }]);
    expect(JSON.stringify(doc)).toBe(snapshot);
  });

  test('rejects a find that is not present, leaving the block untouched', () => {
    const doc = makeDoc();
    const { doc: next, applied, rejected } = applyOps(doc, [
      { op: 'replace', block_id: 'block-a', find: 'raama', replace: 'rāma' },
    ]);

    expect(applied).toHaveLength(0);
    expect(rejected).toHaveLength(1);
    expect(rejected[0].reason).toMatch(/not in this block/);
    expect(blockById(next, 'block-a').content).toBe('சித்த மருத்துவம் rama');
    expect(blockById(next, 'block-a').manually_edited).toBeUndefined();
  });

  test('honours occurrence for a repeated term', () => {
    const doc = makeDoc();
    const { doc: next, rejected } = applyOps(doc, [
      {
        op: 'replace', block_id: 'block-b', find: 'rama', replace: 'rāma', occurrence: 2,
      },
    ]);

    expect(rejected).toHaveLength(0);
    expect(blockById(next, 'block-b').content).toBe('rama dasa and rāma deva');
  });

  test('rejects an occurrence beyond the number present', () => {
    const doc = makeDoc();
    const { applied, rejected } = applyOps(doc, [
      {
        op: 'replace', block_id: 'block-b', find: 'rama', replace: 'rāma', occurrence: 5,
      },
    ]);

    expect(applied).toHaveLength(0);
    expect(rejected[0].reason).toMatch(/occurs 2 time\(s\), not 5/);
  });

  test('rejects an empty find rather than inserting at position zero', () => {
    const { applied, rejected } = applyOps(makeDoc(), [
      { op: 'replace', block_id: 'block-a', find: '', replace: 'x' },
    ]);
    expect(applied).toHaveLength(0);
    expect(rejected[0].reason).toMatch(/empty search text/);
  });
});

describe('applyOps — validation', () => {
  test('rejects an unknown block id', () => {
    const doc = makeDoc();
    const { doc: next, applied, rejected } = applyOps(doc, [
      { op: 'replace', block_id: 'block-zzz', find: 'rama', replace: 'rāma' },
    ]);

    expect(applied).toHaveLength(0);
    expect(rejected[0].reason).toMatch(/no block "block-zzz"/);
    expect(next.blocks).toHaveLength(2);
  });

  test('rejects an unknown operation type', () => {
    const { applied, rejected } = applyOps(makeDoc(), [
      { op: 'reformat_everything', block_id: 'block-a' },
    ]);
    expect(applied).toHaveLength(0);
    expect(rejected[0].reason).toMatch(/unknown operation/);
  });

  test('rejects malformed entries', () => {
    const { applied, rejected } = applyOps(makeDoc(), [null, 'nonsense', 42]);
    expect(applied).toHaveLength(0);
    expect(rejected).toHaveLength(3);
  });

  test('applies the good operations in a mixed batch', () => {
    const { doc: next, applied, rejected } = applyOps(makeDoc(), [
      { op: 'replace', block_id: 'block-a', find: 'rama', replace: 'rāma' },
      { op: 'replace', block_id: 'block-a', find: 'nope', replace: 'x' },
    ]);

    expect(applied).toHaveLength(1);
    expect(rejected).toHaveLength(1);
    expect(blockById(next, 'block-a').content).toBe('சித்த மருத்துவம் rāma');
  });

  test('an empty op list is a clean no-op', () => {
    const doc = makeDoc();
    const { doc: next, applied, rejected } = applyOps(doc, []);
    expect(applied).toHaveLength(0);
    expect(rejected).toHaveLength(0);
    expect(next).toEqual(doc);
  });

  test('tolerates a missing or malformed ops argument', () => {
    expect(applyOps(makeDoc(), undefined).applied).toHaveLength(0);
    expect(applyOps(makeDoc(), null).rejected).toHaveLength(0);
  });

  test('tolerates a document with no blocks', () => {
    const { doc: next, rejected } = applyOps({}, [
      { op: 'replace', block_id: 'block-a', find: 'x', replace: 'y' },
    ]);
    expect(next.blocks).toEqual([]);
    expect(rejected).toHaveLength(1);
  });
});

describe('applyOps — other operations', () => {
  test('replace_block rewrites the whole block', () => {
    const { doc: next } = applyOps(makeDoc(), [
      { op: 'replace_block', block_id: 'block-a', content: 'புதிய வரி' },
    ]);
    expect(blockById(next, 'block-a').content).toBe('புதிய வரி');
    expect(blockById(next, 'block-a').manually_edited).toBe(true);
  });

  test('append adds a separating space', () => {
    const { doc: next } = applyOps(makeDoc(), [
      { op: 'append', block_id: 'block-a', content: 'dasa' },
    ]);
    expect(blockById(next, 'block-a').content).toBe('சித்த மருத்துவம் rama dasa');
  });

  test('append does not double an existing trailing space', () => {
    const doc = makeDoc();
    doc.blocks[0].content = 'ends with space ';
    const { doc: next } = applyOps(doc, [
      { op: 'append', block_id: 'block-a', content: 'more' },
    ]);
    expect(blockById(next, 'block-a').content).toBe('ends with space more');
  });

  test('append into an empty block does not lead with a space', () => {
    const doc = makeDoc();
    doc.blocks[0].content = '';
    const { doc: next } = applyOps(doc, [
      { op: 'append', block_id: 'block-a', content: 'first words' },
    ]);
    expect(blockById(next, 'block-a').content).toBe('first words');
  });

  test('insert_after places a new block and renumbers', () => {
    const { doc: next, applied } = applyOps(makeDoc(), [
      { op: 'insert_after', block_id: 'block-a', content: 'inserted' },
    ]);

    expect(next.blocks).toHaveLength(3);
    expect(next.blocks[1].content).toBe('inserted');
    expect(next.blocks.map((b) => b.reading_order)).toEqual([1, 2, 3]);
    expect(applied[0].created).toBe(true);
    expect(applied[0].block_id).toBe(next.blocks[1].id);
  });

  test('insert_before places a new block ahead of its anchor', () => {
    const { doc: next } = applyOps(makeDoc(), [
      { op: 'insert_before', block_id: 'block-b', content: 'inserted' },
    ]);
    expect(next.blocks[1].content).toBe('inserted');
    expect(next.blocks[2].id).toBe('block-b');
  });

  test('a new block inherits page geometry from its anchor', () => {
    const { doc: next } = applyOps(makeDoc(), [
      { op: 'insert_after', block_id: 'block-a', content: 'inserted' },
    ]);
    expect(next.blocks[1].bbox).toEqual([10, 10, 500, 60]);
    expect(next.blocks[1].manually_edited).toBe(true);
  });

  test('delete_block removes it and renumbers', () => {
    const { doc: next, applied } = applyOps(makeDoc(), [
      { op: 'delete_block', block_id: 'block-a' },
    ]);

    expect(next.blocks).toHaveLength(1);
    expect(next.blocks[0].id).toBe('block-b');
    expect(next.blocks[0].reading_order).toBe(1);
    expect(applied[0].deleted).toBe(true);
  });

  test('set_language writes the block language without touching content', () => {
    const { doc: next, applied } = applyOps(makeDoc(), [
      { op: 'set_language', block_id: 'block-a', language: 'ta' },
    ]);

    expect(blockById(next, 'block-a').language).toBe('ta');
    expect(blockById(next, 'block-a').content).toBe('சித்த மருத்துவம் rama');
    expect(applied[0].language).toBe('ta');
  });
});

describe('UndoStack', () => {
  test('restores the exact prior document', () => {
    const stack = new UndoStack();
    const doc = makeDoc();
    stack.push(doc, 'before edit');

    const { doc: edited } = applyOps(doc, [
      { op: 'replace', block_id: 'block-a', find: 'rama', replace: 'rāma' },
    ]);
    expect(blockById(edited, 'block-a').content).toContain('rāma');

    expect(stack.canUndo).toBe(true);
    expect(stack.pop()).toEqual(doc);
    expect(stack.canUndo).toBe(false);
  });

  test('snapshots are decoupled from later mutation', () => {
    const stack = new UndoStack();
    const doc = makeDoc();
    stack.push(doc);
    doc.blocks[0].content = 'mutated in place';
    expect(stack.pop().blocks[0].content).toBe('சித்த மருத்துவம் rama');
  });

  test('drops the oldest entry past the limit', () => {
    const stack = new UndoStack(2);
    stack.push({ blocks: [], tag: 1 });
    stack.push({ blocks: [], tag: 2 });
    stack.push({ blocks: [], tag: 3 });

    expect(stack.entries).toHaveLength(2);
    expect(stack.pop().tag).toBe(3);
    expect(stack.pop().tag).toBe(2);
  });

  test('pop on an empty stack returns null', () => {
    expect(new UndoStack().pop()).toBeNull();
  });

  test('clear empties the stack', () => {
    const stack = new UndoStack();
    stack.push(makeDoc());
    stack.clear();
    expect(stack.canUndo).toBe(false);
  });
});

describe('describeOp', () => {
  test('summarises each operation type', () => {
    expect(describeOp({ op: 'replace', find: 'a', replace: 'b' })).toBe('"a" → "b"');
    expect(describeOp({ op: 'delete_block' })).toBe('deleted block');
    expect(describeOp({ op: 'set_language', language: 'ta' })).toBe('language → ta');
    expect(describeOp({ op: 'mystery' })).toBe('edit');
    expect(describeOp(null)).toBe('edit');
  });
});
