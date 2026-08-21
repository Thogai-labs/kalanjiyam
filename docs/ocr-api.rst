OCR Service API (v2.2)
======================

Kalanjiyam delegates OCR to external microservices. The editor expects **v2.2** responses
with structured blocks, quality confidence metrics, processing latency, and optional word-level confidence spans.

Base URLs & Fallback
--------------------

Primary and fallback microservice endpoints are configured in Kalanjiyam (via ``.env``):

- ``OCR_SERVICE_URL`` — Primary OCR service endpoint (e.g. ``http://<primary-ocr-host>:8000``).
- ``OCR_SERVICE_URL_2`` — Secondary fallback endpoint (e.g. ``http://<secondary-ocr-host>:8887``). Used automatically if the primary endpoint is unreachable or returns a 5xx error.

Endpoints & Formats
-------------------

``GET /v1/engines``
  Returns ``{"status": "ok", "engines": ["google", "tesseract", "surya", "deepseek", "glm-ocr", "dots-ocr"]}``.

``POST /v1/ocr``
  Multipart form fields:
  - ``image`` (file, required): Image binary stream (`image/jpeg` or `image/png`).
  - ``engine`` (string, required): Accepts both canonical unmasked names (e.g. ``surya``, ``deepseek``, ``glm_ocr``) and numeric masked keys (e.g. ``"1"``, ``"3"``, ``"5"``). Kalanjiyam normalizes the value and sends the unmasked service identifier to the API.
  - ``language`` (string, required): BCP-47 / ISO language code (e.g. ``sa``, ``ta``, ``hi``, ``eng``).

  Headers: ``X-API-Key`` when ``OCR_SERVICE_API_KEY`` (or ``OCR_SERVICE_API_KEY_2``) is set.

Response (v2.2)
---------------

+--------------------+------------------+--------------------------------------------------------------+
| Field              | Type             | Description                                                  |
+====================+==================+==============================================================+
| ``contract_version``| string          | Must be ``"2.2"`` (or legacy ``"2.1"`` / ``"2.0"``)          |
+--------------------+------------------+--------------------------------------------------------------+
| ``engine``         | string           | Engine identifier (e.g. ``"surya"``, ``"google"``)           |
+--------------------+------------------+--------------------------------------------------------------+
| ``model``          | object           | Model provenance metadata (e.g. ``{"name": "...", "version": "..."}``) |
+--------------------+------------------+--------------------------------------------------------------+
| ``page_confidence``| float or null    | Aggregate page recognition accuracy in ``[0.0, 1.0]``        |
+--------------------+------------------+--------------------------------------------------------------+
| ``page_p05``       | float or null    | 5th-percentile confidence floor; ``null`` on VLM engines     |
+--------------------+------------------+--------------------------------------------------------------+
| ``engine_latency_ms``| float          | OCR engine microservice processing time in milliseconds       |
+--------------------+------------------+--------------------------------------------------------------+
| ``page_width``     | integer          | Source image width in pixels                                 |
+--------------------+------------------+--------------------------------------------------------------+
| ``page_height``    | integer          | Source image height in pixels                                |
+--------------------+------------------+--------------------------------------------------------------+
| ``stable_block_ids``| boolean         | Optional (default true). Stable IDs across re-OCR runs       |
+--------------------+------------------+--------------------------------------------------------------+
| ``blocks``         | array            | Structured layout blocks (see below)                         |
+--------------------+------------------+--------------------------------------------------------------+

Block object
~~~~~~~~~~~~

.. code-block:: json

   {
     "id": "b1",
     "type": "paragraph",
     "bbox": [80, 120, 520, 200],
     "content": "Sanskrit text string here.",
     "confidence": 0.92,
     "reading_order": 1,
     "words": [
       {"text": "Sanskrit", "bbox": [80, 120, 150, 150], "confidence": 0.98}
     ]
   }

Block types: ``paragraph``, ``heading``, ``subheading``, ``verse``, ``table``, ``figure``, ``caption``, ``footnote``, ``running-header``, ``page-number``, ``column-header``, ``equation``.

Pipelines
---------

+----------------+----------------------------------------------------------+
| ``vlm``        | Nanonets, Chandra, DeepSeek, Qwen3 — layout from model   |
+----------------+----------------------------------------------------------+
| ``hybrid``     | Google, Tesseract, Surya — LlamaParse layout + OCR merge |
+----------------+----------------------------------------------------------+
| ``standard``   | Heuristic clustering from word boxes only                |
+----------------+----------------------------------------------------------+

OCR service environment (not Kalanjiyam)
----------------------------------------

- ``LLAMA_CLOUD_API_KEY`` — LlamaCloud / LlamaParse for hybrid pipeline
- Optional ``LLAMAPARSE_*`` settings per LlamaCloud docs

If v2 fields are absent, Kalanjiyam builds blocks from ``text`` and ``bounding_boxes``.

Adding a new engine to Kalanjiyam
----------------------------------

When the OCR service adds an engine, it appears in ``GET /v1/engines``, but it
will **not** show up in Kalanjiyam's OCR dropdown automatically. Kalanjiyam
keeps a deliberate allowlist so that a half-tested engine on the service side
doesn't suddenly appear for users. To register a new engine:

1. ``kalanjiyam/utils/ocr_types.py``

   - Add the engine's internal id (underscored) to ``SUPPORTED_ENGINES``.
   - Add an unused numeric key for it in ``ENGINE_MAP`` (e.g. ``"11":
     "tesseract_manuscript"``) — ``REVERSE_ENGINE_MAP`` is derived from this.
   - Add a display label to ``ENGINE_LABELS``.
   - If the service's hyphenated id doesn't convert cleanly to the internal
     id via ``replace("-", "_")`` / ``replace("_", "-")``, add an entry to
     ``SERVICE_ENGINE_ALIASES``.
   - If the engine returns HTML or Markdown instead of plain text, add it to
     ``HTML_ENGINES`` or ``MARKDOWN_ENGINES``.

2. ``kalanjiyam/static/js/proofer.js``

   - Add the same numeric key to the ``ocrEngines`` config object (engine
     name, supported languages, bilingual support).
   - Add the same numeric key → internal id mapping to ``decodeEngine``.

3. ``kalanjiyam/templates/proofing/pages/editor-components.html`` (optional)

   - Add a short description to the OCR Engine info panel.

``build_engine_choices()`` then filters the live ``/v1/engines`` ping through
``SUPPORTED_ENGINES``, so the new engine appears in the dropdown — labeled for
super admins, generically ("OCR N") for everyone else — as soon as the OCR
service reports it as ready.
