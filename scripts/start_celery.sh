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
    
    # Start with conservative settings to prevent memory issues
    # Listen to both default and ocr queues
    celery -A kalanjiyam.tasks worker --loglevel=INFO --concurrency=2 --prefetch-multiplier=1 -Q default,ocr
else
    echo "Running locally..."
    
    # Start Celery worker locally with conservative settings
    # Listen to both default and ocr queues
    celery -A kalanjiyam.tasks worker --loglevel=INFO --concurrency=2 --prefetch-multiplier=1 -Q default,ocr
fi
