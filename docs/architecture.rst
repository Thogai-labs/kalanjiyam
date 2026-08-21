Architecture
===========

This document provides a high-level overview of Kalanjiyam's technical architecture.

Overview
--------

Kalanjiyam is a web application built with Flask that provides access to Siddha Knowledge Systems
literature. The application consists of several key components:

- **Web server**: Flask application that serves HTML pages and API endpoints
- **Database**: SQLite database (development) or PostgreSQL (production) for storing structured metadata
- **Object Storage**: S3-compatible dual-tier storage (via Versity Gateway or cloud S3) for page scans and compressed spatial JSON documents
- **Full-Text Search**: OpenSearch cluster with ICU analysis for multilingual manuscript search
- **Background tasks**: Celery across multiple queues for OCR, batch imports, search indexing, and metadata extraction
- **Static assets**: CSS and JavaScript bundled via Tailwind and esbuild

Data Sources
------------

Kalanjiyam's data comes from several sources:

- **Text data**: Siddha texts from various manuscripts and published sources
- **Dictionary data**: Traditional Siddha and Tamil dictionaries
- **Parse data**: Grammatical analysis data from the Digital Corpus of Siddha
  in `kalanjiyam.seed.dcs`. (Our raw upstream parse data uses the CoNLL-U format, but
  we transform it into a more suitable format for our needs.)

Database
--------

We use SQLAlchemy as our ORM and SQLite for lightweight local prototyping.

For production, we use PostgreSQL for concurrency, multi-tenancy, and data integrity.

Key database table groups (41 tables total across models):

- **Authentication & RBAC**: `users`, `roles`, `user_roles`, `auth_password_reset_tokens`
- **Multi-Tenancy & Quotas**: `groups`, `user_groups`, `text_groups`, `project_groups`
- **Proofing System**: `proof_projects`, `proof_pages`, `proof_page_statuses`, `proof_page_versions`, `proof_revisions`, `proof_translations`, `proof_ocr_comparisons`, `genres`
- **Batch Processing**: `batch_jobs`, `batch_items`, `batch_ocr_chunks`, `batch_ocr_pages`
- **Archival Metadata Extraction**: `metadata_extraction_runs`, `metadata_windows`, `metadata_fields`, `metadata_evidence`
- **Search Indexing**: `search_index_jobs`
- **Digital Library Texts**: `texts`, `text_sections`, `text_blocks`, `block_parses`
- **Dictionaries**: `dictionaries`, `dictionary_entries`
- **Forums & Site**: `discussion_boards`, `discussion_threads`, `discussion_posts`, `blog_posts`, `site_project_sponsorship`, `contributor_info`, `system_settings`, `system_metric_logs`, `usage_logs`, `reported_issues`

Frontend
--------

Our frontend is built with vanilla HTML, CSS, and JavaScript. We use:

- **Tailwind CSS** for styling
- **Vanilla JavaScript** for interactivity
- **HTMX** for dynamic content updates
- **Sanscript.js** for script transliteration

We avoid complex frontend frameworks to keep the codebase simple and maintainable.

Background Tasks
---------------

We use Celery for background processing across 6 dedicated queues:

- `default`: General asynchronous tasks and emails
- `ocr`: Interactive single-page OCR and comparison jobs
- `s3_batch`: Bulk PDF/image folder batch OCR ingestion
- `metadata`: Token-budgeted full-document archival metadata extraction
- `search_index`: Incremental OpenSearch indexing and sync jobs
- `low_priority`: Heavy background exports and non-urgent maintenance

Deployment
----------

Kalanjiyam is deployed using Docker containers (orchestrated via Docker Compose):

- **Web container** (`kalanjiyam-web`): Flask web app behind Gunicorn
- **Database container** (`kalanjiyam-db`): PostgreSQL 15
- **Redis container** (`kalanjiyam-redis`): Celery task broker and result backend
- **Celery worker containers** (`kalanjiyam-celery`, `kalanjiyam-celery-batch`, `kalanjiyam-celery-metadata`): Background workers
- **Search container** (`kalanjiyam-search`): OpenSearch with ICU analysis plugin
- **Storage gateway** (`kalanjiyam-versitygw`): S3 POSIX storage adapter

Security
--------

We follow several security best practices:

- **HTTPS everywhere**: All traffic is encrypted
- **CSRF protection**: All forms are protected against CSRF attacks
- **Input validation**: All user input is validated and sanitized
- **SQL injection protection**: We use parameterized queries
- **XSS protection**: We escape all user-generated content

Monitoring
----------

We use several tools for monitoring:

- **Sentry**: Error tracking and performance monitoring
- **Logs**: Structured logging for debugging and analysis
- **Health checks**: Automated health checks for all services
- **Metrics**: Basic metrics collection for performance analysis
