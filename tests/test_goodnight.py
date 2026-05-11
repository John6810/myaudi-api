"""Tests for the goodnight check feature.

The lock canonical is vehicle.doors_trunk_status — a string property returning
'Locked' / 'Closed' / 'Open'. The plug canonical is vehicle.plug_state — an
Optional[str] returning 'connected' / 'disconnected' / None (None = ICE).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import server as api_module


def _vehicle(vin="WAU1", locked=True, plug=None, title="Test"):
    """Build a minimal AudiVehicle mock.

    `locked` True/False maps to doors_trunk_status "Locked"/"Closed".
    `plug` True/False/None maps to plug_state "connected"/"disconnected"/None.
    """
    v = MagicMock()
    v.vin = vin
    v.title = title
    v.doors_trunk_status = "Locked" if locked else "Closed"
    if plug is None:
        v.plug_state = None
    else:
        v.plug_state = "connected" if plug else "disconnected"
    return v


@pytest.mark.asyncio
async def test_no_alert_when_locked_and_plugged():
    v = _vehicle(locked=True, plug=True)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    on_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_alert_when_unlocked():
    v = _vehicle(locked=False, plug=True)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    on_alert.assert_awaited_once()
    args = on_alert.await_args.args
    assert "unlocked" in args[1]  # alerts list


@pytest.mark.asyncio
async def test_alert_when_unplugged():
    v = _vehicle(locked=True, plug=False)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    on_alert.assert_awaited_once()
    assert "unplugged" in on_alert.await_args.args[1]


@pytest.mark.asyncio
async def test_both_alerts_combined():
    v = _vehicle(locked=False, plug=False)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    on_alert.assert_awaited_once()
    alerts = on_alert.await_args.args[1]
    assert "unlocked" in alerts and "unplugged" in alerts


@pytest.mark.asyncio
async def test_ice_vehicle_skips_plug_check():
    """Vehicle with plug_state=None (ICE) only checks lock."""
    v = _vehicle(locked=True, plug=None)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    on_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_ice_vehicle_unlocked_still_alerts():
    v = _vehicle(locked=False, plug=None)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    on_alert.assert_awaited_once()
    assert on_alert.await_args.args[1] == ["unlocked"]


@pytest.mark.asyncio
async def test_callback_payload_has_vehicle_and_checks():
    v = _vehicle(locked=False, plug=False)
    on_alert = AsyncMock()
    await api_module._goodnight_check([v], on_alert)
    vehicle_arg, alerts, checks = on_alert.await_args.args
    assert vehicle_arg.vin == "WAU1"
    assert checks == {"locked": False, "plugged": False}


@pytest.mark.asyncio
async def test_multiple_vehicles_independent_alerts():
    v1 = _vehicle(vin="WAU1", locked=True, plug=True)   # no alert
    v2 = _vehicle(vin="WAU2", locked=False, plug=True)  # alert
    on_alert = AsyncMock()
    await api_module._goodnight_check([v1, v2], on_alert)
    on_alert.assert_awaited_once()
    assert on_alert.await_args.args[0].vin == "WAU2"
