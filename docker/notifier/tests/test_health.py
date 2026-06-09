"""
Tests for the /healthz endpoint.

Spec: PHASE2-SPEC.md §1.7 (liveness) + impl card t_3fb28f35.
Task body: /healthz returns 200 + JSON when healthy, 503 when
openwebui_reachable=False or ntfy_reachable=False.
"""

from __future__ import annotations

import json

import httpx
import pytest

import health
import openwebui_client  # for the reachability probe stub


# ---------------------------------------------------------------------------
# JSON shape
# ---------------------------------------------------------------------------


def test_health_json_shape_all_reachable():
    """
    The JSON must contain exactly the four documented keys:
    openwebui_reachable, ntfy_reachable, last_poll_at, dedup_cache_size.
    """
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=True,
        last_poll_at=1_700_000_000.0,
        dedup_cache_size=42,
    )
    body = h.snapshot()
    assert body == {
        "openwebui_reachable": True,
        "ntfy_reachable": True,
        "last_poll_at": 1_700_000_000.0,
        "dedup_cache_size": 42,
    }


def test_health_json_shape_serializes_to_json():
    """The snapshot must be JSON-serializable for the HTTP body."""
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=False,
        last_poll_at=0.0,
        dedup_cache_size=0,
    )
    text = json.dumps(h.snapshot())
    parsed = json.loads(text)
    assert parsed["ntfy_reachable"] is False


# ---------------------------------------------------------------------------
# HTTP status code
# ---------------------------------------------------------------------------


def test_health_returns_200_when_all_reachable():
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=True,
        last_poll_at=1.0,
        dedup_cache_size=0,
    )
    response = h.to_http_response()
    assert response.status_code == 200


def test_health_returns_503_when_openwebui_unreachable():
    h = health.HealthCheck(
        openwebui_reachable=False,
        ntfy_reachable=True,
        last_poll_at=1.0,
        dedup_cache_size=0,
    )
    response = h.to_http_response()
    assert response.status_code == 503


def test_health_returns_503_when_ntfy_unreachable():
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=False,
        last_poll_at=1.0,
        dedup_cache_size=0,
    )
    response = h.to_http_response()
    assert response.status_code == 503


def test_health_returns_503_when_both_unreachable():
    h = health.HealthCheck(
        openwebui_reachable=False,
        ntfy_reachable=False,
        last_poll_at=0.0,
        dedup_cache_size=0,
    )
    response = h.to_http_response()
    assert response.status_code == 503


# ---------------------------------------------------------------------------
# Serve via an actual ASGI/HTTP path
# ---------------------------------------------------------------------------


def test_health_endpoint_served_over_http():
    """
    The HealthCheck class can be wired into a tiny ASGI app or http handler
    and respond over a real HTTP roundtrip. We test the in-process dispatch
    directly to keep the test fast and dependency-free.
    """
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=True,
        last_poll_at=1.0,
        dedup_cache_size=0,
    )

    # HealthCheck must expose a callable that takes a request and returns
    # a status code + JSON dict (the spec's interface).
    status, body = h.serve(method="GET", path="/healthz")
    assert status == 200
    assert body["openwebui_reachable"] is True


def test_health_endpoint_wrong_path_returns_404():
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=True,
        last_poll_at=1.0,
        dedup_cache_size=0,
    )
    status, body = h.serve(method="GET", path="/not-healthz")
    assert status == 404
    # Body may be a string error message or None — both are acceptable.
    assert body is None or isinstance(body, str)


def test_health_endpoint_wrong_method_returns_405():
    h = health.HealthCheck(
        openwebui_reachable=True,
        ntfy_reachable=True,
        last_poll_at=1.0,
        dedup_cache_size=0,
    )
    status, body = h.serve(method="POST", path="/healthz")
    assert status == 405
    # Body may be a string error message or None — both are acceptable.
    assert body is None or isinstance(body, str)
