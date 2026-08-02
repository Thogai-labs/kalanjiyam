"""Tests for Superadmin Metrics System (Queues, Latencies, and Error Logs) with OpenTelemetry integration."""

import json
from kalanjiyam import database as db
from kalanjiyam.utils.metrics import (
    record_metric,
    get_active_celery_queues,
    get_latency_metrics_summary,
    get_error_logs_paginated,
)
from kalanjiyam.utils.otel import get_current_trace_id


def test_metrics_view__unauthenticated(client):
    resp = client.get("/admin/platform/metrics")
    assert resp.status_code in (403, 404)


def test_metrics_view__moderator_forbidden(moderator_client):
    resp = moderator_client.get("/admin/platform/metrics")
    assert resp.status_code in (403, 404)


def test_metrics_view__superadmin_success(superadmin_client):
    resp = superadmin_client.get("/admin/platform/metrics")
    assert resp.status_code == 200
    assert b"System Metrics & Logs" in resp.data
    assert b"Running Queues" in resp.data
    assert b"Latencies" in resp.data
    assert b"Error Logs" in resp.data


def test_metrics_api__queues(superadmin_client):
    resp = superadmin_client.get("/admin/platform/metrics/api?tab=queues")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "active_count" in data
    assert "pending_count" in data
    assert "active_tasks" in data


def test_metrics_api__latencies(superadmin_client):
    # Record dummy latency metric
    record_metric(category="latency", name="GET /api/test", latency_ms=45.2, status="200")

    resp = superadmin_client.get("/admin/platform/metrics/api?tab=latencies")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "avg_web_latency" in data
    assert "p95_web_latency" in data
    assert "by_service" in data


def test_metrics_api__errors(superadmin_client):
    # Record dummy error log
    record_metric(
        category="error",
        name="TestException",
        error_level="ERROR",
        error_message="Test failure occurred",
        traceback_str="Traceback (most recent call last):\n  File 'test.py', line 10",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
    )

    resp = superadmin_client.get("/admin/platform/metrics/api?tab=errors")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "records" in data
    assert "total" in data
    assert data["total"] >= 1
    record = next(r for r in data["records"] if r["name"] == "TestException")
    assert record["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_record_metric_and_query_helpers():
    m = record_metric(
        category="queue",
        name="kalanjiyam.tasks.ocr.run_ocr",
        status="SUCCESS",
        latency_ms=1200.5,
        trace_id="1234567890abcdef1234567890abcdef",
    )
    assert m is not None
    assert m.id is not None
    assert m.category == "queue"
    assert m.trace_id == "1234567890abcdef1234567890abcdef"

    queues = get_active_celery_queues()
    assert "active_count" in queues

    latencies = get_latency_metrics_summary(days=1)
    assert "avg_web_latency" in latencies

    errors = get_error_logs_paginated(page=1, per_page=10)
    assert "records" in errors


def test_otel_get_current_trace_id_fallback():
    # Should safely return None when no active span is present
    trace_id = get_current_trace_id()
    assert trace_id is None or isinstance(trace_id, str)
