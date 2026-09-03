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
        "kalanjiyam.tasks.comparison",
        "kalanjiyam.tasks.s3_batch",
        "kalanjiyam.tasks.search_index",
        "kalanjiyam.tasks.metadata",
        "kalanjiyam.tasks.archival_extract",
    ],
)
# Task Priority Levels (0..9) for fair scheduling and starvation prevention
PRIORITY_INTERACTIVE = 9  # Live proofreading editor single-page OCR & translation
PRIORITY_HIGH = 7  # High priority operations (e.g. single document upload)
PRIORITY_DEFAULT = 5  # Default task priority
PRIORITY_BATCH = 3  # UI Project-level batch OCR / translation
PRIORITY_BACKGROUND = 2  # S3 batch sync, archival metadata, search index
PRIORITY_LOW = 1  # Guest / unauthenticated tasks

app.conf.update(
    # Run all tasks asynchronously by default.
    task_always_eager=False,
    # Force arguments to be plain data by requiring them to be JSON-compatible.
    task_serializer="json",
    # Redis broker settings: visibility timeout & message priority scheduling
    broker_transport_options={
        "visibility_timeout": 10800,  # 3 hours (balanced for large batch PDFs)
        "priority_steps": list(range(10)),
        "sep": ":",
        "queue_order_strategy": "priority",
    },
    task_default_priority=PRIORITY_DEFAULT,
    # Conservative worker configuration to prevent memory issues
    worker_concurrency=1,  # Default fallback concurrency limit
    worker_prefetch_multiplier=1,  # Don't prefetch too many tasks (prevents worker starvation)
    task_acks_late=True,  # Only acknowledge tasks after completion
    worker_max_tasks_per_child=50,  # Restart workers after 50 tasks to prevent memory leaks
    worker_max_memory_per_child=1000000,  # Restart workers if they exceed 1GB memory
    # Task routing to isolate long-running operations and prevent starvation
    task_routes={
        "kalanjiyam.tasks.projects.*": {
            "queue": "pdf_processing",
            "routing_key": "pdf_processing",
        },
        "kalanjiyam.tasks.ocr.*": {"queue": "ocr", "routing_key": "ocr"},
        "kalanjiyam.tasks.translation.*": {
            "queue": "translation",
            "routing_key": "translation",
        },
        "kalanjiyam.tasks.comparison.*": {"queue": "ocr", "routing_key": "ocr"},
        "kalanjiyam.tasks.s3_batch.*": {
            "queue": "s3_batch",
            "routing_key": "s3_batch",
        },
        "kalanjiyam.tasks.search_index.*": {
            "queue": "search_index",
            "routing_key": "search_index",
        },
        # Its own queue: a full-text pass is many minutes of continuous GPU on
        # the same service that answers live OCR. On the `ocr` queue it would
        # starve the editor.
        "kalanjiyam.tasks.archival_extract.*": {
            "queue": "metadata",
            "routing_key": "metadata",
        },
    },
    # Queue configuration
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",
)
