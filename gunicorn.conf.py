"""Gunicorn configuration file for Kalanjiyam."""

import os


def child_exit(server, worker):
    """Clean up Prometheus multi-process metrics on worker exit.

    When running Gunicorn in multi-worker mode with PROMETHEUS_MULTIPROC_DIR set,
    this hook ensures that dead worker metric files/gauges are marked as dead and cleaned up.
    """
    multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR") or os.environ.get(
        "prometheus_multiproc_dir"
    )
    if multiproc_dir:
        try:
            from prometheus_client import multiprocess

            multiprocess.mark_process_dead(worker.pid)
        except Exception:
            pass
