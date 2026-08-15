# Metadata Extraction API Response & Metrics Specification (v1.0)

> **Core Requirement**: When Kalanjiyam sends a window of pages via `POST /v1/metadata`, the extraction service **MUST return the extracted description fields AND the metrics for that window together in the same single JSON response**.

---

## 1. Single Request / Response Workflow

The service is **stateless per window**. Kalanjiyam splits a document into token-budgeted
windows, calls the service once per window, and performs the reduce itself. The service is
never asked to hold a whole document, to segment one, or to aggregate across windows.

```
Kalanjiyam Server                        Metadata API Service
       │                                         │
       │ --- POST /v1/metadata (window 3/24) --> │
       │     Payload: pages as typed blocks,     │ (Extracts description fields)
       │              tag list, taxonomy version │
       │ <--- 200 OK (Single JSON Payload) ----- │
       │      Includes:                          │
       │      - Fields + per-value evidence      │
       │      - Engine, Model & Confidence       │
       │      - Token usage & Latency            │
       ▼                                         │
Kalanjiyam verifies evidence, stores window metrics,
reduces all windows into one description per document
```

This mirrors the OCR contract exactly:

```
per page   → OCR metrics        → rolled up to document   (v2.1)
per window → metadata metrics   → rolled up to document   (this spec)
```

---

## 2. Required JSON Payloads (`POST /v1/metadata`)

### A. HTTP Request

```json
{
  "contract_version": "1.0",
  "unit_id": "kalanjiyam:project/kalat-1932-17",
  "window": {"index": 3, "total": 24, "page_slugs": ["61", "62", "63", "64", "65"]},
  "taxonomy_version": "client-2026-08",
  "tags": ["TITLE", "DATE", "CREATOR", "SCOPE CONTENT", "PERSON NAME", "PLACE"],
  "language_hint": ["fa", "ur", "en"],
  "pages": [
    {
      "page_slug": "61",
      "ocr_confidence": 0.94,
      "blocks": [
        {"id": "b1", "type": "heading",   "reading_order": 1, "text": "Grant of an honorary commission"},
        {"id": "b2", "type": "paragraph", "reading_order": 2, "text": "First line of body text..."}
      ]
    }
  ]
}
```

**Page text arrives as typed blocks, not as a flat string.** The `id` on each block is the
anchor the service must cite in its evidence spans; a flattened string makes citation
impossible. The `type` values are those of the OCR contract, so `running-header` and
`page-number` blocks can be recognised and skipped rather than mistaken for document
titles.

`tags` is authoritative. Tags absent from it must never appear in the response — some tags
are deliberately withheld from the extractor because they cannot be derived from the
document text.

`ocr_confidence` is **nullable**: pages OCR'd by a confidence-blind engine carry `null`.
Treat it as a hint only, never as a gate on your side — see §4.5.

### B. Required JSON Response Payload

```json
{
  "contract_version": "1.0",
  "status": "success",
  "engine": "kalanjiyam-archival",
  "model": {
    "name": "gemma-3-27b-it",
    "version": "1.0"
  },
  "taxonomy_version": "client-2026-08",
  "unit_id": "kalanjiyam:project/kalat-1932-17",
  "window_index": 3,
  "chars_in": 18234,
  "engine_latency_ms": 3120.4,
  "usage": {
    "prompt_tokens": 14320,
    "completion_tokens": 2870,
    "total_tokens": 17190
  },
  "fields_attempted": 19,
  "fields_returned": 11,
  "fields_declined": 8,
  "fields": {
    "TITLE": {
      "value": "Grant of an honorary commission to Lt. Shahzada Ahmad Yar Khan",
      "confidence": 0.91,
      "source": "record",
      "evidence": [
        {"page_slug": "61", "block_id": "b1", "quote": "Grant of an honorary commission"}
      ]
    },
    "DATE": {
      "value": "1932-03-11 (1350 AH)",
      "confidence": 0.88,
      "source": "record",
      "evidence": [
        {"page_slug": "61", "block_id": "b3", "quote": "11th March 1932"}
      ]
    },
    "PERSON NAME": {
      "confidence": 0.77,
      "value": [
        {
          "label": "Ahmad Yar Khan, Shahzada",
          "variants": ["Lt. Shahzada Ahmad Yar Khan"],
          "dates": "1904-1979",
          "source": "record",
          "evidence": [
            {"page_slug": "62", "block_id": "b7", "quote": "Lt. Shahzada Ahmad Yar Khan"},
            {"page_slug": "64", "block_id": "b2", "quote": "the Shahzada"}
          ]
        }
      ]
    },
    "SCOPE CONTENT": {
      "value": "Correspondence concerning the proposal to confer an honorary commission...",
      "confidence": 0.80,
      "source": "derived",
      "evidence": [
        {"page_slug": "61"}, {"page_slug": "62"}, {"page_slug": "63"}
      ]
    }
  }
}
```

