Batch OCR CLI
=============

This document provides complete documentation for the Kalanjiyam Batch OCR Command-Line Interface (CLI).
The Batch CLI allows administrators and developers to bulk-ingest PDFs or image directories from local filesystems or S3 object storage into Kalanjiyam, attach them to Organizations, and perform asynchronous OCR via Celery.

Overview
--------

The Batch CLI interface is exposed through `scripts/cli.py` inside the `kalanjiyam-web` container. It handles:

1. **Discovery**: Recursively scanning target S3 bucket paths or local directories for PDFs or image folders.
2. **Database Tracking**: Registering a `BatchJob` and corresponding `BatchItem` records in PostgreSQL.
3. **Organization Scoping**: Attaching created projects directly to an Organization by URL slug (`--org`).
4. **Language Selection**: Setting target OCR language (defaults to `eng`).
5. **Asynchronous Queueing**: Dispatching individual items to the Celery background worker queue for processing.

Command Reference
-----------------

batch-ocr
~~~~~~~~~

Ingest and process PDFs or Image directories from S3 or a local filesystem.

**Syntax:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-ocr [OPTIONS]

**Options:**

* ``--s3-uri TEXT``: S3 URI target path (e.g., ``s3://my-bucket/batch_pdfs/``).
* ``--local-uri TEXT``: Local filesystem path inside the container (e.g., ``/data/uploads/batch_pdfs/``).
* ``--org TEXT``: Organization slug to attach processed projects to (e.g., ``udaan``). Fails fast if the organization does not exist.
* ``--lang, --language TEXT``: Target OCR language code (default: ``eng``). Examples: ``eng``, ``tam``, ``hin``.
* ``--pdf``: Process PDF files only.
* ``--image``: Process image directories only.

**Examples:**

*Process local PDFs for an organization in English:*

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-ocr --local-uri /data/uploads/batch_pdfs/ --pdf --org "udaan" --lang "eng"

*Process an S3 bucket for Tamil documents:*

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-ocr --s3-uri s3://my-bucket/documents/ --org "udaan" --lang "tam"

import-jsonl
~~~~~~~~~~~~

Import externally generated page-level JSONL OCR together with source PDFs.
The JSONL identifier is ``<bookId>↳<pageNumber>``; page numbers are 1-based and
must exactly match source-PDF positions. PDF filenames must be ``<bookId>.pdf``.

.. code-block:: bash

   docker exec -it kalanjiyam-web python cli.py import-jsonl \
     --jsonl-uri s3://my-bucket/imports/jsonl/ \
     --pdf-uri s3://my-bucket/imports/pdfs/ \
     --org udaan --dry-run

``--dry-run`` performs discovery and validation only. The importer uses the
existing 200-DPI PDF rendering behavior; bbox/image alignment is intentionally
not configurable in this first version.

Local directories are supported when the importer cannot access S3. For example,
with ``/home1/kalanjiyam-data/uploads/batch_imports/jsonl`` and ``pdfs``:

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py import-jsonl \
     --jsonl-uri /data/uploads/batch_imports/jsonl \
     --pdf-uri /data/uploads/batch_imports/pdfs \
     --org nai

batch-list
~~~~~~~~~~

List recent batch jobs with their ID, status, creation timestamp, duration/time taken, and target URI.

**Syntax:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-list [OPTIONS]

**Options:**

* ``--limit INTEGER``: Number of recent batch jobs to display (default: 20).

**Example Output:**

::

   ID    | Status          | Created At             | Time Taken   | Target
   ---------------------------------------------------------------------------------------------
   7     | COMPLETED       | 2026-07-28 09:25:29    | 2m 45s       | /data/uploads/batch_pdfs/
   6     | FAILED          | 2026-07-28 09:04:42    | 12s          | /data/uploads/batch_pdfs/

batch-status
~~~~~~~~~~~~

Check detailed item breakdown and performance metrics (time taken, extraction latency, OCR latency, payload sizes) for a specific job or recent jobs.

**Syntax:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-status [OPTIONS]

**Options:**

* ``--job-id INTEGER``: Batch Job ID to inspect. If omitted, shows detailed status for the 5 most recent jobs.

**Example Output:**

::

   === BatchJob #7 ===
   Target URI : /data/uploads/batch_pdfs/
   Job Status : COMPLETED
   Created At : 2026-07-28 09:25:29.168575
   Time Taken : 2m 45s
   Progress   : 5/5 Completed (0 Failed, 0 Processing, 0 Pending)
   Avg Item Processing Time : 33s
   Total Size : 14.25 MB
   Avg Extraction Latency   : 120.45 ms
   Avg OCR Latency          : 450.12 ms

batch-cancel
~~~~~~~~~~~~

Cancel an in-progress or pending batch job. Marks pending items as failed with ``Cancelled by user``.

**Syntax:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-cancel --job-id <JOB_ID>

**Example:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-cancel --job-id 7

batch-retry
~~~~~~~~~~~

Re-queue failed or incomplete items for an existing batch job without re-scanning files.

**Syntax:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-retry --job-id <JOB_ID> [OPTIONS]

**Options:**

* ``--job-id INTEGER``: Batch Job ID to retry (required).
* ``--org TEXT``: Organization slug (optional).
* ``--lang, --language TEXT``: OCR language code (default: ``eng``).

**Example:**

.. code-block:: bash

   docker exec -it kalanjiyam-web python scripts/cli.py batch-retry --job-id 7 --org "udaan" --lang "eng"

Architecture & Data Models
--------------------------

* **BatchJob**: Represents a single batch run containing `target_uri`, `status`, `created_at`, and `completed_at`.
* **BatchItem**: Represents an individual file/folder inside a `BatchJob`. Tracks progress (`status`), performance metrics (`extraction_latency_ms`, `total_ocr_latency_ms`, `source_size_bytes`), and links to the resulting `proof_projects` project ID.
* **Organization Scoping**: Projects created by batch runs are assigned to the target organization via the `project_groups` table, ensuring proper multi-tenant access control and visibility.
