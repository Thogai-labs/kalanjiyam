"""Main entrypoint for Kalanjiyam's background task runner.

The code here shares some utilities with our Flask application, but otherwise
it is an entirely different program that operates outside the Flask application
context.

Use utilities from outside this package with care.

For more information, see our "Background tasks with Celery" doc:

https://kalanjiyam.readthedocs.io/en/latest/
"""

import os
from pathlib import Path

# Load environment variables from .env file for Celery workers
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent / '.env'
    if env_path.exists():
        load_dotenv(env_path)
        print(f"Loaded environment variables from {env_path}")
except ImportError:
    pass

from celery import Celery

# For context on why we use Redis for both the backend and the broker, see the
# "Background tasks with Celery" doc.
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
app = Celery(
    "kalanjiyam-tasks",
    backend=redis_url,
    broker=redis_url,
    include=[
            "kalanjiyam.tasks.projects",
    "kalanjiyam.tasks.ocr",
    "kalanjiyam.tasks.translation",
    ],
)
app.conf.update(
    # Run all tasks asynchronously by default.
    task_always_eager=False,
    # Force arguments to be plain data by requiring them to be JSON-compatible.
    task_serializer="json",
    # Conservative worker configuration to prevent memory issues
    worker_concurrency=2,  # Limit to 2 worker processes instead of default (CPU cores)
    worker_prefetch_multiplier=1,  # Don't prefetch too many tasks
    task_acks_late=True,  # Only acknowledge tasks after completion
    worker_max_tasks_per_child=50,  # Restart workers after 50 tasks to prevent memory leaks
    worker_max_memory_per_child=200000,  # Restart workers if they exceed 200MB memory
    # Task routing for OCR tasks to prevent overwhelming the system
    task_routes={
        'kalanjiyam.tasks.ocr.*': {'queue': 'ocr', 'routing_key': 'ocr'},
    },
    # Queue configuration
    task_default_queue='default',
    task_default_exchange='default',
    task_default_routing_key='default',
)
