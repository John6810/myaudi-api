"""Tests for GET /last-parked endpoint."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import server as api_module


def _make_vehicle(vin="WAUTEST123", position=None):
    """Build a minimal AudiVehicle mock with a position property."""
    v = MagicMock()
    v.vin = vin
    v.title = "Test Audi"
    v.position = position
    return v


@pytest.fixture
def tc(monkeypatch):
    monkeypatch.setattr(api_module, "AUDI_API_KEY", "test-key")
    monkeypatch.setattr(api_module.client, "ensure_auth", AsyncMock(return_value=True))
    monkeypatch.setattr(api_module.client, "update_vehicles", AsyncMock(return_value=None))
    monkeypatch.setattr(api_module.client, "authenticated", True)
    return TestClient(api_module.app)


def test_returns_200_with_position(tc, monkeypatch):
    v = _make_vehicle(position={
        "latitude": 49.684353,
        "longitude": 5.436657,
        "timestamp": "2026-04-12T16:45:00+02:00",
    })
    monkeypatch.setattr(api_module.client, "vehicles", [v])
    r = tc.get("/last-parked", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["vin"] == "WAUTEST123"
    assert body["latitude"] == 49.684353
    assert body["longitude"] == 5.436657
    assert body["parked_at"] == "2026-04-12T16:45:00+02:00"
    assert body["google_maps"] == "https://www.google.com/maps?q=49.684353,5.436657"


def test_filters_by_vin(tc, monkeypatch):
    v1 = _make_vehicle(vin="WAU111", position={"latitude": 1.0, "longitude": 2.0, "timestamp": "t1"})
    v2 = _make_vehicle(vin="WAU222", position={"latitude": 3.0, "longitude": 4.0, "timestamp": "t2"})
    monkeypatch.setattr(api_module.client, "vehicles", [v1, v2])
    r = tc.get("/last-parked?vin=WAU222", headers={"X-API-Key": "test-key"})
    assert r.status_code == 200
    assert r.json()["vin"] == "WAU222"
    assert r.json()["latitude"] == 3.0


def test_returns_404_when_no_position(tc, monkeypatch):
    v = _make_vehicle(position=None)
    monkeypatch.setattr(api_module.client, "vehicles", [v])
    r = tc.get("/last-parked", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404
    assert "No parking position available" in r.json()["detail"]


def test_returns_404_when_position_lat_missing(tc, monkeypatch):
    v = _make_vehicle(position={"latitude": None, "longitude": None, "timestamp": "t"})
    monkeypatch.setattr(api_module.client, "vehicles", [v])
    r = tc.get("/last-parked", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404


def test_returns_404_when_unknown_vin(tc, monkeypatch):
    v = _make_vehicle(vin="WAU111", position={"latitude": 1.0, "longitude": 2.0, "timestamp": "t"})
    monkeypatch.setattr(api_module.client, "vehicles", [v])
    r = tc.get("/last-parked?vin=WAUUNKNOWN", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404


def test_returns_404_when_no_vehicles_available(tc, monkeypatch):
    monkeypatch.setattr(api_module.client, "vehicles", [])
    r = tc.get("/last-parked", headers={"X-API-Key": "test-key"})
    assert r.status_code == 404
    assert "No vehicles available" in r.json()["detail"]


def test_returns_401_without_api_key(tc, monkeypatch):
    v = _make_vehicle(position={"latitude": 1.0, "longitude": 2.0, "timestamp": "t"})
    monkeypatch.setattr(api_module.client, "vehicles", [v])
    r = tc.get("/last-parked")
    assert r.status_code == 401
