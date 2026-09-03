Creating data from the command line
===================================

Kalanjiyam exposes a CLI (``./cli.py``) for administrative tasks. This interface
lets you quickly create users, organizations, and sample projects on a development
or production server.

For roles, environment variables, the web admin UI, and production rollout, see
:doc:`multi-tenant`.


Multi-tenant bootstrap
----------------------

Run database migrations, then create the platform super admin (CLI only; **only one**
allowed)::

    alembic upgrade head
    ./cli.py create-super-admin

Additional users and org admins can be created in the web UI at ``/admin/user/``
(super admin) or ``/admin/org/`` (org admin).

Create an organization and assign its org admin::

    ./cli.py create-organization --name "Example Org" --slug example-org
    ./cli.py assign-org-admin --org example-org --username orgadmin --email orgadmin@example.com

Create an organization member::

    ./cli.py create-org-user --org example-org --username member --email member@example.com

Set organization quotas::

    ./cli.py set-org-quota --org example-org --storage-mb 10240 --ocr-credits 5000

Before enabling ``MULTI_TENANT_MODE=true`` in ``.env``, run migration safety checks::

    python scripts/migrate_multi_tenant.py
    python scripts/migrate_multi_tenant.py --apply --default-org-slug example-org


Web admin (alternative to some CLI steps)
-----------------------------------------

After signing in as super admin, use the **Admin** link in the header:

- **Platform** — ``/admin/platform/``
- **Organizations** — ``/admin/groups/`` (create orgs, assign users and books, edit quotas)
- **Export / Import** — ``/admin/export-import``

Org admins use ``/admin/org/`` to create users and export books for their organization.


Legacy commands
---------------

Create a new user (no organization assignment)::

    ./cli.py create-user

Make that user an administrator (deprecated; prefer ``create-super-admin``)::

    ./cli.py add-role --username <username> --role admin

``add-role`` cannot grant ``super_admin``; use ``create-super-admin`` instead.

Change any user's password. Super-admin passwords **must** use the CLI (blocked on
the website)::

    ./cli.py change-password --username <username>

Create a fake proofing project::

    ./cli.py create-project --title <title> --pdf-path <path-to-your-pdf-file>


Batch ingestion & bulk import
-----------------------------

Bulk ingest PDF or image directories into proofing projects with OCR::

    ./cli.py batch-ocr --s3-uri s3://bucket/folder/ --org example-org --lang eng
    ./cli.py batch-list
    ./cli.py batch-status --job-id 1
    ./cli.py batch-cancel --job-id 1
    ./cli.py batch-retry --job-id 1

Import external JSONL OCR archives directly with associated PDFs::

    ./cli.py import-jsonl --jsonl-uri s3://bucket/data.jsonl --pdf-uri s3://bucket/scans/ --org example-org

Clean up old uploaded source documents older than N days::

    ./cli.py cleanup-uploads --days 30 --force


Archival metadata extraction
----------------------------

Extract structured archival metadata descriptions across projects::

    ./cli.py metadata-extract --project project-slug --local
    ./cli.py metadata-extract --all
    ./cli.py metadata-status --project project-slug
    ./cli.py metadata-runs --limit 20


OpenSearch full-text indexing
-----------------------------

Manage OpenSearch search indices for proofing and library texts::

    ./cli.py search-index init
    ./cli.py search-index status
    ./cli.py search-index sync --incremental
    ./cli.py search-index rebuild --yes
    ./cli.py search-index drop --yes


Storage migration & reconciliation
----------------------------------

Migrate filesystem uploads to S3 or reconcile storage state with PostgreSQL::

    ./cli.py storage-stats
    ./cli.py migrate-to-s3 --batch-size 50
    ./cli.py reconcile-storage
