# Kalanjiyam Translation API

FastAPI service built from your `translation.py` logic (HF IndicTrans2 models, GPU selection, offline local model loading, and DOCX/PDF/TXT document translation).

## What this provides

- `GET /health` for status and GPU visibility
- `GET /languages` for supported language names/codes
- `GET /models` for available translation model names and supported directions
- `POST /translate/text` for text translation
- `POST /translate/document` for document translation (`.docx`, `.pdf`, `.txt`) returning translated `.docx`

## Setup

```bash
cd /home/ganesh/kalanjiyam-translation
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd /home/ganesh/kalanjiyam-translation
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload
```

## Run Tests

Unit tests (fast, mocked translation path):

```bash
cd /home/ganesh/kalanjiyam-translation
source .venv/bin/activate
python -m pytest -q
```

Run a specific translation endpoint test only:

```bash
cd /home/ganesh/kalanjiyam-translation
source .venv/bin/activate
python -m pytest -q tests/test_health.py -k translate_text
```

Expected result for both commands: all selected tests should pass (for example `4 passed`).

## Available Models

Use `GET /models` to fetch the translation models supported by this API.

Example response:

```json
[
  {
    "model_name": "ai4bharat/indictrans2-en-indic-1B",
    "key": "en-indic",
    "description": "English to Indic translation model",
    "source_languages": ["English"],
    "target_languages": ["Hindi", "Bengali", "Tamil"]
  }
]
```

## API Endpoints: Inputs and Expected Outputs

### `GET /health`

- Input: none
- Expected output (`200 OK`):

```json
{
  "status": "ok",
  "available_gpus": [0, 1, 2, 3],
  "offline_mode": {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1"
  }
}
```

### `GET /languages`

- Input: none
- Expected output (`200 OK`): JSON object with language name to language code mapping.

Example:

```json
{
  "English": "eng_Latn",
  "Hindi": "hin_Deva",
  "Tamil": "tam_Taml"
}
```

### `POST /translate/text`

- Input (`application/json`):

```json
{
  "text": "Hello world",
  "model_name": "ai4bharat/indictrans2-en-indic-1B",
  "source_language": "English",
  "target_language": "Hindi",
  "gpu_id": 0,
  "batch_size": 8
}
```

- Expected output (`200 OK`):

```json
{
  "text": "...translated text..."
}
```

- Common error outputs:
  - `400`: invalid language names or invalid source/target combination
  - `400`: GPU id not available
  - `500`: no GPUs detected

### `POST /translate/document`

- Input (`multipart/form-data`):
  - `file`: `.docx`, `.pdf`, or `.txt`
  - `model_name`: one of the values returned by `GET /models`
  - `source_language`: e.g. `English`
  - `target_language`: e.g. `Hindi`
  - `gpu_id`: integer (default `0`)
  - `batch_size`: integer (default `8`)

- Expected output (`200 OK`): file download response (`.docx`) with translated content.

- Common error outputs:
  - `400`: unsupported file type
  - `400`: invalid language names
  - `400`: GPU id not available
  - `500`: no GPUs detected

## Example calls

Health check:

```bash
curl -sS http://127.0.0.1:8000/health
```

Translate text:

```bash
curl -sS -X POST "http://127.0.0.1:8000/translate/text" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello world",
    "model_name": "ai4bharat/indictrans2-en-indic-1B",
    "source_language": "English",
    "target_language": "Hindi",
    "gpu_id": 0,
    "batch_size": 8
  }'
```

Translate a document:

```bash
curl -X POST "http://127.0.0.1:8000/translate/document" \
  -F "file=@/absolute/path/input.docx" \
  -F "model_name=ai4bharat/indictrans2-en-indic-1B" \
  -F "source_language=English" \
  -F "target_language=Hindi" \
  -F "gpu_id=0" \
  -F "batch_size=8" \
  --output translated_output.docx
```

## Notes

- API keeps your original offline behavior (`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`).
- Models are loaded lazily on first request and cached per `(gpu_id, model_type)`.
- You must have local model files available in Hugging Face cache for offline mode.
