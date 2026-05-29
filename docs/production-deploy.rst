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
- ``FLASK_UPLOAD_FOLDER`` — absolute path
- ``REDIS_URL`` — e.g. ``redis://127.0.0.1:6379/0``
- ``OCR_BACKEND=remote``
- ``OCR_SERVICE_URL`` — e.g. ``http://<ocr-host>:5001``
- ``OCR_SERVICE_API_KEY`` — same as OCR service ``API_KEY``
- ``KALANJIYAM_BOT_PASSWORD``
- ``SENTRY_DSN`` — required for ``ProductionConfig``

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

Batch OCR and OCR comparison use the **ocr** queue::

   celery -A kalanjiyam.tasks worker -Q default,ocr --loglevel=INFO --concurrency=2

For a production Celery service, see the `Celery daemonizing guide`_.

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
- Alembic migrations applied
- Super admin created via ``./cli.py create-super-admin``
- At least one organization and org admin; ``migrate_multi_tenant.py`` clean or fixes applied
- ``MULTI_TENANT_MODE=true`` and related flags set **after** bootstrap, then app restarted
- Admin UI reachable at ``/admin/platform/`` for super admin
- ``FLASK_ENV=production`` for Gunicorn / Docker web
- Celery worker includes ``ocr`` queue
- Static assets built (``make css js``)
- nginx TLS; OCR admin not public
