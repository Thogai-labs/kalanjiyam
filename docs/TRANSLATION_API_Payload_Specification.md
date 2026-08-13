# Translation API Response & Metrics Specification (v1.0)

> **Core Requirement**: When Kalanjiyam sends a text translation request via `POST /translate/text` (or `/v1/translate`), the Translation API microservice **MUST return the translated text AND execution metrics together in the same single JSON response**.

---

## 1. Single Request / Response Workflow

```
Kalanjiyam Server                     Translation API Service
       │                                         │
       │ --- POST /translate/text -------------> │
       │     Payload: text, src/tgt lang, model  │ (Processes translation)
       │ <--- 200 OK (Single JSON Payload) ----- │
       │      Includes:                          │
       │      - Translated Text                  │
       │      - Engine & Model Identity          │
       │      - Processing Latency & Confidence  │
       ▼                                         │
Kalanjiyam stores translation + metrics in DB
```

---

## 2. Required Request & Response Payloads

### A. HTTP Request (`POST /translate/text`)

```json
{
  "text": "धर्मक्षेत्रे कुरुक्षेत्रे समवेता युयुत्सवः।",
  "source_language": "Sanskrit",
  "target_language": "English",
  "model_name": "ai4bharat/indictrans2-indic-en-1B",
  "batch_size": 8
}
```

### B. Required JSON Response Payload

When translation completes, the service must return a single JSON response:

```json
{
  "status": "success",
  "engine": "indictrans2",
  "model": {
    "name": "ai4bharat/indictrans2-indic-en-1B",
    "version": "1.0"
  },
  "source_language": "sa",
  "target_language": "en",
  "text": "Gathered together on the sacred field of Kurukshetra, eager for battle...",
  "translated_text": "Gathered together on the sacred field of Kurukshetra, eager for battle...",
  "confidence": 0.965,
  "input_chars": 42,
  "output_chars": 73,
  "engine_latency_ms": 185.4
}
```

---

## 3. Required Fields & Metrics Mapping

From this single response payload, Kalanjiyam automatically extracts and records **key translation metrics**:

| Core Metric | API Response Field | Data Type | Value Range / Format | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`Engine`** | `engine` | String | e.g. `"indictrans2"`, `"nayan_sa-en"`, `"google"` | Identifier of the translation engine/model. |
| **`Source Lang`**| `source_language`| String | e.g. `"sa"`, `"hi"`, `"en"` | Source language ISO/BCP-47 code. |
| **`Target Lang`**| `target_language`| String | e.g. `"en"`, `"hi"`, `"ta"` | Target language ISO/BCP-47 code. |
| **`Confidence`** | `confidence` | Float | `0.0` to `1.0` (e.g. `0.965` = `96.5%`) | Model confidence score or BLEU quality estimate. |
| **`Input Chars`**| `input_chars` | Integer | e.g. `42` | Total character count of source text. |
| **`Output Chars`**| `output_chars` | Integer | e.g. `73` | Total character count of generated translation text. |
| **`Latency`** | `engine_latency_ms` | Float | Milliseconds (e.g. `185.4`) | Pure translation model execution time in ms. |

---

## 4. Field Specification Checklist

### Top-Level Required Fields
* `status` (String, Required): `"success"` or `"ok"`.
* `engine` (String, Required): Engine identifier (e.g. `"indictrans2"`).
* `source_language` (String, Required): Source language code (e.g. `"sa"`).
* `target_language` (String, Required): Target language code (e.g. `"en"`).
* `text` / `translated_text` (String, Required): The generated translation text.
* `confidence` (Float, Optional): Translation confidence score (`0.0` to `1.0`).
* `input_chars`, `output_chars` (Integers, Optional): Source vs target character counts.
* `engine_latency_ms` (Float, Required): Model translation processing time in milliseconds.

---

## 5. HTTP Error Payload Format

If translation fails (unsupported language pair, OOM, model error), return an HTTP error (`400`, `422`, `500`) with JSON detail:

```json
{
  "status": "error",
  "detail": "Unsupported language pair: Sanskrit (sa) -> French (fr) on model indictrans2-indic-en-1B"
}
```
