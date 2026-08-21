Installation
============

This guide will show you how to install Kalanjiyam and its dependencies. By the end
of this guide, you'll have a working devserver that contains the same texts and
data as our production server.


Before you begin
----------------

We recommend using the latest version of Python. At a mininum, Kalanjiyam requires
Python 3.10.

We also recommend having a recent version of `npm`. We use `npm` to fetch our
Tailwind watcher, which generates CSS based on changes to our HTML files. (We
currently use npm version 8.5.2.)

We tested this setup on a MacBook running macOS 12.3 Monterey, but we think it
will work on most Unix machines. If you have installation problems that seem
specific to the Kalanjiyam project, please file an issue on our repo or let us know
on our Discord server.


Code dependencies
-----------------

Start by downloading Kalanjiyam's project code from GitHub::

    $ git clone git@github.com/AnaadiAI/kalanjiyam.git

You can install all dependencies with a simple `make` call::

    $ make install

This command will install Kalanjiyam's Python and JavaScript dependencies, create a
new database, and add a sample dictionary and a few sample texts to that database.

If the install command succeeds, you can bring up a basic version of Kalanjiyam by
running the following commands::

    # Enter the virtual environment.
    # (If you're using a non-Bash shell, you might need to use a different
    # command. Search "virtualenv $YOUR_SHELL_HERE" for details.
    $ source env/bin/activate

    # Then, start the development server.
    $ make devserver


Environment setup
-----------------

Behind the scenes, we configure Kalanjiyam by setting various environment
variables, which is the standard practice for Flask applications. To organize
all of these settings, we keep environment variables in a `.env` file in the
project root.

`make install` creates an `.env` file for you. If you ever need to add more
variables in the future, just edit `.env`. All Kalanjiyam code will refer to
`.env` by default.

If you need access to these environment variables as part of some other script,
you can run the following command for shell scripts::
    
    $ source .env

Or the following commands for Python scripts:

.. code-block:: python

    from dotenv import load_dotenv
    load_dotenv(".env")


Docker setup (beta)
-------------------

This feature is still under development and may change. You can alternatively
run a local development environment using Docker by running:

    make docker-start


Data dependencies
-----------------

The `kalanjiyam` repo doesn't contain any of the texts, dictionaries, or parse data
that we serve on our library. To install this data, we run different **seed
scripts** that fetch the data we need from the Internet.

The `make install` script runs many of these seed scripts for you
automatically. Specifically, it runs `make db-seed-basic`, which installs a
small amount of sample data that you can experiment with.

If you want to install all texts, dictionaries, and parse data, you can run the
following command::

    make db-seed-all

This command fetches multiple large data sources from multiple sites, so it
might take several minutes to run.

.. note::

    Generally, our seed scripts cache any downloaded data in a cache directory
    at `data/download-cache`. We define this cache so that you can quickly
    rebuild the database if you need to install from scratch.


Service dependencies
--------------------

Kalanjiyam has several important service dependencies. These dependencies are
required only for specific features on Kalanjiyam. For general usage, you can skip
these.


Background Task Services
^^^^^^^^^^^^^^^^^^^^^^^^

Kalanjiyam uses Celery for background task processing, which is required for features
like:

- Project uploads (PDF processing and page splitting)
- OCR (Optical Character Recognition) processing
- Email sending
- Batch operations

To enable these features, you need to start both Redis and Celery:

1. Start Redis (message broker and backend)::

    make redis

2. Start Celery worker (in a separate terminal)::

    make celery

.. note::
    Without these services running, project uploads will appear to complete but
    the actual PDF processing will not occur. The uploaded PDF will be saved but
    not split into pages or added to the database.


Remote OCR service
^^^^^^^^^^^^^^^^^^

Kalanjiyam calls a separate OCR microservice over HTTP. Set ``OCR_SERVICE_URL``
and ``OCR_SERVICE_API_KEY`` in ``.env`` to point at a running
`kalanjiyam-ocr-service <https://github.com/Thogai-labs/kalanjiyam-ocr-service>`_
instance.

Without the OCR service, OCR features (batch OCR, inline editor OCR) will be
unavailable but the rest of the app runs normally. For quick local testing you
can run the OCR service on ``http://localhost:8000`` and set::

    OCR_BACKEND=remote
    OCR_SERVICE_URL=http://localhost:8000
    OCR_SERVICE_API_KEY=<key from ocr-service .env>


reCAPTCHA
^^^^^^^^^

We use reCAPTCHA v2 ("I'm not a robot" checkbox) as an anti-spam measure when users create an account or reset passwords.

To set up reCAPTCHA credentials for local authentication testing:

1. Create a **reCAPTCHA v2 Checkbox** key pair on the `Google reCAPTCHA Console`_.
2. Set your keys in the ``.env`` file::

    RECAPTCHA_PUBLIC_KEY=your_site_key
    RECAPTCHA_PRIVATE_KEY=your_secret_key

.. _`Google reCAPTCHA Console`: https://www.google.com/recaptcha/admin


Sentry
^^^^^^

We use Sentry to log server errors when we run in production.

You should set up Sentry only if you want to emulate our production logging
setup. To do so, refer to the documentation here:

- `How to set up Sentry`_

.. _`How to set up Sentry`: https://docs.sentry.io/platforms/python/
