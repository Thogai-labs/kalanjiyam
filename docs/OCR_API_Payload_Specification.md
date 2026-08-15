# OCR API Response & Metrics Specification (v2.2)

> **Core Requirement**: When Kalanjiyam sends a single image (e.g., `1.jpg`) via `POST /v1/ocr`, the OCR service **MUST return page transcription data AND metrics metadata together in the same single JSON response**.

## Changes in v2.2

Three changes, all driven by the metadata-extraction pipeline
(`METADATA_API_Payload_Specification.md`), which reads OCR output rather than images and
cites it as evidence:

1. **`page_p05` becomes a required key with an explicitly nullable value.** Send `words[]`
   on text blocks where the engine produces sub-block scores, or send a top-level
   `page_p05`. Engines with **no confidence signal at all** — VLM-based OCR such as
   `chandra`, `glm-ocr` and `dots-ocr`, which expose no token logprobs — must send
   `"page_p05": null` and `"page_confidence": null`.

   The key must be present either way. `null` is an affirmative declaration that the
   engine is confidence-blind; an *absent* key is ambiguous — a bug, an old build, or an
   oversight — and Kalanjiyam cannot tell those apart. Never substitute a placeholder:
   `0.0` would exclude every page from downstream processing and `1.0` would claim
   quality the engine never measured.
2. **`blocks[].id` must be stable** for a given (image, engine, model version). Evidence
   spans in the metadata pipeline are stored against block ids; ids that reshuffle between
   runs silently repoint every citation on that page at the wrong region. If an engine
   cannot guarantee stability, it must declare `"stable_block_ids": false` so Kalanjiyam
   re-verifies evidence after re-OCR.
3. **Non-content blocks must be typed** when emitted at all. Omitting `running-header` and
   `page-number` blocks remains fine; labelling them `paragraph` is not. They repeat on
   every page, so mislabelled they are indistinguishable from body text and get read as
   document titles by downstream extraction.

Everything else is unchanged from v2.1.

---

## 1. Single Request / Response Workflow

```
Kalanjiyam Server                         OCR API Service
       │                                         │
       │ --- POST /v1/ocr (image: 1.jpg) ------> │
       │                                         │ (Processes image)
       │ <--- 200 OK (Single JSON Payload) ----- │
       │      Includes:                          │
       │      - Page Text & Blocks               │
       │      - Engine, Model & Confidence       │
       │      - Processing Latency               │
       ▼                                         │
Kalanjiyam stores text + metrics in DB
```

---

## 2. Required JSON Response Payload (`POST /v1/ocr`)

When processing `1.jpg`, the OCR API must return a single JSON object containing:

```json
{
  "contract_version": "2.2",
  "engine": "surya",
  "model": {
    "name": "surya-rec",
    "version": "0.6.1"
  },
  "page_confidence": 0.942,
  "engine_latency_ms": 342.5,
  "page_width": 1240,
  "page_height": 1754,
  "blocks": [
    {
      "id": "b1",
      "type": "heading",
      "bbox": [120, 40, 980, 88],
      "reading_order": 1,
      "content": "Chapter Title Text",
      "confidence": 0.985
    },
    {
      "id": "b2",
      "type": "paragraph",
      "bbox": [120, 100, 980, 280],
      "reading_order": 2,
      "content": "First line of body text.\nSecond line of body text.",
      "confidence": 0.912,
      "words": [
        {"text": "First", "bbox": [120, 100, 180, 135], "confidence": 0.990},
        {"text": "line", "bbox": [190, 100, 240, 135], "confidence": 0.760}
      ]
    }
  ]
}
```

---

## 3. Per-Page Metrics Collected from Response

From each single page response payload, Kalanjiyam automatically extracts and stores per-page metrics:

| Per-Page Metric | API Response Field | Data Type | Value Range / Format | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`Engine`** | `engine` | String | e.g., `"surya"`, `"google"`, `"tesseract"` | Identifier of the OCR engine used. |
| **`Confidence`** | `page_confidence` | Float | `0.0` to `1.0` (e.g. `0.942` = `94.2%`) | Overall page recognition accuracy score. |
| **`p05`** | `words[].confidence`, or `page_p05` | Float or `null` | `0.0` to `1.0`, or `null` | 5th percentile confidence cutoff (Quality Floor). Gates which pages are trusted for metadata extraction. `null` on confidence-blind engines — see below. |
| **`Blocks`** | `blocks` | Array | Count of items in `blocks` array | Total structural layout blocks detected on page. |
| **`Chars`** | `blocks[].content` | String | Character length sum of block contents | Total extracted character count on page. |
| **`Engine-Latency`** | `engine_latency_ms` | Float | Milliseconds (e.g. `342.5`) | Pure OCR model processing latency for `1.jpg`. |

---

## 4. Full Document & Project Level Aggregated Metrics

As pages complete, Kalanjiyam aggregates per-page metrics into **Document / Project Level Rollups** (`BatchItem` table, Admin UI Summary & CSV Exports):

