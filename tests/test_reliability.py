"""Regression tests for truthful status and shared upstream request budgets."""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

import server
from audi_connect.exceptions import DeviceGrantRejectedError, VehicleUpdateError
from audi_connect.models import VehicleDataResponse
from audi_connect.vehicle import AudiVehicle
from audi_connect.watcher import check_vehicles


NOW = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


def vehicle_with_access(state="locked", timestamp=None):
    v = AudiVehicle(AsyncMock(), {"vin": "TEST"})
    v._vehicle_data = VehicleDataResponse({"access": {"accessStatus": {"value": {
        "carCapturedTimestamp": timestamp or (NOW + timedelta(seconds=2)).isoformat(),
        "doors": [
            {"name": point, "status": [state, "closed"]}
            for point in ("frontLeft", "frontRight", "rearLeft", "rearRight", "trunk")
        ],
    }}}})
    return v


@pytest.mark.parametrize("action,state,expected", [
    ("lock", "locked", True), ("lock", "unlocked", False),
    ("unlock", "unlocked", True), ("unlock", "locked", False),
    ("lock", "unknown", None), ("unlock", "unknown", None),
])
def test_lock_confirmation_needs_explicit_telemetry(action, state, expected):
    assert vehicle_with_access(state).action_confirmed(action, NOW) is expected


@pytest.mark.parametrize("timestamp", [
    (NOW - timedelta(seconds=10)).isoformat(), "invalid", "2026-10-08T12:00:02",
])
def test_lock_confirmation_rejects_stale_or_unusable_timestamps(timestamp):
    assert vehicle_with_access(timestamp=timestamp).action_confirmed("lock", NOW) is None


@pytest.mark.parametrize("action,state,expected", [
    ("climate_start", "heating", True), ("climate_start", "cooling", True),
    ("climate_start", "ventilation", True), ("climate_start", "off", False),
    ("climate_stop", "off", True), ("climate_stop", "heating", False),
    ("climate_start", "unknown", None), ("climate_stop", "unknown", None),
])
def test_climate_confirmation_checks_requested_state(action, state, expected):
    v = vehicle_with_access()
    v._vehicle_data = VehicleDataResponse({"climatisation": {"climatisationStatus": {"value": {
        "carCapturedTimestamp": (NOW + timedelta(seconds=2)).isoformat(),
        "climatisationState": state,
    }}}})
    assert v.action_confirmed(action, NOW) is expected


@pytest.mark.parametrize("action", ["lock", "unlock", "climate_start", "climate_stop", "heater_start", "heater_stop"])
def test_no_telemetry_never_confirms(action):
    v = AudiVehicle(AsyncMock(), {"vin": "TEST"})
    assert v.action_confirmed(action, NOW) is None
    assert v.get_brief()["locked"] == "Unknown"