---

## 3. Per-Window Metrics Collected from Response

From each single window response payload, Kalanjiyam automatically extracts and stores
per-window metrics:

| Per-Window Metric | API Response Field | Data Type | Value Range / Format | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`Engine`** | `engine` | String | e.g. `"kalanjiyam-archival"` | Identifier of the extraction engine used. |
| **`Model`** | `model.name`, `model.version` | String | e.g. `"gemma-3-27b-it"` / `"1.0"` | Model identity, for reproducibility of a run. |
| **`Taxonomy Version`** | `taxonomy_version` | String | e.g. `"client-2026-08"` | Which revision of the tag list was applied. |
| **`Fields Attempted`** | `fields_attempted` | Integer | Count of `tags` in the request | Tags requested for this window. |
| **`Fields Filled`** | `fields_returned` | Integer | Count of non-null entries in `fields` | Tags the model was able to support. |
| **`Fields Declined`** | `fields_declined` | Integer | attempted − returned | Tags refused for lack of evidence. **Not an error.** |
| **`Field Confidence`** | `fields[].confidence` | Float | `0.0` to `1.0` (e.g. `0.77` = `77%`) | Per-tag extraction confidence. |
| **`Chars In`** | `chars_in` | Integer | e.g. `18234` | Source characters consumed from the window. |
| **`Prompt Tokens`** | `usage.prompt_tokens` | Integer | e.g. `14320` | Input tokens billed for this window. |
| **`Completion Tokens`** | `usage.completion_tokens` | Integer | e.g. `2870` | Output tokens generated. |
| **`Engine-Latency`** | `engine_latency_ms` | Float | Milliseconds (e.g. `3120.4`) | Pure model processing latency for this window. |

### Metrics Kalanjiyam derives itself (do **not** send these)

| Derived Metric | Derivation | Purpose |
| :--- | :--- | :--- |
| **`Evidence Verified Rate`** | Quotes found verbatim in the request text ÷ filled fields | Objective quality signal — see §4. |
| **`Evidence Spans`** | Count of `evidence[]` entries | Density of citation. |
| **`Min / Mean Field Conf`** | Over `fields[].confidence` | Pinpoints the weakest tag to review. |
| **`Fields <0.7`** | Count of `fields[].confidence < 0.7` | Mirrors `low_conf_page_count` in the OCR spec. |
| **`Source OCR Confidence`** | Mean `page_confidence` of the window's pages, over pages that have one | Separates a bad model run from unreadable OCR. `null` when every page in the window came from a confidence-blind engine — see §4.5. |
| **`Extraction Latency`** | Wall time around the call | Minus `engine_latency_ms` = queue + transport overhead. |

---

## 4. Evidence & Provenance (Mandatory)

Every value must declare **where it came from**. This is not optional metadata: it is how
Kalanjiyam scores the run, and how a catalogue entry links back to the page image.

### 4.1 Evidence attaches to the *value*, not the tag

