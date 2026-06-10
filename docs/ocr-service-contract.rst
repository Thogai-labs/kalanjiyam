OCR Service Response Contract (v2)
===================================

This document is the authoritative, **engine-agnostic** contract between the
Kalanjiyam editing frontend and any OCR engine or model served through the OCR
service.  Hand this to the OCR agent / service / model team.  Any engine that
emits this shape — classical OCR, layout models, or VLM-based OCR — plugs into
the editor with no frontend changes.

Changes from v1:

- ``contract_version``, ``engine``, and ``model`` fields for provenance.
- Block-level ``confidence`` is now **required whenever the engine produces
  any internal score** (v1 treated it as fully optional).
- Optional **word-level granularity** via ``words`` inside each block
  (v1 forbade word-level data; that restriction is lifted).
- Explicit ``coordinate_space`` declaration instead of frontend heuristics.

----

Drop-in instruction for the OCR agent
--------------------------------------

When processing a document page, return a JSON object with the following
structure.  Do not return plain text or HTML as the top-level response — always
return JSON with ``Content-Type: application/json``.

.. code-block:: json

    {
      "contract_version": "2.0",
      "engine": "surya",
      "model": {"name": "surya-rec", "version": "0.6.1"},
      "source_type": "scan",
      "coordinate_space": "pixel",
      "page_width": 1240,
      "page_height": 1754,
      "page_confidence": 0.91,
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
          "content": "Body paragraph text here.\nSecond line continues here.",
          "confidence": 0.62,
          "language": "sa",
          "words": [
            {"text": "Body", "bbox": [120, 100, 190, 130], "confidence": 0.98},
            {"text": "paragraph", "bbox": [200, 100, 350, 130], "confidence": 0.41}
          ]
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

Top-level fields
----------------

``contract_version`` (string, required)
  ``"2.0"``.  Lets the frontend apply legacy fallbacks for older engines.
  Responses without this field are treated as v1 (still accepted).

``engine`` (string, required)
  Stable engine identifier, lowercase: ``"google"``, ``"tesseract"``,
  ``"surya"``, ``"nanonets"``, ``"deepseek"``, ``"chandra"``, ``"qwen3"``,
  ``"paddle_table"``, or a new identifier for a new engine.
  Stored on every block as provenance, shown to reviewers, and kept across
  revisions — so a block can always answer "which model produced this text?".

``model`` (object, optional but strongly recommended)
  ``{"name": "<model name>", "version": "<model/checkpoint version>"}``.
  Distinguishes outputs when the same engine upgrades its underlying model.
  Include whatever versioning you have — a checkpoint tag, an API model id,
  or a release date string.

``source_type`` (string, required)
  ``"scan"`` for photographed or scanned documents,
  ``"pdf"`` for born-digital PDFs where the text layer was extracted,
  ``"digital"`` for HTML or plain-text sources.

``coordinate_space`` (string, optional, default ``"pixel"``)
  ``"pixel"``: all bboxes are pixels in the ``page_width × page_height`` grid.
  ``"normalized"``: all bboxes are floats in ``[0, 1]`` relative to page size.
  Declare this explicitly; do not rely on the frontend guessing.

``page_width``, ``page_height`` (integer, required)
  Pixel dimensions of the source image or rendered PDF page.
  Required for the spatial replica view to scale block positions correctly.

``page_confidence`` (float 0.0–1.0, optional)
  Aggregate recognition confidence for the page (e.g. content-length-weighted
  mean of block confidences).  Used for page triage in project views.

Per-block fields
----------------

``id`` (string, required)
  A short stable identifier, unique within the page — 8 hex characters is
  fine.  The frontend uses this to track which blocks a human has manually
  edited, so edits survive a re-OCR run.

``type`` (string, required)
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

``bbox`` (array of 4 numbers, required)
  ``[x1, y1, x2, y2]`` from the top-left corner of the page, in the declared
  ``coordinate_space``.  If a block has no spatial position (e.g. inferred
  from structure), use ``[0, 0, 0, 0]`` — the block renders as a stacked
  element in replica view rather than a positioned one.

``reading_order`` (integer, required)
  1-based index of reading order across the whole page.
  For multi-column layouts, finish the left column entirely before starting
  the right column, unless the actual reading order differs.

``content`` (string, required)
  **Plain text for all non-table types.**  Do not emit ``<p>``,
  ``<strong>``, ``<em>``, or any other HTML tags for paragraph, heading,
  footnote, caption, or equation blocks.  Newlines within a block (``\n``)
  are preserved.

  **For ``table`` type only:** emit a complete ``<table>`` HTML string with
  ``<th>`` for header cells; preserve ``colspan``/``rowspan``.  Do not wrap
  in ``<div>``.

  For ``figure`` type, ``content`` should be empty or contain a detected
  caption string.

``confidence`` (float 0.0–1.0; required if the engine scores at all)
  Recognition confidence for the whole block, mapped onto a common scale:

  - **If your engine produces any internal score (softmax, log-prob, API
    confidence), you must map it to [0, 1] and emit it.**  Omitting
    confidence when a score exists is a contract violation — the editor's
    review workflow depends on it.
  - ``null`` only when the engine genuinely has no confidence measure
    (e.g. some generative VLMs).  The editor renders such blocks neutrally.
  - ``1.0`` = certain (e.g. a structural element you detected, not
    recognised).
  - Calibration target — proofreaders see::

        ≥ 0.75      no highlight (clean)
        0.50–0.74   amber (review recommended)
        < 0.50      red (likely OCR error)

    Calibrate your mapping so these bands are *meaningful*: text that is
    wrong roughly half the time should score near 0.5, not 0.9.  A simple
    min/mean of word scores is acceptable; document your mapping in the
    engine's README.

``language`` (BCP-47 string, nullable)
  Detected language of the block: ``"sa"``, ``"ta"``, ``"en"``, ``"hi"``, …
  ``null`` is acceptable for figures or blocks with no text.  Drives the
  ``lang`` attribute on block elements (font selection and spell-checking).

``words`` (array, optional — word/line-level granularity)
  Fine-grained recognition spans inside the block, in reading order.  Emit
  this **if your engine produces word- or line-level results** — the editor
  uses it for in-block confidence highlighting ("show me the exact words the
  model was unsure about").  Each item:

  .. code-block:: json

      {"text": "paragraph", "bbox": [200, 100, 350, 130], "confidence": 0.41}

  - ``text`` (string, required): the span exactly as it appears in
    ``content`` (concatenating ``words[].text`` with whitespace should
    reconstruct the block content, modulo line breaks).
  - ``confidence`` (float 0.0–1.0, required): same scale and calibration as
    block confidence.  This is the point of the field — do not emit words
    without scores.
  - ``bbox`` (array of 4 numbers, optional but recommended): same
    ``coordinate_space`` as block bboxes.

  Line-level engines may emit one item per line instead of per word — same
  shape.  If you emit ``words``, block ``confidence`` should be an aggregate
  of them.  Blocks may freely mix: some with ``words``, some without.

----

What NOT to include
-------------------

- Do not apply bold, italic, underline, or any rich-text markup inside
  ``content`` for non-table blocks.  OCR engines are unreliable about
  formatting and it shows as literal HTML tags in the editor.
- Do not include ``running-header`` or ``page-number`` blocks if the content
  adds no value for the transcription (e.g. repeated "Page 12").
- Do not emit ``words`` without per-word ``confidence`` — geometry alone is
  not useful to the editor and bloats the payload.
- Do not invent confidence values.  ``null`` is always better than a
  hard-coded ``0.9``.

----

How the platform consumes this (informative)
--------------------------------------------

On ingestion the web app stamps every block with provenance::

    "source": {"engine": "surya", "model": "surya-rec/0.6.1", "ocr_at": "2026-06-10T09:14:03Z"}

and tracks ``manually_edited`` per block as humans correct text.  These
fields are persisted across revisions and rendered in the editor (confidence
colouring, provenance badges, review queues).  Engines never need to send
``source`` or ``manually_edited`` — they are platform-owned.