@pytest.mark.asyncio
async def test_failed_update_not_cached_and_recovery_is_backed_off(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    c = server.AudiClient()
    v = vehicle_with_access()
    v.update = AsyncMock(side_effect=[VehicleUpdateError("down"), None])
    c.vehicles = [v]
    for _ in range(2):
        with pytest.raises(VehicleUpdateError):
            await c.update_vehicles()
    assert c._last_update == 0
    assert v.update.await_count == 1
    clock[0] += server.UPSTREAM_RETRY_INTERVAL + 1
    await c.update_vehicles()
    assert c._last_update > 0
    assert v.update.await_count == 2
    await c.update_vehicles()
    assert v.update.await_count == 2


@pytest.mark.asyncio
async def test_failure_during_forced_refresh_does_not_revive_old_cache(monkeypatch):
    c = server.AudiClient()
    c._last_update = time.time()
    v = vehicle_with_access()
    v.update = AsyncMock(side_effect=[VehicleUpdateError("down"), None])
    c.vehicles = [v]
    with pytest.raises(VehicleUpdateError):
        await c.update_vehicles(force=True)
    c._next_update_at = 0
    await c.update_vehicles()
    assert v.update.await_count == 2


@pytest.mark.asyncio
async def test_concurrent_forced_refreshes_share_one_read():
    c = server.AudiClient()
    v = vehicle_with_access()
    v.update = AsyncMock()
    c.vehicles = [v]
    await asyncio.gather(*(c.update_vehicles(force=True) for _ in range(5)))
    v.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_watcher_reuses_already_fetched_data():
    v = vehicle_with_access()
    v.update = AsyncMock()
    on_initial = AsyncMock()
    await check_vehicles([v], {}, on_initial=on_initial, refresh=False)
    v.update.assert_not_awaited()
    on_initial.assert_awaited_once()


@pytest.mark.asyncio
async def test_background_watcher_reads_once_per_cycle(monkeypatch):
    c = server.AudiClient()
    c.ensure_auth = AsyncMock(return_value=True)
    v = vehicle_with_access()
    v.update = AsyncMock()
    c.vehicles = [v]
    monkeypatch.setattr(server, "client", c)
    monkeypatch.setattr(server, "GOODNIGHT_HOUR", 0)
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await server._background_watcher()
    v.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_goodnight_missing_data_reports_unknown():
    v = AudiVehicle(AsyncMock(), {"vin": "TEST"})
    on_alert = AsyncMock()
    await server._goodnight_check([v], on_alert)
    assert on_alert.await_args.args[1] == ["lock_unknown"]
    assert on_alert.await_args.args[2]["locked"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action,expected", [
    ("lock", "confirmed"), ("unlock", "pending"), ("climate_start", "sent_unconfirmed"),
])
async def test_confirmation_response(monkeypatch, action, expected):
    v = vehicle_with_access()
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(server.client, "update_vehicles", AsyncMock())
    result = await server._confirm_action(v, action, NOW)
    assert result["status"] == expected


@pytest.mark.asyncio
async def test_heater_confirmation_does_not_fetch_unsupported_state(monkeypatch):
    update = AsyncMock()
    monkeypatch.setattr(server.client, "update_vehicles", update)
    result = await server._confirm_action(vehicle_with_access(), "heater_start", NOW)
    assert result["status"] == "sent_unconfirmed"
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_failure_cannot_confirm_old_locked_state(monkeypatch):
    c = server.AudiClient()
    v = vehicle_with_access()
    v.update = AsyncMock(side_effect=VehicleUpdateError("down"))
    c.vehicles = [v]
    monkeypatch.setattr(server, "client", c)
    monkeypatch.setattr(server.asyncio, "sleep", AsyncMock())
    result = await server._confirm_action(v, "lock", NOW)
    assert result["status"] == "sent_unconfirmed"


def test_http_failed_refresh_returns_503_and_retry_after(monkeypatch):
    c = server.AudiClient()
    c.ensure_auth = AsyncMock(return_value=True)
    v = vehicle_with_access()
    v.update = AsyncMock(side_effect=VehicleUpdateError("down"))
    c.vehicles = [v]
    monkeypatch.setattr(server, "client", c)
    monkeypatch.setattr(server, "AUDI_API_KEY", "test-key")
    monkeypatch.setattr(server.limiter, "enabled", False)
    tc = TestClient(server.app)
    for _ in range(2):
        result = tc.get("/status", headers={"X-API-Key": "test-key"})
        assert result.status_code == 503
        assert int(result.headers["Retry-After"]) > 0
    v.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_action_without_confirmation_invalidates_cache(monkeypatch):
    c = server.AudiClient()
    monkeypatch.setattr(server, "client", c)
    action = AsyncMock()
    sent_at = await server._track_action("lock", vehicle_with_access(), action())
    assert c._cache_generation != c._cached_generation
    assert sent_at.tzinfo is not None


@pytest.mark.asyncio
async def test_rejected_device_grant_stops_further_authentication(monkeypatch):
    c = server.AudiClient()
    c._session = MagicMock()
    auth = MagicMock()
    auth.retry_after = 0
    auth.login = AsyncMock(side_effect=DeviceGrantRejectedError("unauthorized_client"))
    monkeypatch.setattr(server, "AudiAuth", MagicMock(return_value=auth))
    assert await c.ensure_auth() is False
    c._auth_retry_at = 0  # Even after the ordinary retry delay, a grant remains blocked.
    assert await c.ensure_auth() is False
    auth.login.assert_awaited_once()
