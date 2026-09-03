"""Prometheus metrics exporter and HTTP request tracking middleware for Kalanjiyam."""

import logging
import os
import time

import prometheus_client
from flask import Flask, Response, g, request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Histogram,
    multiprocess,
)

LOG = logging.getLogger(__name__)

# Request count partitioned by method, route/endpoint, and HTTP status code
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests partitioned by method, route/endpoint, and HTTP status code.",
    ["method", "endpoint", "status"],
)

# Request latency histogram with default buckets partitioned by method, route/endpoint, and HTTP status code
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds partitioned by method, route/endpoint, and HTTP status code.",
    ["method", "endpoint", "status"],
)


def get_multiproc_dir() -> str | None:
    """Return the configured Prometheus multiprocess directory, if set."""
    return os.environ.get("PROMETHEUS_MULTIPROC_DIR") or os.environ.get("prometheus_multiproc_dir")


def _get_request_endpoint() -> str:
    """Extract normalized route pattern or endpoint name to avoid high cardinality."""
    try:
        if request.url_rule is not None:
            return request.url_rule.rule
        if request.endpoint:
            return request.endpoint
    except Exception:
        pass
    return "unmatched"


def generate_metrics_response() -> tuple[bytes, str]:
    """Collect and return Prometheus formatted metrics bytes and content type.

    Supports both single-process and multi-process server modes (via PROMETHEUS_MULTIPROC_DIR).
    """
    multiproc_dir = get_multiproc_dir()
    if multiproc_dir and os.path.isdir(multiproc_dir):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        data = prometheus_client.generate_latest(registry)
        return data, CONTENT_TYPE_LATEST

    # Default single-process collector registry (includes ProcessCollector, GCCollector, etc.)
    data = prometheus_client.generate_latest(REGISTRY)
    return data, CONTENT_TYPE_LATEST


def metrics_endpoint():
    """HTTP view function returning Prometheus exposition text format."""
    try:
        data, content_type = generate_metrics_response()
        return Response(data, content_type=content_type)
    except Exception as ex:
        LOG.exception("Failed to collect Prometheus metrics: %s", ex)
        return Response(
            f"# Error generating Prometheus metrics: {ex}\n",
            status=500,
            content_type="text/plain; charset=utf-8",
        )


def init_prometheus(app: Flask) -> None:
    """Attach Prometheus request tracking middleware and register /metrics endpoint on Flask app."""
    # Ensure multiprocess directory exists if configured
    multiproc_dir = get_multiproc_dir()
    if multiproc_dir:
        try:
            os.makedirs(multiproc_dir, exist_ok=True)
        except Exception as ex:
            LOG.warning("Could not create PROMETHEUS_MULTIPROC_DIR directory '%s': %s", multiproc_dir, ex)

    @app.before_request
    def _prometheus_before_request():
        # High-precision timer for request tracking
        g._prometheus_start_time = time.perf_counter()

    @app.after_request
    def _prometheus_after_request(response):
        if hasattr(g, "_prometheus_start_time"):
            duration = time.perf_counter() - g._prometheus_start_time
            endpoint = _get_request_endpoint()
            status = str(response.status_code)
            method = request.method

            HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, endpoint=endpoint, status=status).observe(duration)
            g._prometheus_recorded = True
        return response

    @app.teardown_request
    def _prometheus_teardown_request(exception=None):
        if exception is not None and not getattr(g, "_prometheus_recorded", False):
            duration = (
                time.perf_counter() - g._prometheus_start_time
                if hasattr(g, "_prometheus_start_time")
                else 0.0
            )
            endpoint = _get_request_endpoint()
            method = request.method if request else "UNKNOWN"
            status = "500"

            HTTP_REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(method=method, endpoint=endpoint, status=status).observe(duration)
            g._prometheus_recorded = True

    # Register root /metrics endpoint
    app.add_url_rule(
        "/metrics",
        endpoint="prometheus_metrics",
        view_func=metrics_endpoint,
        methods=["GET"],
    )

    # Register prefixed /metrics endpoint if APPLICATION_URL_PREFIX is configured
    url_prefix = app.config.get("APPLICATION_URL_PREFIX", "")
    if url_prefix and url_prefix.strip("/"):
        prefixed_route = f"/{url_prefix.strip('/')}/metrics"
        if prefixed_route != "/metrics":
            app.add_url_rule(
                prefixed_route,
                endpoint="prometheus_metrics_prefixed",
                view_func=metrics_endpoint,
                methods=["GET"],
            )
