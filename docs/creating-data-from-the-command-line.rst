Creating data from the command line
===================================

Kalanjiyam exposes a basic CLI for common administrative tasks. This interface lets
you quickly create objects to interact with on the development server.

Multi-tenant bootstrap (production)
-----------------------------------

Run database migrations, then create the platform super admin (CLI only)::

    alembic upgrade head
    ./cli.py create-super-admin

Create an organization and assign its org admin::

    ./cli.py create-organization --name "Example Org" --slug example-org
    ./cli.py assign-org-admin --org example-org --username orgadmin --email orgadmin@example.com

Create an organization member::

    ./cli.py create-org-user --org example-org --username member --email member@example.com

Set organization quotas::

    ./cli.py set-org-quota --org example-org --storage-mb 10240 --ocr-credits 5000

Before enabling ``MULTI_TENANT_MODE=true``, run migration safety checks::

    python scripts/migrate_multi_tenant.py
    python scripts/migrate_multi_tenant.py --apply --default-org-slug example-org

Legacy commands
---------------

Create a new user (no org assignment)::

    ./cli.py create-user

Make that user an administrator (deprecated; prefer ``create-super-admin``)::

    ./cli.py add-role --username <username> --role admin

Change any user's password (super-admin passwords must use this CLI)::

    ./cli.py change-password --username <username>

Create a fake proofing project::

    ./cli.py create-project --title <title> --pdf-path <path-to-your-pdf-file>
