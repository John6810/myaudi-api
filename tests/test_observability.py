"""Tests for the observability surface: /metrics, /ready, and request-id middleware."""

import re
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import server as api_module


@pytest.fixture
def tc(monkeypatch):
    """TestClient with a configured API key so protected endpoints don't 503."""
    monkeypatch.setattr(api_module, "AUDI_API_KEY", "test-key")
    monkeypatch.setattr(api_module.client, "ensure_auth", AsyncMock(return_value=True))
    monkeypatch.setattr(api_module.client, "update_vehicles", AsyncMock(return_value=None))
    monkeypatch.setattr(api_module.client, "vehicles", [])
    return TestClient(api_module.app)


def test_metrics_endpoint_returns_200_and_text_format(tc):
    response = tc.get("/metrics")
    assert response.status_code == 200
    # Prometheus exposition format is text/plain.
    assert response.headers["content-type"].startswith("text/plain")
    # At least one of our business counters must appear (registered at import time).
    assert "audi_auth_refresh_total" in response.text


def test_metrics_endpoint_does_not_require_api_key(tc):
    # No X-API-Key header — Prometheus has no way to send one.
    response = tc.get("/metrics")
    assert response.status_code == 200


def test_ready_returns_503_when_not_authenticated(tc, monkeypatch):
    monkeypatch.setattr(api_module.client, "authenticated", False)
    response = tc.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"] == "Not authenticated to Audi Connect"


def test_ready_returns_200_when_authenticated(tc, monkeypatch):
    monkeypatch.setattr(api_module.client, "authenticated", True)
    response = tc.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_does_not_require_api_key(tc, monkeypatch):
    # /ready is for kubelet — never returns 401, even without the key header.
    monkeypatch.setattr(api_module.client, "authenticated", True)
    response = tc.get("/ready")
    assert response.status_code != 401


def test_request_id_header_returned_when_not_provided(tc):
    response = tc.get("/health")
    assert "X-Request-ID" in response.headers
    rid = response.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{12}", rid), f"unexpected rid format: {rid!r}"


def test_request_id_echoed_when_provided(tc):
    response = tc.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
