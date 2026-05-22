Production deployment
=====================

Kalanjiyam runs as two services: the **main app** (this repo) and the **OCR service**
(`kalanjiyam-ocr-service <https://github.com/Thogai-labs/kalanjiyam-ocr-service>`_).
The app calls OCR over HTTP; GPU models live only on the OCR host.

Architecture
------------

::

   Browser → nginx → Gunicorn (Kalanjiyam)
                       ├── PostgreSQL
                       ├── Redis → Celery (queues: default, ocr)
                       └── HTTP → OCR service (:5001)

OCR service (deploy first)
--------------------------

On a CPU or GPU machine::

   git clone <ocr-service-repo> kalanjiyam-ocr-service
   cd kalanjiyam-ocr-service
   cp .env.example .env
   pip install -r requirements.txt

Verify: ``curl http://<ocr-host>:5001/health``

Restrict ``/admin`` to internal networks or VPN.

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
- ``FLASK_UPLOAD_FOLDER`` — absolute path
- ``REDIS_URL`` — e.g. ``redis://127.0.0.1:6379/0``
- ``OCR_BACKEND=remote``
- ``OCR_SERVICE_URL`` — e.g. ``http://<ocr-host>:5001``
- ``OCR_SERVICE_API_KEY`` — same as OCR service ``API_KEY``
- ``KALANJIYAM_BOT_PASSWORD``
- ``SENTRY_DSN`` — required for ``ProductionConfig``

Database::

   python scripts/add_ocr_comparison_table.py

Gunicorn — ``wsgi.py`` reads ``FLASK_ENV`` (defaults to ``production``)::

   export FLASK_ENV=production
   gunicorn -w 4 -b 127.0.0.1:8000 wsgi:app

See ``deploy/siddhasagaram-config.md`` for nginx samples.

Celery
------

Batch OCR and OCR comparison use the **ocr** queue::

   celery -A kalanjiyam.tasks worker -Q default,ocr --loglevel=INFO --concurrency=2

Docker deployment
-----------------

::

   cp .env.example .env
   make docker-build
   KALANJIYAM_DEPLOYMENT_ENV=prod make docker-start

Uses ``deploy/prod/docker-compose.yml``. Set ``OCR_SERVICE_URL`` to a host reachable
from containers.

Pre-go-live checklist
---------------------

- OCR service healthy; API key matches Kalanjiyam ``.env``
- PostgreSQL (not SQLite) in production
- ``proof_ocr_comparisons`` table created
- ``FLASK_ENV=production`` for Gunicorn / Docker web
- Celery worker includes ``ocr`` queue
- Static assets built (``make css js``)
- nginx TLS; OCR admin not public