For list-valued tags (persons, places, subjects, formats), one citation per tag is
useless — a `PERSON NAME` list may hold forty names. Each entity object therefore carries
its own `evidence` array. Only single-valued tags (`text` / `prose` kinds) carry evidence
at field level.

### 4.2 The four `source` kinds

| `source` | Meaning | Carries a `quote`? |
| :--- | :--- | :--- |
| `record` | Stated in the document text. | **Yes — required.** |
| `derived` | Synthesised or computed (e.g. a summary, a date span). | No. Cites contributing `page_slug`s only. |
| `enrichment` | From an external authority file (coordinates, VIAF/Wikidata IDs). | No. Cites the authority source. |
| `curated` | Entered by an archivist. Never produced by the service. | No. |

An evidence span is `{"page_slug": str, "block_id": str, "quote": str}`. For `derived`
values, `block_id` and `quote` are omitted.

### 4.3 Verification

For every `record` value, Kalanjiyam checks that `quote` appears **verbatim
(whitespace-normalised) in the block text it sent**, and stores the result per span.

* A `record` value with **no** evidence span is treated as `confidence = 0`.
* A `record` value whose quote is **not found** in the source is treated as
  `confidence = 0` and flagged as unverifiable.

The service cannot influence this number. It is the only objective quality signal
available, because there is no ground-truth description set to score against. **A declined
field is always preferable to an invented one.**

### 4.4 Why evidence carries the quality signal

Three of the OCR engines in service (`chandra`, `glm-ocr`, `dots-ocr`) are VLM-based and
produce **no confidence signal at all** — they send `page_confidence: null` and
`page_p05: null` under OCR contract v2.2. For pages OCR'd by those engines there is no
input-quality score to gate on.

Evidence verification is what carries the weight there. It compares an extracted quote
against the OCR text itself, so it is entirely independent of whether the engine scored
its own output. On confidence-blind engines it is not merely the best signal available —
it is the only one. This is the strongest argument for making `evidence` mandatory rather
than encouraged.

### 4.5 Nulls must stay null

`ocr_confidence` in the request is nullable, and every metric derived from it is nullable
in turn. A `null` must never be coerced to `0.0` or `1.0` on the way through: the first
would exclude every page OCR'd by a confidence-blind engine, the second would assert
quality nothing measured. Averages are computed over pages that have a score, and reported
alongside a count of pages that did not. A document whose OCR gave no confidence at all
reports `avg_source_ocr_confidence: null`, not `0`.

The description's `DESCRIPTION` tag (ISAD(G) Area 7) records the absence in prose, which is
what that element is for.

### 4.6 Why block ids matter

Kalanjiyam persists `blocks[].bbox` and the page dimensions from the OCR response
(contract v2.1 §3). A verified evidence span therefore resolves to a rectangle on the page
image, letting a reader click any fact in the catalogue and see the pixels it was read
from. `block_id` is the join that makes this work — an evidence span without one still
counts as verified if the quote matches, but loses the image link.

---

## 5. Full Document Aggregated Metrics

As windows complete, Kalanjiyam reduces them into one description per document and
aggregates the metrics into **Document Level Rollups** (Admin UI Summary & CSV Exports):

| Document Metric | Aggregation Method | UI Header | Description |
| :--- | :--- | :--- | :--- |
| **`Windows`** | `count(windows)` / `count(completed)` | `Windows` | Total and completed window count. |
| **`Extraction Coverage`** | $\frac{\text{pages read}}{\text{pages total}}$ | `Coverage` | Proves a full pass ran rather than a sample. |
| **`Field Coverage`** | $\frac{\text{fields filled}}{\text{tags in schema}}$ | `Fields` | How much of the schema the document supports. |
| **`Avg Conf.`** | $\frac{\sum \text{field conf}}{\text{fields}}$ | `Avg Conf.` | Mean confidence across filled fields. |
| **`Min Conf.`** | $\min(\text{field conf})$ | `Min Conf.` | Weakest field in the description. |
| **`Fields <0.7`** | $\text{count}(\text{conf} < 0.7)$ | `Fields <0.7` | Fields needing archivist review. |
| **`Evidence Rate`** | verified spans ÷ `record` values | `Evidence` | The publishability gate. |
| **`Tokens`** | $\sum \text{prompt} + \sum \text{completion}$ | `Tokens` | Cost accounting per document. |
| **`Avg Engine Latency`** | $\frac{\text{total engine latency}}{\text{windows}}$ | `Avg Engine Latency` | Average model time per window (e.g. `3.12s/w`). |
| **`Src OCR Conf.`** | mean `page_confidence` of pages read | `Src OCR Conf.` | Quality of the input the model was given. |

