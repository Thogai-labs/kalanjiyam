# OCR Comparison Feature

This feature allows proofreaders and moderators to run different OCR engines on a project and compare the results against "Ground Truth" (pages that have already been proofed). This helps in evaluating the performance of different OCR models (Google, Tesseract, Surya, etc.) for specific texts.

## How it Works

1.  **Ground Truth Selection**: The system identifies pages in the project that have been marked as "Reviewed" (R1 or R2 status). The content of the latest revision of these pages is treated as the Ground Truth.
2.  **OCR Execution**: A Celery background task runs the selected OCR engine on the images of these proofed pages.
3.  **Comparison**: The system compares the generated OCR text with the Ground Truth using standard metrics:
    *   **WER (Word Error Rate)**: The percentage of words that differ.
    *   **CER (Character Error Rate)**: The percentage of characters that differ.
    *   **Levenshtein Distance**: The minimum number of single-character edits required to change one word into the other.
4.  **Reporting**: Results are aggregated to show average error rates for the project, and detailed per-page comparisons are available to inspect specific differences.

## Usage

1.  Navigate to the **Stats** page of a project (requires Moderator access).
2.  In the "OCR Comparisons" section, select an OCR engine from the dropdown (e.g., Google, Surya, Tesseract).
3.  Click **Run Comparison**.
4.  The task will run in the background. Refresh the page to see the status update.
5.  Once completed, click **View** to see the detailed report, including overall metrics and side-by-side text comparisons for each page.

## Technical Implementation

*   **Model**: `OCRComparison` stores the task status, engine used, and JSON blobs for summary metrics and detailed page results.
*   **Task**: `kalanjiyam.tasks.comparison.run_ocr_comparison_task` handles the logic of fetching pages, running OCR, and calculating metrics using `jiwer` and `python-Levenshtein`.
*   **Views**: New routes in `proofing/project.py` handle the UI interactions.

## Supported Engines

The feature supports all OCR engines configured in the system, including:
*   Google Vision
*   Tesseract
*   Surya
*   Nanonets
*   DeepSeek
*   Chandra
*   Qwen3

