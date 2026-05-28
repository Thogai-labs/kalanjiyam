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
