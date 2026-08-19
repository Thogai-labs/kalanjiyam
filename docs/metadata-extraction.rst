Archival Metadata Extraction
=============================

Kalanjiyam includes an automated archival description extraction pipeline that reads every page of a digitized text or manuscript in token-budgeted windows, delegates extraction to the ``POST /v1/metadata`` microservice, verifies citation evidence, and reduces window outputs into unified archival metadata records (ISAD(G), ISAAR(CPF), and RiC-CM standards).

Overview
--------

Unlike simple sampling pipelines that read only front matter, archival description requires high recall across the entire document (e.g. identifying names, dates, places, and relationships mentioned on any folio). 

.. code-block:: text

   Kalanjiyam Server                                  Metadata Microservice
          │                                                    │
          │ ─── POST /v1/metadata (Window 3 of 24) ──────────> │ (Server-side schema-guided
          │     Payload: Typed blocks, tag list,               │  decoding & prompt execution)
          │              taxonomy version                      │
          │ <─── 200 OK (Single JSON Payload) ──────────────── │
          │      Includes: Fields, per-value evidence citations,│
          │      token usage, engine latency                   │
          ▼                                                    │
   Kalanjiyam verifies quotes, stores window metrics,
   and reduces all windows into one description per project.

Key Architecture Principles
~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Stateless per Window**: The microservice is stateless per window. Kalanjiyam divides the document into budgeted windows, dispatches requests, and handles the map-reduce aggregation.
2. **Server-Side Prompt & Schema Enforcement**: No natural language prompt is transmitted across HTTP. The backend microservice manages the model instruction and schema-constrained decoding (e.g. Gemma 3 27B) guided by ``taxonomy_version`` and ``tags``.
3. **Mandatory Evidence & Provenance**: Every extracted fact declares its origin (``record``, ``derived``, ``enrichment``, or ``curated``). Facts stated in the record must carry verbatim quotes matching source OCR blocks.
4. **Resilience to Quality-Blind OCR**: VLM-based OCR engines (e.g., Chandra, GLM-OCR, Dots-OCR) produce no character-level confidence scores. Kalanjiyam preserves ``null`` confidence values end-to-end and uses quote verification as the primary quality gate.

How Pages are Divided into Windows
----------------------------------

The windowing algorithm in ``kalanjiyam.utils.project_metadata`` divides documents into windows using token budgets and script awareness:

1. Page Selection & Script Profiling
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* For every page, ``resolve_extraction_tracks()`` selects the highest quality revision (Reviewed > Proofread P2 > Proofread P1 > OCR). Pages with fewer than 50 characters (blank pages) are skipped.
* The dominant script of the text is profiled to estimate token density:
  * **Latin script (``Latn``)**: ~3.0 characters per token.
  * **Indic / Non-Latin scripts (``_default``)**: ~1.2 characters per token (pessimistic estimate accounting for conjuncts and matras).
  * A safety multiplier of ``TOKEN_SAFETY_FACTOR = 0.85`` is applied.

2. Token & Character Budgeting
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each window targets a budget of **20,000 tokens** (``WINDOW_TOKEN_BUDGET = 20_000``). On a 32,768-token context window, this reserves ~3,000 tokens for system taxonomy instructions and ~4,500 tokens for generation output.

The character budget is calculated as:

.. math::

   \text{budget\_chars} = \lfloor \text{WINDOW\_TOKEN\_BUDGET} \times (\text{chars\_per\_token} \times 0.85) \rfloor

