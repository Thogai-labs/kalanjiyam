Quickstart
==========

All of Kalanjiyam's major commands are in `Makefile`.


Running the development server
------------------------------

After you've cloned the repo, you can bring up a minimal setup by running the
following command::

    make install

Next, create an admin user. For multi-tenant development, prefer a super admin::

    ./cli.py create-super-admin

Legacy single-tenant setup::

    ./cli.py create-user
    ./cli.py add-role --username <username> --role admin

See :doc:`multi-tenant` for organizations, quotas, and the ``/admin/platform/`` UI.

Then bring up the development server::

    make devserver

Then go to `localhost:5000` to see the local application.

Some parts of Kalanjiyam, such as PDF parsing and project uploads, need to run tasks in the background.
To add support for these in your local setup, run the following::

    ./scripts/install_osx_dependencies.sh
    make redis
    make celery

Roughly, Tailwind generates a new CSS file whenever it detects certain changes
to Kalanjiyam's HTML files. For more details, see the `Tailwind docs`_.

.. _Tailwind docs: https://tailwindcss.com/docs/


Linting and testing
-------------------

For linting, you can use::

    # Lints both JS and Python.
    # - To lint just Python, run `black .`
    # - To lint just JS, run `make js-lint`.
    make lint

To run unit tests, you can simply run::

    make test

And to check test coverage, run::

    make coverage


Database migrations
-------------------

Database migrations are complex. If you're pulling an upstream change that
contains a database schema change, run this command to upgrade your local
database::

    alembic upgrade head

See :doc:`managing-the-database` to learn more about how to work with the test
database and safely make schema changes.


Documentation
-------------

Finally, you can generate these docs with::

    make docs

Then you can view the output by opening `_build/index.html`.
