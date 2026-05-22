Running in production
=====================

See :doc:`production-deploy` for the full guide (OCR service split, Gunicorn, Celery ``ocr`` queue, Docker, and checklist).

Quick reference
---------------

**Gunicorn**

::

    export FLASK_ENV=production
    gunicorn -w 4 -b 127.0.0.1:8000 wsgi:app

**Celery** (must include the ``ocr`` queue for batch OCR and OCR comparison)::

    celery -A kalanjiyam.tasks worker -Q default,ocr --loglevel=INFO

**Redis**

We use Redis as a message broker and backend for Celery. For setup, see steps 1 and 2 in this `tutorial`_.

::

    sudo apt update
    sudo apt install redis-server
    sudo systemctl enable redis-server.service
    redis-cli ping

.. _tutorial: https://www.digitalocean.com/community/tutorials/how-to-install-and-secure-redis-on-ubuntu-20-04

**Celery systemd**

For a production Celery service, see the `Celery daemonizing guide`_.

.. _guide: https://docs.celeryq.dev/en/stable/userguide/daemonizing.html
