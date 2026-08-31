import logging

from celery import states


class TaskStatus:
    """Helper class to track progress on a task.

    - For Celery tasks, use CeleryTaskStatus.
    - For local usage (unit tests, CLI, ...), use a LocalTaskStatus instead.
    """

    def progress(self, current: int, total: int, **extra):
        """Update the task's progress.

        :param current: progress numerator
        :param total: progress denominator
        """
        raise NotImplementedError

    def success(self, num_pages: int, slug: str, **extra):
        """Mark the task as a success.

        # FIXME(arun): make this API more generic.
        """
        raise NotImplementedError

    def failure(self, message: str):
        """Mark the task as failed."""
        raise NotImplementedError


class CeleryTaskStatus(TaskStatus):
    """Helper class to track progress on a Celery task."""

    def __init__(self, task):
        self.task = task

    def progress(self, current: int, total: int, **extra):
        """Update the task's progress.

        :param current: progress numerator
        :param total: progress denominator
        """
        # Celery doesn't have a "PROGRESS" state, so just use a hard-coded string.
        meta = {"current": current, "total": total}
        meta.update(extra)
        self.task.update_state(state="PROGRESS", meta=meta)

    def success(self, num_pages: int, slug: str, **extra):
        """Mark the task as a success."""
        meta = {"current": num_pages, "total": num_pages, "slug": slug}
        meta.update(extra)
        self.task.update_state(state=states.SUCCESS, meta=meta)

    def failure(self, message: str):
        """Mark the task as failed."""
        self.task.update_state(state=states.FAILURE, meta={"message": message})


class LocalTaskStatus(TaskStatus):
    """Helper class to track progress on a task running locally."""

    def progress(self, current: int, total: int, **extra):
        logging.info(f"{current} / {total} complete")

    def success(self, num_pages: int, slug: str, **extra):
        logging.info(f"Succeeded. Project is at {slug}.")

    def failure(self, message: str):
        logging.info(f"Failed. ({message})")
