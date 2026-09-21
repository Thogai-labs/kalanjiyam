#!/bin/bash
# Production server: Gunicorn serving the Kalanjiyam WSGI app.
set -e

WORKERS=${GUNICORN_WORKERS:-4}
THREADS=${GUNICORN_THREADS:-8}
BIND=${GUNICORN_BIND:-0.0.0.0:5000}
TIMEOUT=${GUNICORN_TIMEOUT:-120}

CONFIG_ARGS=()
if [ -f "gunicorn.conf.py" ]; then
    CONFIG_ARGS+=("-c" "gunicorn.conf.py")
fi

echo "Starting Kalanjiyam (gunicorn, workers=${WORKERS}, threads=${THREADS}, bind=${BIND})"
exec gunicorn \
    --bind "${BIND}" \
    --workers "${WORKERS}" \
    --worker-class gthread \
    --threads "${THREADS}" \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --timeout "${TIMEOUT}" \
    --worker-tmp-dir /dev/shm \
    --access-logfile - \
    --error-logfile - \
    "${CONFIG_ARGS[@]}" \
    wsgi:app
