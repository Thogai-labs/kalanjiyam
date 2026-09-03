Background tasks with Celery
============================

Sometimes, a user on Kalanjiyam will request an operation that takes a long time
for our application code to process. Some examples of long-running tasks include:

- calling third-party OCR APIs (Google Vision, Dots OCR, Tesseract).
- machine translation (IndicTrans2, Nayan, LLMs).
- splitting and pre-processing large PDF or DOCX files into page images.
- full-text search indexing with OpenSearch.
- full-document archival metadata extraction.
- bulk S3 ingestion and synchronization.

To handle these background tasks efficiently and prevent head-of-line blocking,
we use `Celery`_ with Redis broker priority queues and dedicated worker partitions.

.. _Celery: https://docs.celeryq.dev/en/stable/


Quickstart
----------

First, ensure Redis is running::

    make redis

Then, start the Celery worker (with auto-scaling enabled across all queues)::

    make celery

Or using Docker Compose::

    make docker-start


Dedicated Queue Architecture
----------------------------

To ensure long-running operations (like converting a 1,000-page PDF or running full-text archival extraction) do not starve interactive user requests (like a single-page OCR or translation in the live proofreading editor), tasks are partitioned across dedicated queues:

* ``pdf_processing``: CPU and memory-intensive PDF splitting, DOCX segmentation, and page rasterization (routed from ``kalanjiyam.tasks.projects.*``).
* ``ocr``: Interactive and batch optical character recognition, enhanced preprocessing, and comparison jobs (routed from ``kalanjiyam.tasks.ocr.*``, ``kalanjiyam.tasks.comparison.*``).
* ``translation``: Machine translation tasks across IndicTrans2, Nayan, and LLMs (routed from ``kalanjiyam.tasks.translation.*``).
* ``s3_batch``: Bulk image/PDF folder synchronization and batch OCR ingestion (routed from ``kalanjiyam.tasks.s3_batch.*``).
* ``metadata``: Token-budgeted full-document archival description and metadata extraction (routed from ``kalanjiyam.tasks.archival_extract.*``).
* ``search_index``: OpenSearch full-text search index synchronization (routed from ``kalanjiyam.tasks.search_index.*``).
* ``low_priority``: Throttled guest/unauthenticated tasks and low-priority maintenance.
* ``default``: General asynchronous tasks, notifications, and emails.


Task Priorities & Anti-Starvation Scheduling
--------------------------------------------

Celery is configured with Redis message priority scheduling (``priority_steps`` 0 to 9). Tasks are dispatched with explicit priority levels:

* **Priority 9 (Interactive)**: Single-page editor OCR and translations initiated by an active user proofreader.
* **Priority 7 (High)**: Single-document user uploads and project creations.
* **Priority 5 (Default)**: General background tasks.
* **Priority 3 (Batch)**: Whole-book UI batch OCR and project translation jobs.
* **Priority 2 (Background)**: S3 batch folder ingestion, archival metadata, and search indexing.
* **Priority 1 (Low)**: Unauthenticated / guest requests.

When a 500-page batch job is queued at Priority 3, any user in the live editor clicking "Run OCR" or "Translate" (Priority 9) **jumps straight to the front of the queue** and executes immediately without waiting for the batch job to complete.


Worker Concurrency & Autoscaling
--------------------------------

Worker containers are configured according to their workload type:

* **I/O-Bound Workers (``ocr``, ``translation``)**:
  Use ``--autoscale=8,2 --prefetch-multiplier=1``. Because external model API calls spend most of their time in network I/O wait, workers scale up to 8 concurrent threads to process multiple pages simultaneously.
* **CPU-Bound Workers (``pdf_processing``)**:
  Use ``--autoscale=4,1 --prefetch-multiplier=1`` with prefork multiprocessing to handle heavy PyMuPDF rasterization without exhausting system RAM.
* **Batch & Maintenance Workers (``s3_batch``, ``metadata``)**:
  Dedicated containers with isolated resource limits to ensure heavy background processing never interferes with real-time web traffic.


Redis OCR Caching & Cache Stampede Prevention
---------------------------------------------

To minimize repeated S3 fetches and external API costs:

* **Version-Validated Envelopes**: Cached OCR documents and bounding boxes in Redis include document versions and revision IDs. If a revision changes in the database, stale keys are automatically evicted on read.
* **Automatic Invalidation**: Creating a new revision (via ``add_revision``) automatically invalidates cached page OCR and bounding box entries.
* **Cache Stampede Locking**: When multiple concurrent requests experience a cache miss for the same page or revision, Redis distributed locking (``acquire_stampede_lock`` / ``release_stampede_lock``) elects a single leader to fetch/decompress the payload from S3/VersityGW while follower requests wait and receive the populated cache result via ``coalesce_cache_fetch``.


Related Documentation
--------------------

- :doc:`batch-ocr-task-tracking` - Specific implementation details for batch OCR task tracking
- :doc:`production-deploy` - Production deployment and container topologies
- :doc:`Developer-s-documentation` - Complete developer guide and CLI reference
