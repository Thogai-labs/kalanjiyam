Multi-tenant organizations and admin
======================================

Kalanjiyam can run in **multi-tenant mode**: each **organization** (stored as a
``Group``) owns users, proofing projects (books), storage quota, and OCR credits.
A **super admin** manages the whole platform; **org admins** manage one organization.

This guide covers configuration, CLI bootstrap, the web admin UI, and safe migration
from a single-tenant install.


Roles
-----

+------------------+----------------------------------------------------------+
| Role             | Access                                                   |
+==================+==========================================================+
| ``super_admin``  | Platform-wide: all orgs, quotas, export/import, legacy   |
|                  | Flask-Admin models (users, projects, dictionary, …)    |
+------------------+----------------------------------------------------------+
| ``org_admin``    | One organization: create users, view org books, export   |
+------------------+----------------------------------------------------------+
| ``admin``        | Deprecated alias; treated like ``super_admin`` in code   |
+------------------+----------------------------------------------------------+
| ``p1``, etc.     | Normal proofing users scoped to their organization       |
+------------------+----------------------------------------------------------+

**Super admins** must be created with ``./cli.py create-super-admin`` only. The
web UI cannot grant or change super-admin passwords (use
``./cli.py change-password``).


Environment variables
---------------------

Set these in ``.env`` (see ``.env.example``):

+-------------------------------+---------+----------------------------------------+
| Variable                      | Default | Meaning                                |
+===============================+=========+========================================+
| ``MULTI_TENANT_MODE``         | false   | Enable org-scoped access helpers       |
+-------------------------------+---------+----------------------------------------+
| ``ENFORCE_ORG_ACCESS``        | true    | When multi-tenant mode is on, restrict |
|                               |         | projects/books/APIs by organization    |
+-------------------------------+---------+----------------------------------------+
| ``DEFAULT_PROJECT_REQUIRES_ORG`` | true | New uploads must belong to an org   |
+-------------------------------+---------+----------------------------------------+
| ``ENFORCE_GROUP_ACCESS_FOR_PROJECTS`` | false | Legacy group gate for projects |
|                               |         | (can be true alongside org mode)       |
+-------------------------------+---------+----------------------------------------+
| ``ENFORCE_GROUP_ACCESS_FOR_TEXTS`` | false | Legacy group gate for library texts |
+-------------------------------+---------+----------------------------------------+

Recommended **production** values after bootstrap::

   MULTI_TENANT_MODE=true
   ENFORCE_ORG_ACCESS=true
   DEFAULT_PROJECT_REQUIRES_ORG=true

For local development you can leave ``MULTI_TENANT_MODE=false`` until you have
created organizations and assigned projects.


CLI bootstrap
-------------

After ``alembic upgrade head``:

1. Create the platform super admin (interactive prompts)::

      ./cli.py create-super-admin

2. Create an organization::

      ./cli.py create-organization --name "Default Org" --slug default

3. Assign an org admin (creates the user if needed)::

      ./cli.py assign-org-admin --org default --username orgadmin --email orgadmin@example.com

4. Optional: create org members and quotas::

      ./cli.py create-org-user --org default --username member --email member@example.com
      ./cli.py set-org-quota --org default --storage-mb 10240 --ocr-credits 5000

5. Before turning on ``MULTI_TENANT_MODE=true``, run migration safety checks::

      python scripts/migrate_multi_tenant.py
      python scripts/migrate_multi_tenant.py --apply --default-org-slug default

The script reports users without ``organization_id``, projects not linked to any
group, and legacy ``admin`` roles. With ``--apply`` it can migrate ``admin`` →
``super_admin``, sync ``organization_id`` from ``user_groups``, and attach orphan
projects to the default org slug you pass.

See also :doc:`creating-data-from-the-command-line`.


Web admin UI
------------

Sign in as a super admin, then open **Admin** in the site header (or go directly):

+-------------------------------+-----------------------------------------------+
| URL                           | Purpose                                       |
+===============================+===============================================+
| ``/admin/platform/``        | Platform overview, org stats                    |
+-------------------------------+-----------------------------------------------+
| ``/admin/groups/``            | Create/edit organizations, quotas, membership |
+-------------------------------+-----------------------------------------------+
| ``/admin/export-import``      | Export/import books (super admin)             |
+-------------------------------+-----------------------------------------------+
| ``/admin/org/``               | Org admin dashboard (org admins only)         |
+-------------------------------+-----------------------------------------------+

Org admins use ``/admin/org/`` only — they cannot open ``/admin/platform/`` or
``/admin/groups/`` (those routes are **super_admin** only). Attempts to open
platform URLs redirect org admins to their organization dashboard.

User management (super admin only)
----------------------------------

At ``/admin/user/`` (sidebar **Users**), platform super admins can:

- **Create** users with password, organization, and roles (``p1``, ``p2``,
  ``moderator``, ``org_admin``)
- **Edit** organization membership and roles
- **Delete** users (soft-delete; frees username/email so you can recreate)

**Not available in the web UI:**

- ``super_admin`` — create only via ``./cli.py create-super-admin`` (at most **one**
  account)
- Deleting the only super admin or the ``kalanjiyam-bot`` account

Org admins manage their own users at ``/admin/org/`` (not ``/admin/user/``).

Public books (``/books/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, multi-tenant mode hides books from guests and from other organizations.
Org admins can mark a book **public** on ``/admin/org/`` (**Make public**). That
book then appears on ``/books/`` for everyone (including anonymous visitors) once it
has OCR content. Proofing, page images, and translations work the same; only the
access gate changes. Books stay owned by the organization for quotas and admin.

Super admins can toggle the same flag under **Organizations → manage** or on
``/admin/project/`` (column **Public on /books/**).

Run the migration that adds ``proof_projects.is_publicly_viewable`` before using
this feature::

   alembic upgrade head

Flask-Admin CRUD for **users** and **projects** remains at ``/admin/`` for platform
super admins. Dictionary metadata, genres, sponsorships, and contributor bios are
managed via seed scripts or the database, not the admin UI.


Phased rollout
--------------

1. Deploy code and run migrations; keep ``MULTI_TENANT_MODE=false``.
2. Create super admin and at least one organization via CLI.
3. Run ``migrate_multi_tenant.py`` (dry run, then ``--apply``).
4. Assign remaining projects and users in **Admin → Organizations** if needed.
5. Set ``MULTI_TENANT_MODE=true`` (and related flags) and restart the app.
6. Verify proofing, ``/books/``, and org-admin flows.

Export format **2.0** includes ``organization_slug`` and per-project assets in bulk
ZIPs; see export/import in the admin UI.


Quotas
------

Organizations track **storage used** (uploads) and **OCR credits used**. Set limits
with ``./cli.py set-org-quota`` or when editing an organization in the admin UI.
Uploads and OCR jobs are blocked when a quota is exceeded.