| Document / Project Metric | DB Field / Aggregation Method | UI Header | Description |
| :--- | :--- | :--- | :--- |
| **`Pages`** | `item.pages` / `count(completed_pages)` | `Pages` | Total count of pages in document/project. |
| **`Avg Conf.`** | `item.avg_confidence` = $\frac{\sum \text{conf}}{\text{pages}}$ | `Avg Conf.` | Mean confidence score across all completed pages (e.g. `94.2%`). |
| **`Min Conf.`** | `item.min_confidence` = $\min(\text{page.confidence})$ | `Min Conf.` | Lowest page confidence score in the document (pinpoints weakest page). |
| **`Pages <0.7`** | `item.low_conf_page_count` = $\text{count}(\text{conf} < 0.7)$ | `Pages <0.7` | Number of pages in document with confidence/quality below 70%. |
| **`Avg Engine Latency`**| `avg_engine_latency_sec` = $\frac{\text{total\_engine\_latency}}{\text{pages}}$ | `Avg Engine Latency` | Average engine processing time per page in seconds (e.g. `0.34s/p`). |
| **`Chars`** | `item.total_chars` = $\sum \text{page.chars}$ | `Chars` | Cumulative total character count extracted across all pages. |

---

## 5. Field Specification Checklist

### Top-Level Required Fields
* `contract_version` (String, Required): Must be `"2.2"`.
* `engine` (String, Required): OCR engine name.
* `model` (Object, Optional): `{"name": "...", "version": "..."}` metadata.
* `page_confidence` (Float **or `null`**, key Required): Overall page accuracy (`0.0` to
  `1.0`), or `null` if the engine produces no confidence signal.
* `page_p05` (Float **or `null`**, key Required): 5th-percentile word confidence. May be
  omitted *as a value* — i.e. sent as `null` — only when the engine has no sub-block
  scores **and** no page-level score. If `words[]` is present, Kalanjiyam computes `p05`
  itself and this field is ignored. The key itself is always required: see
  "Confidence-blind engines" below.
* `engine_latency_ms` (Float, Required): OCR processing time in milliseconds.
* `page_width`, `page_height` (Integers, Required): Image pixel dimensions.
* `stable_block_ids` (Boolean, Optional, default `true`): Whether `blocks[].id` is
  reproducible for this image on this engine and model version. Declare `false` if not.
* `blocks` (Array, Required): List of layout block objects.

### Block Fields (`blocks[]`)
* `id` (String, Required): Unique block ID. **Must be stable** across runs for the same
  (image, engine, model version) unless `stable_block_ids` is `false`. Downstream
  descriptive metadata cites these ids to link a fact back to a region of the page image.
* `type` (String, Required): `paragraph`, `heading`, `subheading`, `table`, `figure`, `caption`, `footnote`, `running-header`, `page-number`, `column-header`, `equation`.
  **Non-content blocks must carry their true type**: a running header, folio number or
  footnote must never be emitted as `paragraph`.
* `bbox` (Array[4], Required): Bounding box `[x1, y1, x2, y2]`.
* `reading_order` (Integer, Required): 1-based reading sequence order.
* `content` (String, Required): Plain text (or `<table>` HTML snippet for table blocks).
* `confidence` (Float, Required): Block accuracy score (`0.0` to `1.0`).
* `words` (Array, Required on text blocks **where the engine produces sub-block scores**):
  Word spans `[{"text": "...", "bbox": [...], "confidence": 0.95}]` used to compute
  **`p05`**. `figure` blocks need not include them. An engine with no sub-block scores
  omits `words` and sends `page_p05: null`.

---

## Confidence-blind engines

Three of the engines in service (`chandra`, `glm-ocr`, `dots-ocr`) are VLM-based and
expose no per-token or per-word probability. They are not "usually missing" confidence —
they are structurally incapable of producing it, and no amount of post-processing on the
service side can invent it honestly.

Such an engine sends:

```json
{
  "contract_version": "2.2",
  "engine": "dots-ocr",
  "page_confidence": null,
  "page_p05": null,
  "blocks": [
    {"id": "b1", "type": "paragraph", "bbox": [120, 100, 980, 280],
     "reading_order": 1, "content": "...", "confidence": null}
  ]
}
```

**What this costs, stated plainly.** Kalanjiyam's OCR-quality gate — which decides whether
a page's text is trustworthy enough to extract descriptive metadata from — cannot run on
these engines. That is a real loss, and the correct response is to record it rather than
paper over it:

* `null` propagates as `null`. Page-confidence rollups average over pages that *have* a
  score and report `pages_without_confidence` alongside, so an average is never computed
  from a mix of measured and assumed values.
* The description's `DESCRIPTION` tag (ISAD(G) Area 7, "description control and
  confidence") records that OCR confidence was unavailable for the engine used. Stating
  where the evidence is weak is exactly what that element exists for.
* Evidence verification is unaffected. It checks extracted quotes against the OCR text
  itself, not against confidence scores, so it works identically on every engine — and on
  confidence-blind engines it becomes the *only* quality signal available.

Deriving confidence from vLLM token logprobs would restore the gate for these engines, but
that is engine-level work well beyond this contract and is explicitly **not** required by
v2.2.
