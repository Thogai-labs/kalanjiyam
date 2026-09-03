Production deployment
=====================

Kalanjiyam runs as two services: the **main app** (this repo) and the **OCR service**
(`kalanjiyam-ocr-service <https://github.com/Thogai-labs/kalanjiyam-ocr-service>`_).
The app calls OCR over HTTP; GPU models live only on the OCR host.

For multi-tenant organizations, super/org admin setup, and the admin UI, see
:doc:`multi-tenant`.


Architecture
------------

::

   Browser → nginx → Gunicorn (Kalanjiyam)
                       ├── PostgreSQL
                       ├── Redis → Celery (queues: default, ocr, low_priority, s3_batch, search_index, metadata)
                       ├── OpenSearch (search index)
                       ├── HTTP → OCR service (OCR_SERVICE_URL)
                       ├── HTTP → Translation service (TRANSLATION_SERVICE_URL)
                       └── S3 API → Versity Gateway → ~/kalanjiyam-data/uploads/


OCR service (deploy first)
--------------------------

On a CPU or GPU machine::

   git clone <ocr-service-repo> kalanjiyam-ocr-service
   cd kalanjiyam-ocr-service
   cp .env.example .env
   pip install -r requirements.txt

Verify: ``curl http://<ocr-host>:5001/health``

Restrict ``/admin`` on the OCR host to internal networks or VPN.


Kalanjiyam app server
---------------------

Install::

   git clone <kalanjiyam-repo> kalanjiyam
   cd kalanjiyam
   python3 -m venv env && source env/bin/activate
   pip install -r requirements.txt
   npm ci && make css js
   cp .env.example .env

Required production environment variables:

- ``FLASK_ENV=production``
- ``SECRET_KEY`` — long random string
- ``SQLALCHEMY_DATABASE_URI`` — PostgreSQL URI
- ``FLASK_UPLOAD_FOLDER`` — absolute path (e.g. ``/data/uploads`` in Docker, ``/srv/kalanjiyam/uploads`` bare-metal)
- ``APPLICATION_URL_PREFIX=/kalanjiyam`` — required when hosting under a subpath
- ``REDIS_URL`` — e.g. ``redis://kalanjiyam-redis:6379/0``
- ``OCR_BACKEND=remote``
- ``OCR_SERVICE_URL`` — full URL to the OCR service (e.g. ``http://<ocr-host>:8000``)
- ``OCR_SERVICE_API_KEY`` — same as OCR service ``API_KEY``
- ``KALANJIYAM_BOT_PASSWORD``
- ``POSTGRES_PASSWORD`` — PostgreSQL password for Docker deployments
- ``KALANJIYAM_HOST_IP`` — host IP Docker binds the web port to (default ``127.0.0.1``)
- ``KALANJIYAM_HOST_PORT`` — host port for the web container (default ``5000``)
- ``SENTRY_DSN`` — required for ``ProductionConfig``

Storage (S3-compatible, required when ``STORAGE_BACKEND=s3``):

- ``STORAGE_BACKEND=s3`` — use ``local`` to write directly under ``FLASK_UPLOAD_FOLDER``
- ``S3_ACCESS_KEY_ID`` — credential for the S3 gateway
- ``S3_SECRET_ACCESS_KEY`` — credential for the S3 gateway
- ``S3_BUCKET`` — bucket name (default ``uploads``; the Docker Compose creates it automatically)
- ``S3_ENDPOINT_URL`` — set by Compose to the bundled Versity Gateway; override for external S3
- ``S3_REGION`` — optional; self-hosted gateways ignore it
- ``S3_PUBLIC_ENDPOINT_URL`` — optional; if set, page images redirect to presigned URLs rather than streaming through Flask

Optional multi-tenant flags (enable after bootstrap; see :doc:`multi-tenant`)::

   MULTI_TENANT_MODE=true
   ENFORCE_ORG_ACCESS=true
   DEFAULT_PROJECT_REQUIRES_ORG=true
   ENFORCE_GROUP_ACCESS_FOR_PROJECTS=true

Database::

   alembic upgrade head
   python scripts/add_ocr_comparison_table.py

Translations (optional)
^^^^^^^^^^^^^^^^^^^^^^^

``make install-i18n`` clones ``kalanjiyam-i18n`` from GitHub. If that repository
is private or unavailable, skip this step — the app runs in English without compiled
``.mo`` files. To install later, clone the repo into ``data/kalanjiyam-i18n`` with
credentials you have, then run ``make babel-compile``.


Multi-tenant bootstrap
----------------------

After migrations, before enabling ``MULTI_TENANT_MODE`` in production::

   ./cli.py create-super-admin
   ./cli.py create-organization --name "Default Org" --slug default
   ./cli.py assign-org-admin --org default --username orgadmin --email orgadmin@example.com

Run safety checks and apply fixes for existing data::

   python scripts/migrate_multi_tenant.py
   python scripts/migrate_multi_tenant.py --apply --default-org-slug default

Then set multi-tenant flags in ``.env``, restart the app, and use the web admin:

- ``/admin/platform/`` — platform overview
- ``/admin/groups/`` — organizations, users, books, quotas
- ``/admin/export-import`` — bulk export/import

Gunicorn — ``wsgi.py`` reads ``FLASK_ENV`` (defaults to ``production``)::

   export FLASK_ENV=production
   gunicorn -w 4 -b 127.0.0.1:8000 wsgi:app

For a persistent service, run Gunicorn under systemd. Set ``APPLICATION_URL_PREFIX``
if hosting under a subpath (e.g. ``/kalanjiyam``).


Redis
-----

Redis is the Celery broker and result backend::

   sudo apt update
   sudo apt install redis-server
   sudo systemctl enable redis-server.service
   redis-cli ping


Celery
------

Kalanjiyam processes background tasks across dedicated queues:

* `default`: General tasks, notifications, and emails
* `pdf_processing`: CPU/memory-intensive PDF splitting, DOCX segmentation, and book creation
* `ocr`: Interactive single-page OCR, enhanced OCR, and comparison jobs
* `translation`: Page, revision, and batch translation jobs
* `s3_batch`: Bulk PDF/image folder batch OCR ingestion
* `metadata`: Token-budgeted full-document archival metadata extraction
* `search_index`: Incremental OpenSearch indexing and sync jobs
* `low_priority`: Throttled guest OCR/translation and non-urgent maintenance

In Docker Compose, these are split across dedicated worker containers (`kalanjiyam-celery`, `kalanjiyam-celery-pdf`, `kalanjiyam-celery-ocr`, `kalanjiyam-celery-translation`, `kalanjiyam-celery-batch`, `kalanjiyam-celery-metadata`). For bare-metal single-worker deployments::

   celery -A kalanjiyam.tasks worker -Q default,pdf_processing,ocr,translation,low_priority,s3_batch,search_index,metadata --loglevel=INFO --concurrency=2

.. _Celery daemonizing guide: https://docs.celeryq.dev/en/stable/userguide/daemonizing.html


nginx
-----

Put TLS in front of Gunicorn (or the Docker web container on port 5000)::

   server {
       listen 443 ssl http2;
       server_name your-domain.com;

       ssl_certificate     /path/to/cert.pem;
       ssl_certificate_key /path/to/key.pem;

       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }

Redirect HTTP to HTTPS on port 80.


File storage
------------

All uploads (source PDFs, page images, editor images) go through an S3-compatible
storage layer. In the Docker deployment, a bundled `Versity Gateway`_ exposes
``~/kalanjiyam-data/uploads/`` on disk as the ``uploads`` S3 bucket. Files stay as
plain files on the host; the app addresses them through the S3 API. Pre-existing
uploads need no migration.

.. _Versity Gateway: https://github.com/versity/versitygw

To switch to an external S3 backend later (AWS, MinIO, Ceph, SeaweedFS), sync the
objects with ``rclone sync`` or ``aws s3 sync``, then update ``S3_ENDPOINT_URL`` and
the credentials — no code changes required.

Set ``STORAGE_BACKEND=local`` to write files directly under ``FLASK_UPLOAD_FOLDER``
(no gateway required; suitable for simple bare-metal setups).


Docker deployment
-----------------

The recommended way to deploy to production is via ``deploy/prod/deploy.sh``::

   cp .env.example .env
   # Edit .env — fill in SECRET_KEY, POSTGRES_PASSWORD, OCR_SERVICE_URL,
   # S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, and all other required values.
   ./deploy/prod/deploy.sh

The script validates ``.env``, builds the Docker image, runs Alembic migrations,
and starts all services (web, Celery, PostgreSQL, Redis, Versity Gateway).

Other subcommands::

   ./deploy/prod/deploy.sh migrate   # run migrations only
   ./deploy/prod/deploy.sh restart   # restart running containers
   ./deploy/prod/deploy.sh logs      # tail all logs
   ./deploy/prod/deploy.sh stop      # stop and remove containers

Uses ``deploy/prod/docker-compose.yml``. ``OCR_SERVICE_URL`` must point to a host
reachable from inside the containers (not ``localhost``).


Pre-go-live checklist
---------------------

- ``.env`` validated by ``deploy.sh`` (run ``./deploy/prod/deploy.sh`` to check)
- ``FLASK_ENV=production`` and ``APPLICATION_URL_PREFIX=/kalanjiyam`` set
- ``SECRET_KEY``, ``POSTGRES_PASSWORD``, ``KALANJIYAM_BOT_PASSWORD`` all changed from defaults
- OCR service healthy (``curl http://<ocr-host>/health``); ``OCR_SERVICE_API_KEY`` matches
- PostgreSQL (not SQLite) in production; Alembic migrations applied (``./deploy/prod/deploy.sh migrate``)
- ``proof_ocr_comparisons`` table created
- Storage: if ``STORAGE_BACKEND=s3``, ``S3_ACCESS_KEY_ID`` and ``S3_SECRET_ACCESS_KEY`` set; gateway container starts cleanly
- Super admin created via ``./cli.py create-super-admin``
- At least one organization and org admin; ``migrate_multi_tenant.py`` clean or fixes applied
- ``MULTI_TENANT_MODE=true`` and related flags set **after** bootstrap, then app restarted
- Admin UI reachable at ``/admin/platform/`` for super admin
- Celery workers include all required queues (``default``, ``ocr``, ``low_priority``, ``s3_batch``, ``search_index``, ``metadata``)
- OpenSearch service online and indices initialized (``python cli.py search-index init``)
- Static assets built (``make css js``) before Docker image build
- nginx TLS terminating; OCR service ``/admin`` not publicly routable