3. Window Planning (``plan_windows``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Windows are planned from ``TrackRow.char_len`` without loading entire page contents into memory:

* **Whole Pages Preserved**: Pages are never split mid-page across windows. If a single page exceeds the budget, it is assigned its own window.
* **1-Page Overlap (``WINDOW_OVERLAP_PAGES = 1``)**: The last page of each window is carried forward to the beginning of the next window. This guarantees that multi-page sentences, signatures, and dates straddling page breaks are captured in context.

.. code-block:: text

   Window 1: [ Page 1 ] [ Page 2 ] [ Page 3 ]
                                     └── Overlap
   Window 2:                       [ Page 3 ] [ Page 4 ] [ Page 5 ]
                                                           └── Overlap
   Window 3:                                             [ Page 5 ] [ Page 6 ] ...

4. Streamed Loading & Content Hashing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* Pages are streamed in database batches of 250 (``_STREAM_BATCH = 250``) to keep worker RAM usage constant (under 50 MB).
* Text is structured into ordered, typed OCR blocks (``heading``, ``paragraph``, etc.) with unique block IDs.
* A SHA-256 hash (``window_hash``) is computed over block text and slugs. If a project is re-extracted after a minor edit, unchanged windows are reused from cache (``STATUS_SKIPPED``), saving GPU compute.

Backend Communication (``POST /v1/metadata``)
---------------------------------------------

Kalanjiyam dispatches window extraction requests to the metadata service with automatic fallback between primary (``OCR_SERVICE_URL``) and secondary (``OCR_SERVICE_URL_2``) endpoints.

HTTP Request Specification
~~~~~~~~~~~~~~~~~~~~~~~~~~

* **URL**: ``POST {OCR_SERVICE_URL}/v1/metadata``
* **Headers**: ``Content-Type: application/json``, ``X-API-Key: <OCR_SERVICE_API_KEY>``
* **Timeout**: 300 seconds default

Request Payload Example:

.. code-block:: json

   {
     "contract_version": "1.0",
     "unit_id": "kalanjiyam:project/kalat-1932-17",
     "window": {
       "index": 3,
       "total": 24,
       "page_slugs": ["61", "62", "63"]
     },
     "taxonomy_version": "client-2026-08",
     "tags": [
       "TITLE",
       "DATE",
       "CREATOR",
       "SCOPE CONTENT",
       "PERSON NAME",
       "PLACE",
       "ORGANISATION",
       "SUBJECT"
     ],
     "language_hint": ["fa", "ur", "en"],
     "pages": [
       {
         "page_slug": "61",
         "ocr_confidence": 0.94,
         "blocks": [
           {
             "id": "b1",
             "type": "heading",
             "reading_order": 1,
             "text": "Grant of an honorary commission"
           },
           {
             "id": "b2",
             "type": "paragraph",
             "reading_order": 2,
             "text": "First line of body text..."
           }
         ]
       }
     ]
   }

Field Reference
~~~~~~~~~~~~~~~

+----------------------+---------------+-------------------------------------------------------------+
| Field                | Type          | Description                                                 |
+======================+===============+=============================================================+
| ``contract_version`` | string        | API specification version (``"1.0"``).                      |
+----------------------+---------------+-------------------------------------------------------------+
| ``unit_id``          | string        | Document/project identifier (e.g. ``kalanjiyam:project/x``).|
+----------------------+---------------+-------------------------------------------------------------+
| ``window``           | object        | Contains ``index``, ``total``, and ``page_slugs`` list.     |
+----------------------+---------------+-------------------------------------------------------------+
| ``taxonomy_version`` | string        | Schema release identifier (e.g. ``"client-2026-08"``).      |
+----------------------+---------------+-------------------------------------------------------------+
| ``tags``             | array[string] | Authoritative whitelist of requested tags. Write-locked tags|
|                      |               | are automatically filtered out.                             |
+----------------------+---------------+-------------------------------------------------------------+
| ``language_hint``    | array[string] | Known language codes for the document (e.g. ``["en", "ta"]``)|
+----------------------+---------------+-------------------------------------------------------------+
| ``pages``            | array[object] | Ordered pages in the window, each containing ``page_slug``, |
|                      |               | nullable ``ocr_confidence``, and list of typed ``blocks``.   |
+----------------------+---------------+-------------------------------------------------------------+

Prompt Architecture & Schema Handling
-------------------------------------

* **No Natural Language Prompt in the Wire Payload**: The client sends only structured text blocks and taxonomy tags. The system prompt, extraction instructions, few-shot examples, and grammar constraints live on the microservice.
* **Write-Locked Tags Excluded**: Custodial history and access restriction tags are write-locked (human archivist curated). They are excluded from ``tags`` before sending and stripped if returned by the model.
* **Internal Reference Prompt**: The function ``build_prompt()`` in ``kalanjiyam.utils.archival_taxonomy`` defines the canonical prompt instruction for local evaluation, test suites, and offline auditing.

Evidence Verification and Reduction
-----------------------------------

Once a window response is received:

1. **Quote Verification**: Every ``record`` field is checked verbatim against the original block text sent in the request. If the quote is missing or hallucinated, its confidence score is set to ``0.0``.
2. **Coordinate Linking**: The ``block_id`` links verified quotes directly to spatial bounding boxes in ``Revision.document``, enabling users to click a fact in the catalog and highlight its exact bounding box on the page image.
3. **Map-Reduce (``reduce_windows``)**: Once all windows complete, window fields are merged into a canonical project description. Single-value fields choose the highest confidence verified entry, and entity lists (persons, places, subjects) are deduplicated and merged.
4. **Bibliographic Write-Down**: Search-facing columns in ``Project`` (title, author, publication year, etc.) are seeded from the verified archival fields without overwriting user-curated data.