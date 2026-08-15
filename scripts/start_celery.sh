#!/bin/bash

# Start the Kalanjiyam Celery worker
# This script starts the Celery worker for background tasks

set -e

echo "Starting Kalanjiyam Celery worker with conservative settings..."

# Check if we're in a Docker container
if [ -f /.dockerenv ]; then
    echo "Running in Docker container..."
    
    # Switch to python venv and start Celery worker
    . /venv/bin/activate
    export PATH=$PATH:/venv/bin/
    
    # Start with conservative settings to prevent memory issues.
    # This is the *only* worker, so it must list every queue `task_routes` in
    # kalanjiyam/tasks/__init__.py routes to. A routed queue with no consumer
    # does not error -- the task enqueues and then sits in Redis forever.
    celery -A kalanjiyam.tasks worker --loglevel=INFO --concurrency=1 --prefetch-multiplier=1 -Q default,ocr,low_priority,s3_batch,search_index,metadata
else
    echo "Running locally..."
    
    # Start Celery worker locally with conservative settings.
    # Same rule as above: every routed queue, or its tasks never run.
    celery -A kalanjiyam.tasks worker --loglevel=INFO --concurrency=1 --prefetch-multiplier=1 -Q default,ocr,low_priority,s3_batch,search_index,metadata
fi
