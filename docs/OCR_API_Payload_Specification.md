# OCR API Response & Metrics Specification (v2.1)

> **Core Requirement**: When Kalanjiyam sends a single image (e.g., `1.jpg`) via `POST /v1/ocr`, the OCR service **MUST return page transcription data AND metrics metadata together in the same single JSON response**.

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
  "contract_version": "2.1",
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
| **`p05`** | `words[].confidence` | Float | `0.0` to `1.0` | 5th percentile confidence cutoff (Quality Floor). |
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
* `contract_version` (String, Required): Must be `"2.1"`.
* `engine` (String, Required): OCR engine name.
* `model` (Object, Optional): `{"name": "...", "version": "..."}` metadata.
* `page_confidence` (Float, Required): Overall page accuracy (`0.0` to `1.0`).
* `engine_latency_ms` (Float, Required): OCR processing time in milliseconds.
* `page_width`, `page_height` (Integers, Required): Image pixel dimensions.
* `blocks` (Array, Required): List of layout block objects.

### Block Fields (`blocks[]`)
* `id` (String, Required): Unique block ID.
* `type` (String, Required): `paragraph`, `heading`, `subheading`, `table`, `figure`, `caption`, `footnote`, `running-header`, `page-number`, `column-header`, `equation`.
* `bbox` (Array[4], Required): Bounding box `[x1, y1, x2, y2]`.
* `reading_order` (Integer, Required): 1-based reading sequence order.
* `content` (String, Required): Plain text (or `<table>` HTML snippet for table blocks).
* `confidence` (Float, Required): Block accuracy score (`0.0` to `1.0`).
* `words` (Array, Optional): Word spans `[{"text": "...", "bbox": [...], "confidence": 0.95}]` used to compute **`p05`**.
