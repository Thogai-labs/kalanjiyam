# DeepSeek OCR Integration

This document explains how to use the newly integrated DeepSeek OCR engine in the Kalanjiyam project.

## Overview

DeepSeek OCR is a state-of-the-art multimodal large language model (MLLM) designed for efficient optical character recognition. It supports approximately 100 languages including Sanskrit and provides high accuracy OCR with grounding capabilities.

## Features

- **High Accuracy**: Achieves up to 97% OCR precision
- **Multilingual Support**: Supports ~100 languages including Sanskrit (sa)
- **Structured Output**: Converts documents to Markdown format while preserving layout
- **Grounding Support**: Provides bounding box information for text elements
- **GPU Acceleration**: Supports CUDA for faster processing

## Installation

The required dependencies have been added to `requirements.txt`:

```
torch>=2.0.0
transformers>=4.30.0
tokenizers>=0.20.0
einops
addict
easydict
flash-attn>=2.7.0
```

Install them with:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```python
from kalanjiyam.utils.ocr_engine import run_ocr
from pathlib import Path

# Run DeepSeek OCR on an image
image_path = Path("path/to/your/image.jpg")
result = run_ocr(image_path, engine_name='deepseek', language='sa')

print(f"Extracted text: {result.text_content}")
print(f"Confidence: {result.confidence}")
print(f"Bounding boxes: {len(result.bounding_boxes)}")
```

### Using the OCR Engine Factory

```python
from kalanjiyam.utils.ocr_engine import OcrEngineFactory

# Create DeepSeek OCR engine
engine = OcrEngineFactory.create('deepseek')

# Run OCR
result = engine.run(image_path, language='sa')

# Get supported languages
languages = engine.get_supported_languages()
print(f"Supported languages: {languages}")
```

### Using in Background Tasks

DeepSeek OCR can be used in Celery background tasks:

```python
from kalanjiyam.tasks.ocr import run_ocr_for_page

# Run OCR task with DeepSeek (engine='4' for DeepSeek)
task = run_ocr_for_page.delay(
    app_env='development',
    project_slug='your-project',
    page_slug='your-page',
    engine='4',  # DeepSeek OCR
    language='sa'
)
```

## Supported Languages

DeepSeek OCR supports many languages including:

- `sa` - Sanskrit
- `en` - English
- `hi` - Hindi
- `te` - Telugu
- `mr` - Marathi
- `bn` - Bengali
- `gu` - Gujarati
- `kn` - Kannada
- `ml` - Malayalam
- `ta` - Tamil
- `pa` - Punjabi
- `or` - Odia
- `ur` - Urdu
- `ar` - Arabic
- `fa` - Persian
- `th` - Thai
- `ko` - Korean
- `ja` - Japanese
- `zh` - Chinese
- `ru` - Russian
- And many more...

## Configuration

### Device Selection

DeepSeek OCR automatically detects available devices:

```python
# Use auto device detection (default)
engine = OcrEngineFactory.create('deepseek')

# Force CPU usage
engine = OcrEngineFactory.create('deepseek', gpu_config={'device': 'cpu'})

# Force CUDA usage
engine = OcrEngineFactory.create('deepseek', gpu_config={'device': 'cuda'})
```

### Custom Prompts

You can customize the OCR prompt for specific use cases:

```python
from kalanjiyam.utils.deepseek_ocr import DeepSeekOcrEngine

engine = DeepSeekOcrEngine()
custom_prompt = "<image>\n<|grounding|>Extract only the Sanskrit text from this document."

result = engine.run(image_path, prompt=custom_prompt, language='sa')
```

## Performance Considerations

- **GPU-First Approach**: DeepSeek OCR tries GPU first, then falls back to CPU if GPU is unavailable
- **GPU Memory**: DeepSeek OCR requires significant GPU memory (8GB+ VRAM recommended for optimal performance)
- **CPU Fallback**: Will automatically use CPU if GPU is not available (slower but functional)
- **Model Loading**: The model is loaded once and reused for multiple OCR operations
- **Batch Processing**: For multiple images, consider using the background task system

## Troubleshooting

### Common Issues

1. **CUDA Not Available**: DeepSeek OCR will automatically fall back to CPU
   ```
   WARNING: CUDA not available - falling back to CPU (slower performance)
   ```
   **Solution**: Install CUDA drivers for better performance, or use CPU mode.

2. **GPU Memory Error**: Insufficient VRAM for the model
   ```
   RuntimeError: CUDA out of memory
   ```
   **Solution**: Use a GPU with more VRAM (8GB+ recommended) or the system will fall back to CPU.

3. **Import Error**: Ensure all dependencies are installed correctly
   ```bash
   pip install torch>=2.0.0 transformers>=4.30.0 flash-attn>=2.7.0
   ```

4. **Model Loading Failed**: Check internet connection and Hugging Face access
   ```bash
   # Test model access
   python -c "from transformers import AutoModel; AutoModel.from_pretrained('deepseek-ai/DeepSeek-OCR')"
   ```

### Testing

Run the test script to verify DeepSeek OCR is working:

```bash
python test_deepseek_ocr.py
```

## Comparison with Other OCR Engines

| Engine | Accuracy | Speed | Languages | Special Features |
|--------|----------|-------|-----------|------------------|
| Google OCR | High | Fast | Many | Cloud-based |
| Tesseract | Medium | Medium | Many | Local, configurable |
| Surya OCR | High | Medium | 90+ | Layout-aware |
| DeepSeek OCR | Very High | Medium | ~100 | Markdown output, grounding |

## References

- [DeepSeek OCR Hugging Face Model](https://huggingface.co/deepseek-ai/DeepSeek-OCR)
- [DeepSeek OCR GitHub Repository](https://github.com/deepseek-ai/DeepSeek-OCR)
- [Hugging Face OCR Blog](https://huggingface.co/blog/ocr-open-models)
