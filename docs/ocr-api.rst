OCR Service API (v2)
====================

Kalanjiyam delegates OCR to an external service. The editor expects **v2** responses
with structured blocks for layout-faithful editing.

Base URL
--------

Configured in Kalanjiyam via ``OCR_SERVICE_URL`` (see ``.env.example``).

Endpoints
---------

``GET /v1/engines``
  Returns ``{"engines": ["google", "tesseract", ...]}``.

``POST /v1/ocr``
  Multipart form: ``image`` (file), ``engine`` (string), ``language`` (string).

  Headers: ``X-API-Key`` when ``OCR_SERVICE_API_KEY`` is set.

Response (v2)
-------------

Legacy fields remain for older clients:

+------------------+----------+------------------------------------------+
| Field            | Type     | Description                              |
+==================+==========+==========================================+
| ``text``         | string   | Plain-text fallback                      |
+------------------+----------+------------------------------------------+
| ``bounding_boxes`` | string | TSV lines ``x1 y1 x2 y2 text`` or Surya JSON |
+------------------+----------+------------------------------------------+

New fields:

+------------------+----------+------------------------------------------+
| Field            | Type     | Description                              |
+==================+==========+==========================================+
| ``layout_html``  | string   | Optional HTML layout from VLM engines    |
+------------------+----------+------------------------------------------+
| ``content_format`` | string | ``plain``, ``html``, or ``blocks``       |
+------------------+----------+------------------------------------------+
| ``page_width``   | integer  | Image width in pixels                    |
+------------------+----------+------------------------------------------+
| ``page_height``  | integer  | Image height in pixels                   |
+------------------+----------+------------------------------------------+
| ``pipeline``     | string   | ``vlm``, ``hybrid``, or ``standard``     |
+------------------+----------+------------------------------------------+
| ``blocks``       | array    | Structured blocks (see below)            |
+------------------+----------+------------------------------------------+

Block object
~~~~~~~~~~~~

.. code-block:: json

   {
     "id": "b1",
     "type": "paragraph",
     "bbox": [80, 120, 520, 200],
     "content": "…",
     "reading_order": 1,
     "children": []
   }

Block types: ``paragraph``, ``heading``, ``verse``, ``table``, ``figure``, ``list_item``.

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
