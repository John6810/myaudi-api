"""Tests for the X-API-Key auth dependency on the FastAPI server."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import api as api_module


@pytest.fixture
def client_with_key(monkeypatch):
    """TestClient with AUDI_API_KEY=test-key configured and the Audi backend mocked."""
    monkeypatch.setattr(api_module, "AUDI_API_KEY", "test-key")
    monkeypatch.setattr(api_module.client, "ensure_auth", AsyncMock(return_value=True))
    monkeypatch.setattr(api_module.client, "update_vehicles", AsyncMock(return_value=None))
    monkeypatch.setattr(api_module.client, "vehicles", [])
    monkeypatch.setattr(api_module.client, "authenticated", True)
    return TestClient(api_module.app)


def test_endpoint_returns_401_without_header(client_with_key):
    response = client_with_key.get("/brief")
    assert response.status_code == 401
    assert "Invalid or missing X-API-Key" in response.json()["detail"]


def test_endpoint_returns_401_with_wrong_key(client_with_key):
    response = client_with_key.get("/brief", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_endpoint_returns_503_when_key_not_configured(monkeypatch):
    monkeypatch.setattr(api_module, "AUDI_API_KEY", "")
    monkeypatch.setattr(api_module.client, "ensure_auth", AsyncMock(return_value=True))
    monkeypatch.setattr(api_module.client, "update_vehicles", AsyncMock(return_value=None))
    monkeypatch.setattr(api_module.client, "vehicles", [])
    with TestClient(api_module.app) as tc:
        response = tc.get("/brief", headers={"X-API-Key": "anything"})
    assert response.status_code == 503
    assert "API key not configured" in response.json()["detail"]


def test_endpoint_returns_200_with_valid_key(client_with_key):
    response = client_with_key.get("/brief", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200
    assert response.json() == {"vehicles": []}


def test_health_endpoint_remains_public(client_with_key):
    # /health must stay open so Kubernetes liveness probes don't need the key.
    response = client_with_key.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] in ("ok", "degraded")
