OCR Service Response Contract
==============================

This document is the authoritative contract between the Kalanjiyam editing
frontend and the OCR service.  Hand this to the OCR agent / service team.

----

Drop-in instruction for the OCR agent
--------------------------------------

When processing a document page, return a JSON object with the following
structure.  Do not return plain text or HTML as the top-level response — always
return JSON with ``Content-Type: application/json``.

.. code-block:: json

    {
      "source_type": "scan",
      "page_width": 1240,
      "page_height": 1754,
      "blocks": [
        {
          "id": "b1a2c3d4",
          "type": "heading",
          "bbox": [120, 40, 980, 88],
          "reading_order": 1,
          "content": "Chapter title text here",
          "confidence": 0.96,
          "language": "sa"
        },
        {
          "id": "b2e3f4a5",
          "type": "paragraph",
          "bbox": [120, 100, 980, 280],
          "reading_order": 2,
          "content": "Body paragraph text here, potentially multi-line.\nSecond line continues here.",
          "confidence": 0.88,
          "language": "sa"
        },
        {
          "id": "b3g4h5i6",
          "type": "table",
          "bbox": [120, 300, 980, 620],
          "reading_order": 3,
          "content": "<table><tr><th>Col A</th><th>Col B</th></tr><tr><td>value</td><td>value</td></tr></table>",
          "confidence": 0.79,
          "language": "sa"
        },
        {
          "id": "b4j5k6l7",
          "type": "figure",
          "bbox": [200, 640, 800, 900],
          "reading_order": 4,
          "content": "",
          "confidence": 1.0,
          "language": null
        }
      ]
    }

----

Field-by-field rules
---------------------

``source_type`` (string, top-level)
  ``"scan"`` for photographed or scanned documents.
  ``"pdf"`` for born-digital PDFs where the text layer was extracted.
  ``"digital"`` for HTML or plain-text sources.
  The frontend uses this to decide the default editing mode.

``page_width``, ``page_height`` (integer, top-level)
  Pixel dimensions of the source image or rendered PDF page.
  Required for the spatial replica view to scale block positions correctly.

``id`` (string, per block)
  A short stable identifier for the block — 8 hex characters is fine.
  Must be unique within the page.  The frontend uses this to track which
  blocks a human has manually edited, so they survive a re-OCR run.

``type`` (string, per block)
  One of the following values exactly::

      paragraph        regular body text
      heading          chapter or section title (maps to <h2>)
      subheading       sub-section title (maps to <h3>)
      table            tabular data
      figure           image, diagram, or illustration
      caption          text label beneath a figure or table
      footnote         footnote at the bottom of a page
      running-header   repeated header at the top of every page
      page-number      page number
      column-header    header row labelling columns in a multi-column layout
      equation         mathematical expression

  The frontend silently skips ``running-header``, ``page-number``, and
  ``figure`` in flow mode.  Unknown types are treated as ``paragraph``.

``bbox`` (array of 4 numbers, per block)
  ``[x1, y1, x2, y2]`` in pixels from the top-left corner of the page image.
  For PDF sources use points; set ``source_type: "pdf"`` so the frontend
  knows the coordinate system.
  If a block has no spatial position (e.g. inferred from structure), use
  ``[0, 0, 0, 0]`` — the block will render as a stacked element in replica
  view rather than a positioned one.

``reading_order`` (integer, per block)
  1-based index of reading order across the whole page.
  For multi-column layouts, interleave correctly: finish the left column
  entirely before starting the right column, unless the actual reading order
  is different.
  The frontend sorts all blocks by this value before rendering in flow mode.

``content`` (string, per block)
  **Plain text for all non-table types.**
  Do not emit ``<p>``, ``<strong>``, ``<em>``, or any other HTML tags for
  paragraph, heading, footnote, caption, or equation blocks.
  Newlines within a block are fine (``\n``) and will be preserved.

  **For ``table`` type only:** emit a complete ``<table>`` HTML string.
  Include ``<th>`` for header cells.  Preserve ``colspan`` and ``rowspan``
  for merged cells.  Do not wrap in ``<div>`` — the frontend handles that.
  Example::

      <table>
        <tr><th>Term</th><th>Meaning</th></tr>
        <tr><td>धर्म</td><td>Righteousness</td></tr>
      </table>

  For ``figure`` type, ``content`` should be empty or contain a detected
  caption string if one is attached to the figure.

``confidence`` (float 0.0–1.0, per block)
  Recognition confidence for the whole block.
  ``1.0`` = certain (e.g. a structural element you detected, not recognised).
  ``null`` is acceptable if the engine does not produce confidence scores.

  The frontend uses this for provenance colouring::

      ≥ 0.75   no highlight (clean)
      0.50–0.74   amber border (review recommended)
      < 0.50   red border (likely OCR error)

``language`` (BCP-47 string, per block, nullable)
  Detected language of the block.  Examples: ``"sa"`` (Sanskrit),
  ``"ta"`` (Tamil), ``"en"`` (English), ``"hi"`` (Hindi).
  ``null`` is acceptable for figures or blocks with no text.
  This drives the ``lang`` attribute on block elements, which controls
  font selection and spell-checking in the browser.

----

What NOT to include
-------------------

- Do not emit word-level or character-level bounding boxes in the blocks
  array.  If you need to include them for other consumers, use a separate
  ``word_boxes`` field at the top level — the frontend ignores it.
- Do not apply bold, italic, underline, or any rich-text markup inside
  ``content`` for non-table blocks.  OCR engines are unreliable about
  formatting and it will show as literal HTML tags in the editor.
- Do not include ``running-header`` or ``page-number`` blocks if the
  content adds no value for the transcription (e.g. repeated "Page 12").
