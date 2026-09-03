"""Unit tests for Prometheus metrics exporter and HTTP request tracking middleware."""

import importlib.util
import os
import shutil
import tempfile
from unittest.mock import MagicMock

from flask import Flask
from prometheus_client import CollectorRegistry, multiprocess

from kalanjiyam.utils.prometheus import (
    generate_metrics_response,
    get_multiproc_dir,
    init_prometheus,
)


def test_metrics_endpoint_http_200(client):
    """Test that GET /metrics returns HTTP 200 with Prometheus text format."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.content_type

    text = response.get_data(as_text=True)
    assert "# HELP" in text or "# TYPE" in text


def test_process_runtime_metrics_exposed(client):
    """Test that default process runtime metrics (memory, CPU, FDs, GC) are exposed."""
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.get_data(as_text=True)

    # Process metrics
    assert "process_cpu_seconds_total" in text
    assert "process_resident_memory_bytes" in text or "process_virtual_memory_bytes" in text
    assert "process_open_fds" in text or "process_max_fds" in text

    # Garbage collection / runtime metrics
    assert "python_gc_collections_total" in text or "python_gc_objects_collected_total" in text
    assert "python_info" in text


def test_http_request_tracking_middleware(client):
    """Test that HTTP request count and latency histograms are tracked."""
    # Hit an endpoint
    res = client.get("/")
    assert res.status_code == 200

    metrics_res = client.get("/metrics")
    assert metrics_res.status_code == 200
    text = metrics_res.get_data(as_text=True)

    # Check request count counter
    assert "http_requests_total" in text
    assert 'method="GET"' in text
    assert 'status="200"' in text

    # Check request duration histogram
    assert "http_request_duration_seconds_bucket" in text
    assert "http_request_duration_seconds_count" in text
    assert "http_request_duration_seconds_sum" in text


def test_http_request_tracking_404_unmatched(client):
    """Test tracking requests for non-existent routes."""
    res = client.get("/non-existent-route-for-testing-404")
    assert res.status_code == 404

    metrics_res = client.get("/metrics")
    text = metrics_res.get_data(as_text=True)

    assert "http_requests_total" in text
    assert 'status="404"' in text


def test_http_request_tracking_error_500():
    """Test that internal errors record status 500 in Prometheus metrics."""
    test_app = Flask("test_error_app")
    init_prometheus(test_app)

    @test_app.route("/error-endpoint")
    def error_route():
        raise RuntimeError("Test server crash")

    test_client = test_app.test_client()
    res = test_client.get("/error-endpoint")
    assert res.status_code == 500

    metrics_res = test_client.get("/metrics")
    text = metrics_res.get_data(as_text=True)
    assert 'status="500"' in text


def test_multiprocess_metrics_collection():
    """Test Prometheus metrics collection in multi-process mode."""
    temp_dir = tempfile.mkdtemp()
    old_env = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    try:
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = temp_dir
        assert get_multiproc_dir() == temp_dir

        # Test multiprocess registry and collector
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)

        data, content_type = generate_metrics_response()
        assert isinstance(data, bytes)
        assert "text/plain" in content_type

    finally:
        if old_env is None:
            os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        else:
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = old_env
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_application_url_prefix_metrics():
    """Test that /metrics is also mounted on prefixed URL if configured."""
    test_app = Flask("test_prefix_app")
    test_app.config["APPLICATION_URL_PREFIX"] = "/kalanjiyam"
    init_prometheus(test_app)

    test_client = test_app.test_client()

    res1 = test_client.get("/metrics")
    assert res1.status_code == 200

    res2 = test_client.get("/kalanjiyam/metrics")
    assert res2.status_code == 200


def test_gunicorn_conf_child_exit():
    """Test that gunicorn child_exit hook runs cleanly."""
    spec = importlib.util.spec_from_file_location("gunicorn_conf", "gunicorn.conf.py")
    gconf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gconf)

    mock_server = MagicMock()
    mock_worker = MagicMock()
    mock_worker.pid = 12345

    # Should run cleanly without exception when multiproc dir is not set
    gconf.child_exit(mock_server, mock_worker)

    temp_dir = tempfile.mkdtemp()
    old_env = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    try:
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = temp_dir
        # Should also run cleanly when multiproc dir is set
        gconf.child_exit(mock_server, mock_worker)
    finally:
        if old_env is None:
            os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        else:
            os.environ["PROMETHEUS_MULTIPROC_DIR"] = old_env
        shutil.rmtree(temp_dir, ignore_errors=True)
