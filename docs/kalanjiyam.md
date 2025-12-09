# Kalanjiyam: Comprehensive Feature Overview

Kalanjiyam is an advanced digital library and proofing platform dedicated to preserving and making accessible Siddha literature and knowledge systems. It combines robust text management with state-of-the-art OCR and translation capabilities to facilitate the digitization of ancient texts.

## 1. Advanced OCR Capabilities

Kalanjiyam integrates multiple Optical Character Recognition (OCR) engines to handle diverse document types and languages, with a special focus on Indic scripts like Sanskrit and Tamil.

### Supported Engines
*   **DeepSeek OCR**: A state-of-the-art multimodal large language model (MLLM) that provides:
    *   High accuracy (up to 97%) for ~100 languages.
    *   **Grounding Support**: Provides bounding box information for text elements.
    *   **Structured Output**: Converts documents directly to Markdown while preserving layout.
    *   GPU acceleration for high-performance processing.
*   **Surya OCR**: A lightweight, high-performance toolkit supporting 90+ languages.
    *   Specialized for line-level text detection and recognition.
    *   Efficient reading order detection and table recognition.
    *   Configurable for CPU or GPU usage.
*   **Google Vision OCR**: Reliable cloud-based OCR for general-purpose text extraction.
*   **Tesseract**: Open-source standard for offline OCR.
*   **Specialized Models**: Includes support for **Nanonets**, **Chandra**, and **Qwen3** for specific use cases.

### Batch Processing & Infrastructure
*   **Background Processing**: Utilizes **Celery** workers to handle long-running OCR tasks without blocking the user interface.
*   **Task Tracking**: Custom **Redis**-based system for tracking batch OCR operations across thousands of pages, allowing users to navigate away and resume monitoring later.

### OCR Performance Comparison
To ensure the highest quality digitization, Kalanjiyam includes a robust **OCR Comparison** feature that allows moderators to benchmark different engines.
*   **Ground Truth Evaluation**: Automatically compares OCR output against pages that have already been human-proofed (R1/R2 status).
*   **Quantitative Metrics**: Calculates standard accuracy metrics to objectively assess engine performance:
    *   **WER (Word Error Rate)**: Percentage of incorrect words.
    *   **CER (Character Error Rate)**: Percentage of incorrect characters.
    *   **Levenshtein Distance**: Measures the edit distance between generated text and ground truth.
*   **Detailed Reporting**: Provides both aggregate project-level statistics and side-by-side per-page comparisons to identify specific model weaknesses (e.g., handling of conjunct consonants or layout issues).
*   **Workflow**: Integrated directly into the project "Stats" dashboard, running as a background task to handle large datasets efficiently.

## 2. Translation Service

A comprehensive system for translating digitized content, bridging the gap between classical texts and modern readers.

*   **Multi-Engine Support**:
    *   **Google Translate**: For quick, broad-coverage translations.
    *   **OpenAI GPT**: For high-quality, context-aware translations suitable for complex literary texts.
*   **Intelligent Segmentation**: Automatically segments long texts into manageable chunks to preserve paragraph structure and optimize translation quality.
*   **Integration**: Translations are linked directly to page revisions, allowing for versioned translation history.
*   **Batch Workflow**: Supports translating entire projects in the background, with progress tracking and error resilience.

## 3. Digital Library & Text Management

Kalanjiyam uses a sophisticated data model to structure texts, ensuring they are not just stored but are semantically meaningful and navigable.

### TEI-XML Compliance
Texts are stored using the **Text Encoding Initiative (TEI)** XML standard, widely used in digital humanities. This ensures compatibility and long-term preservation.

### Hierarchical Structure
*   **Text**: The top-level entity representing a book or manuscript.
*   **TextSection**: Ordered divisions (e.g., *kāṇḍas*, *sargas*) that serve as the unit of viewing.
*   **TextBlock**: The atomic unit of reuse (e.g., a verse or paragraph), allowing for granular cross-referencing.

### Cross-Referencing
The system supports deep linking and cross-referencing between different texts at the block level, essential for connecting commentaries with root texts.

## 4. Proofing & Digitization Workflow

The platform provides a complete workflow for converting scanned PDFs into structured digital text.

### Powerful Editing Interface
At the heart of the proofing workflow is a sophisticated **TipTap-based rich text editor** customized for the specific needs of Indic text digitization.
*   **Split-Screen Layout**: Displays the original PDF page image side-by-side with the text editor, allowing for easy verification and transcription.
*   **Rich Text Features**:
    *   Standard formatting (Bold, Italic, Underline).
    *   Semantic markup for **Headings** (H1-H3), **Blockquotes**, and **Tables**.
    *   Support for **LaTeX math** rendering via KaTeX.
    *   Code blocks with syntax highlighting.
*   **Specialized Indology Tools**:
    *   **Transliterator**: Built-in tool to convert ITRANS or Harvard-Kyoto schemes into Devanagari or IAST script on the fly.
    *   **Virtual Keyboard**: Quick access to difficult-to-type special characters (diacritics, Vedic accents).
    *   **Semantic Annotations**: Tools to mark specific text segments as *errors* (`<sic>`), *corrections* (`<corr>`), *unclear text*, or *footnotes*, ensuring high-fidelity preservation of the source material.
*   **Shortcuts & usability**: Keyboard shortcuts for common actions (Undo/Redo, Save) to speed up the workflow.

*   **Project Management**: Work is organized into "Projects," each corresponding to a physical book or manuscript.
*   **Page-Level Granularity**: Each page of a PDF is treated as a distinct unit (`Page`) with its own status workflow.
*   **Revision History**: Complete version control (`Revision`) for every page edit, tracking authors, timestamps, and changes.
*   **Status Tracking**: Pages move through defined states (e.g., `Pending`, `R0`, `R1`, `Completed`) to ensure quality control.
*   **Collaboration**: Integrated discussion boards allow contributors to discuss difficult passages or digitization decisions.

## 5. Additional Features

*   **Multilingual Dictionaries**: Integrated dictionary support maps Sanskrit and Tamil expressions to definitions, aiding translators and readers.
*   **Blogging Platform**: Built-in blog engine for community updates, scholarly articles, and project news.
*   **Internationalization (i18n)**: The platform is designed to be multilingual, supporting user interfaces in different languages.

## 6. Technical Architecture

Kalanjiyam is built on a modern, scalable, and open-source technology stack.

*   **Backend**: Python with **Flask** web framework.
*   **Database**: **PostgreSQL** with **SQLAlchemy** ORM for robust data management.
*   **Asynchronous Tasks**: **Celery** with **Redis** broker for OCR, translation, and other background jobs.
*   **Containerization**: Fully Dockerized environment for consistent development and deployment.
*   **Frontend**: Server-side rendered templates with modern JavaScript enhancements for a responsive user experience.