---

## 6. Field Specification Checklist

### Top-Level Required Fields
* `contract_version` (String, Required): Must be `"1.0"`.
* `status` (String, Required): `"success"` or `"error"`.
* `engine` (String, Required): Extraction engine name.
* `model` (Object, Required): `{"name": "...", "version": "..."}`.
* `taxonomy_version` (String, Required): Echoed from the request. A response that does not
  say which schema it answered cannot be audited.
* `unit_id`, `window_index` (Required): Echoed from the request.
* `chars_in` (Integer, Required): Source characters consumed.
* `engine_latency_ms` (Float, Required): Model processing time in milliseconds.
* `usage` (Object, Required): `prompt_tokens`, `completion_tokens`, `total_tokens`.
* `fields_attempted`, `fields_returned`, `fields_declined` (Integers, Required).
* `fields` (Object, Required): Tag code → field object. May be empty.

### Field Object (`fields[<TAG>]`)
* `value` (String | Array, Required): String for `text`/`prose` tags; array of objects for
  entity and relation tags.
* `confidence` (Float, Required): `0.0` to `1.0`.
* `source` (String, Required for single-valued tags): one of `record`, `derived`,
  `enrichment`.
* `evidence` (Array, Required for single-valued `record` tags): see §4.

### Entity Object (inside an array-valued `fields[<TAG>].value`)
* `label` (String, Required): The form used in the record.
* `variants` (Array[String], Optional): Other forms seen in the same window.
* `dates` (String, Optional): Life/existence dates, if stated.
* `auth_id` (String, Optional): **Only if an identifier appears in the text itself.** Never
  invent VIAF, LCNAF or Wikidata identifiers.
* `source` (String, Required): as §4.2.
* `evidence` (Array, Required when `source` is `record`): as §4.
* `note` (String, Optional).

### Relation Object
* `subject`, `type`, `object` (Strings, Required), `note` (String, Optional),
  plus `source` and `evidence` as above.

---

## 7. Service Requirements

| Requirement | Rationale |
| :--- | :--- |
| **Constrained JSON output** (schema-guided decoding) | Truncated and unparseable generations are the dominant failure mode of the existing `/v1/chat` path. A schema-constrained decoder removes the class entirely. |
| **`max_tokens` ≥ 4,500** | Entity lists with variants plus an evidence quote per value are verbose; a lower ceiling truncates mid-object on dense windows. |
| **`finish_reason: "length"` on truncation** | A cut-off generation must not return as an ordinary `200` with a silently broken payload. |
| **Honour `tags`** | Return only requested tags. Withheld tags must never appear. |
| **Never invent a quote** | Every `record` value must quote text present in the request. Verified server-side; a fabricated quote scores worse than a declined field. |
| **Declining is correct** | A tag the window does not support must be omitted, never guessed. Counted in `fields_declined`, which is measured separately and is not an error. |
| **No cross-window state** | No statefulness, no segmentation, no aggregation. Kalanjiyam owns the reduce. |

---

## 8. HTTP Error Payload Format

If extraction fails (context overflow, model error, OOM), return an HTTP error (`400`,
`422`, `500`) with JSON detail:

```json
{
  "status": "error",
  "detail": "Window 3 exceeds context: 34210 tokens with instruction, limit 32768"
}
```

A failed window is retried on its own; it does not fail the document.
