import {
  isMarkdownTable,
  plainTextToHtmlTable,
  documentToFlowHtml,
  fromOcrPayload,
  blocksFromFlowHtml,
} from '@/page-document.js';
import { createRichEditor, getEditorContent, getEditorMarkdown, setEditorContent } from '@/rich-editor.js';

describe('Markdown Table Parsing and Rendering', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="rich-editor"></div>';
  });

  test('isMarkdownTable detects various Markdown table patterns', () => {
    const standardTable = '| Header 1 | Header 2 |\n|---|---|\n| Cell 1 | Cell 2 |';
    expect(isMarkdownTable(standardTable)).toBe(true);

    const alignedTable = '| Header 1 | Header 2 |\n| :--- | ---: |\n| Cell 1 | Cell 2 |';
    expect(isMarkdownTable(alignedTable)).toBe(true);

    const nonTable = 'This is just some regular text with a | pipe in it.';
    expect(isMarkdownTable(nonTable)).toBe(false);

    const empty = '';
    expect(isMarkdownTable(empty)).toBe(false);
  });

  test('plainTextToHtmlTable generates valid HTML table with th and td', () => {
    const markdownTable = '| Name | Age | Formula |\n| :--- | :--- | :--- |\n| Alice | 30 | x = y^2 |\n| Bob | 25 | E = mc^2 |';
    const html = plainTextToHtmlTable(markdownTable);

    expect(html).toContain('<table class="ocr-detected-table">');
    expect(html).toContain('<th>Name</th>');
    expect(html).toContain('<th>Age</th>');
    expect(html).toContain('<th>Formula</th>');
    expect(html).toContain('<td>Alice</td>');
    expect(html).toContain('<td>30</td>');
    expect(html).toContain('<td>Bob</td>');
  });

  test('documentToFlowHtml transforms markdown tables into table markup with data-block-id', () => {
    const doc = {
      content_format: 'blocks',
      blocks: [
        {
          id: 'b1',
          type: 'paragraph',
          content: 'Introductory text.',
          reading_order: 1,
        },
        {
          id: 'b2',
          type: 'paragraph',
          content: '| Col A | Col B |\n|---|---|\n| Val 1 | Val 2 |',
          reading_order: 2,
        },
      ],
    };

    const flowHtml = documentToFlowHtml(doc);
    expect(flowHtml).toContain('<p data-block-id="b1">Introductory text.</p>');
    expect(flowHtml).toContain('<div class="ocr-detected-table-wrap" data-block-id="b2">');
    expect(flowHtml).toContain('<table class="ocr-detected-table">');
    expect(flowHtml).toContain('<th>Col A</th>');
    expect(flowHtml).toContain('<td>Val 1</td>');
  });

  test('fromOcrPayload sets type to table when payload text contains markdown table', () => {
    const payload = {
      text: '| Engine | Accuracy |\n|---|---|\n| Gemma | 98% |',
      page_width: 800,
      page_height: 1000,
    };

    const pageDoc = fromOcrPayload(payload);
    expect(pageDoc.blocks).toHaveLength(1);
    expect(pageDoc.blocks[0].type).toBe('table');
  });

  test('blocksFromFlowHtml extracts table blocks with preserved block IDs', () => {
    const html = '<p data-block-id="b1">Paragraph 1</p><table data-block-id="b2"><tr><th>Header</th></tr><tr><td>Data</td></tr></table>';
    const previousBlocks = [
      { id: 'b1', type: 'paragraph', bbox: [10, 10, 100, 50] },
      { id: 'b2', type: 'table', bbox: [10, 60, 200, 150] },
    ];

    const blocks = blocksFromFlowHtml(html, previousBlocks);
    expect(blocks).toHaveLength(2);
    expect(blocks[0].id).toBe('b1');
    expect(blocks[0].type).toBe('paragraph');
    expect(blocks[1].id).toBe('b2');
    expect(blocks[1].type).toBe('table');
    expect(blocks[1].content).toContain('<table');
  });

  test('createRichEditor mounts TipTap with Markdown extension and parses markdown tables', () => {
    const editor = createRichEditor('rich-editor', {
      content: '| Col 1 | Col 2 |\n|---|---|\n| Val 1 | Val 2 |',
    });

    const html = getEditorContent(editor);
    expect(html).toContain('<table');
    expect(html).toContain('<th');
    expect(html).toContain('Col 1');
    expect(html).toContain('<td');
    expect(html).toContain('Val 1');

    editor.destroy();
  });
});
