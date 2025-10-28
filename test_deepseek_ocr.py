#!/usr/bin/env python3
"""Test script for DeepSeek OCR implementation."""

import sys
import logging
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from kalanjiyam.utils.ocr_engine import OcrEngineFactory, run_ocr

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_deepseek_ocr_availability():
    """Test if DeepSeek OCR can be imported and initialized."""
    try:
        logger.info("Testing DeepSeek OCR availability...")
        
        # Check CUDA availability
        import torch
        if torch.cuda.is_available():
            logger.info(f"✓ CUDA available: {torch.cuda.get_device_name(0)}")
        else:
            logger.warning("⚠ CUDA not available - DeepSeek OCR will use CPU (slower)")
        
        # Test factory creation
        engine = OcrEngineFactory.create('deepseek')
        logger.info("✓ DeepSeek OCR engine created successfully")
        
        # Test supported languages
        languages = engine.get_supported_languages()
        logger.info(f"✓ Supported languages: {len(languages)} languages")
        logger.info(f"  Including Sanskrit: {'sa' in languages}")
        
        # Test supported engines
        engines = OcrEngineFactory.get_supported_engines()
        logger.info(f"✓ Supported engines: {engines}")
        logger.info(f"  DeepSeek included: {'deepseek' in engines}")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ DeepSeek OCR test failed: {e}")
        return False


def test_deepseek_ocr_with_sample():
    """Test DeepSeek OCR with a sample image (if available)."""
    try:
        logger.info("Testing DeepSeek OCR with sample image...")
        
        # Look for a sample image in the project
        sample_images = [
            Path("test/sample_image.jpg"),
            Path("test/sample_image.png"),
            Path("data/sample_image.jpg"),
            Path("data/sample_image.png"),
        ]
        
        sample_image = None
        for img_path in sample_images:
            if img_path.exists():
                sample_image = img_path
                break
        
        if not sample_image:
            logger.warning("No sample image found, skipping OCR test")
            return True
        
        logger.info(f"Using sample image: {sample_image}")
        
        # Test OCR
        result = run_ocr(sample_image, engine_name='deepseek', language='sa')
        
        logger.info(f"✓ OCR completed successfully")
        logger.info(f"  Text length: {len(result.text_content)} characters")
        logger.info(f"  Bounding boxes: {len(result.bounding_boxes)}")
        logger.info(f"  Confidence: {result.confidence}")
        logger.info(f"  Language: {result.language}")
        
        # Show first 200 characters of extracted text
        preview = result.text_content[:200]
        if len(result.text_content) > 200:
            preview += "..."
        logger.info(f"  Text preview: {preview}")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ DeepSeek OCR sample test failed: {e}")
        return False


def main():
    """Run all DeepSeek OCR tests."""
    logger.info("Starting DeepSeek OCR tests...")
    
    tests = [
        ("Availability Test", test_deepseek_ocr_availability),
        ("Sample Image Test", test_deepseek_ocr_with_sample),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        logger.info(f"\n--- {test_name} ---")
        if test_func():
            passed += 1
            logger.info(f"✓ {test_name} PASSED")
        else:
            logger.error(f"✗ {test_name} FAILED")
    
    logger.info(f"\n--- Test Summary ---")
    logger.info(f"Passed: {passed}/{total}")
    
    if passed == total:
        logger.info("🎉 All tests passed! DeepSeek OCR is ready to use.")
        return 0
    else:
        logger.error("❌ Some tests failed. Check the logs above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
