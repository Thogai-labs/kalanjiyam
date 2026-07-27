#!/bin/bash
# Production server: Gunicorn serving the Kalanjiyam WSGI app.
set -e

WORKERS=${GUNICORN_WORKERS:-4}
BIND=${GUNICORN_BIND:-0.0.0.0:5000}
TIMEOUT=${GUNICORN_TIMEOUT:-120}

echo "Starting Kalanjiyam (gunicorn, workers=${WORKERS}, bind=${BIND})"
exec gunicorn \
    --bind "${BIND}" \
    --workers "${WORKERS}" \
    --timeout "${TIMEOUT}" \
    --access-logfile - \
    --error-logfile - \
    wsgi:app
