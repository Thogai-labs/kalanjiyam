Batch OCR Task Tracking
=======================

This document describes the Redis-based task tracking system for batch OCR operations in Kalanjiyam. This feature allows users to navigate away from the OCR progress page and return to see the current progress, providing a better user experience for long-running OCR operations.

Overview
--------

Batch OCR operations can take a significant amount of time, especially for large documents with hundreds of pages. The task tracking system ensures that users can:

- Start a batch OCR operation
- Navigate away from the progress page
- Return later to see the current progress
- Resume monitoring without losing track of the operation

The system uses Redis to store task information temporarily, providing a stateless solution that doesn't require database schema changes.

Architecture
-----------

The task tracking system consists of several components:

1. **Redis Storage**: Task information is stored in Redis with a 24-hour expiration
2. **Task Detection**: The system checks for ongoing tasks when users visit the batch OCR page
3. **Progress Restoration**: Active tasks are restored and progress is displayed
4. **Automatic Cleanup**: Completed or failed tasks are automatically removed from Redis

Redis Key Format
---------------

Tasks are stored in Redis using the following format:

- **Key**: ``ocr_task:{project_slug}``
- **Value**: JSON object containing:
  - ``task_id``: The Celery task ID
  - ``engine``: OCR engine being used (google/tesseract)
  - ``started_at``: ISO timestamp when the task was started
  - ``project_slug``: The project slug for reference
- **Expiration**: 24 hours (86400 seconds)

Example Redis entry:
::

    Key: ocr_task:my-project
    Value: {
        "task_id": "abc123-def456-ghi789",
        "engine": "google",
        "started_at": "2024-01-15T10:30:00.000000",
        "project_slug": "my-project"
    }

Implementation Details
---------------------

Task Storage
~~~~~~~~~~~

When a user starts a batch OCR operation, task information is stored in Redis:

.. code-block:: python

    # Store task info in Redis with expiration (24 hours)
    task_info = {
        'task_id': task.id,
        'engine': engine,
        'started_at': datetime.utcnow().isoformat(),
        'project_slug': slug
    }
    redis_client.setex(task_key, 86400, json.dumps(task_info))

Task Detection
~~~~~~~~~~~~~

When a user visits the batch OCR page, the system checks for ongoing tasks:

.. code-block:: python

    # Check if there's an ongoing OCR task using Redis
    task_key = f"ocr_task:{slug}"
    task_info = redis_client.get(task_key)
    
    if task_info:
        task_data = json.loads(task_info)
        task_id = task_data.get('task_id')
        
        # Try to restore the task to check if it's still active
        r = GroupResult.restore(task_id, app=celery_app)
        if r and r.state in ['PENDING', 'PROGRESS']:
            # Show progress page instead of OCR form
            return render_template("proofing/projects/batch-ocr-post.html", ...)

Automatic Cleanup
~~~~~~~~~~~~~~~~

Tasks are automatically removed from Redis when they complete or fail:

.. code-block:: python

    def _clear_ocr_task_from_redis(task_id):
        """Clear OCR task from Redis when it completes or fails."""
        try:
            # Find the task key by scanning Redis keys
            for key in redis_client.scan_iter(match="ocr_task:*"):
                task_info = redis_client.get(key)
                if task_info:
                    task_data = json.loads(task_info)
                    if task_data.get('task_id') == task_id:
                        redis_client.delete(key)
                        break
        except Exception as e:
            LOG.warning(f"Error clearing OCR task from Redis: {e}")

User Experience Flow
-------------------

1. **Start OCR**: User clicks "Run OCR" button
   - Task is created and stored in Redis
   - User is redirected to progress page

2. **Navigate Away**: User can navigate to other pages
   - Task continues running in background
   - Task information remains in Redis

3. **Return to OCR**: User visits batch OCR page again
   - System detects ongoing task in Redis
   - Shows progress page instead of OCR form
   - Displays current progress

4. **Task Completion**: OCR operation finishes
   - Task is automatically removed from Redis
   - User sees completion status

5. **Server Restart**: If server restarts
   - Redis data is preserved (if Redis is persistent)
   - If Redis data is lost, user can start new OCR
   - Clean slate approach is actually better UX

Error Handling
-------------

The system includes robust error handling for various scenarios:

Redis Connection Issues
~~~~~~~~~~~~~~~~~~~~~~

If Redis is unavailable, the system gracefully falls back to normal behavior:

.. code-block:: python

    try:
        task_info = redis_client.get(task_key)
        # Process task info
    except Exception as e:
        LOG.warning(f"Error checking OCR task for {slug}: {e}")
        # Fall back to normal behavior

Invalid Task Data
~~~~~~~~~~~~~~~~

If task data in Redis is corrupted or invalid:

.. code-block:: python

    try:
        task_data = json.loads(task_info)
        task_id = task_data.get('task_id')
    except Exception:
        # Remove invalid data and continue
        redis_client.delete(task_key)

Task Not Found
~~~~~~~~~~~~~

If a task ID in Redis no longer exists in Celery:

.. code-block:: python

    try:
        r = GroupResult.restore(task_id, app=celery_app)
        if r and r.state in ['PENDING', 'PROGRESS']:
            # Task is active
        else:
            # Task is complete/failed, remove from Redis
            redis_client.delete(task_key)
    except Exception:
        # Task not found, remove from Redis
        redis_client.delete(task_key)

Configuration
------------

Redis Connection
~~~~~~~~~~~~~~~

The Redis client is configured using environment variables:

.. code-block:: python

    redis_client = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )

Environment Variables
~~~~~~~~~~~~~~~~~~~~

- ``REDIS_URL``: Redis connection string (default: redis://localhost:6379/0)

Task Expiration
~~~~~~~~~~~~~~

Tasks automatically expire after 24 hours (86400 seconds) to prevent Redis from filling up with stale data.

Monitoring and Debugging
-----------------------

Logging
~~~~~~~

The system logs various events for monitoring and debugging:

- Task storage: Debug level
- Task detection: Info level
- Task cleanup: Debug level
- Errors: Warning level

Redis Monitoring
~~~~~~~~~~~~~~~

You can monitor Redis to see active tasks:

.. code-block:: bash

    # List all OCR tasks
    redis-cli keys "ocr_task:*"
    
    # Get details of a specific task
    redis-cli get "ocr_task:my-project"
    
    # Check Redis memory usage
    redis-cli info memory

Benefits
--------

1. **Better UX**: Users can navigate away and return to see progress
2. **Stateless Design**: No database schema changes required
3. **Automatic Cleanup**: Tasks are automatically removed when complete
4. **Error Resilience**: Graceful handling of Redis issues
5. **Server Restart Friendly**: Clean slate after restarts
6. **Scalable**: Uses existing Redis infrastructure

Limitations
-----------

1. **Redis Dependency**: Requires Redis to be running
2. **Temporary Storage**: Task data is lost if Redis restarts (unless persistent)
3. **Memory Usage**: Active tasks consume Redis memory
4. **Network Dependency**: Requires network connectivity to Redis

Future Enhancements
------------------

Potential improvements to consider:

1. **Database Persistence**: Store task metadata in database for longer-term tracking
2. **Task History**: Keep completed task history for audit purposes
3. **User Notifications**: Send notifications when tasks complete
4. **Task Cancellation**: Allow users to cancel running tasks
5. **Progress Estimation**: Provide time estimates for remaining work
6. **Batch Operations**: Support for multiple concurrent OCR tasks per user

Related Documentation
--------------------

- :doc:`background-tasks-with-celery` - General Celery setup and usage
- :doc:`architecture` - Overall system architecture
- :doc:`production-deploy` - Production deployment