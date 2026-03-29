"""Tests for audi_connect.watcher module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from audi_connect.watcher import diff_states, check_vehicles


class TestDiffStates:
    def test_no_changes(self):
        prev = {"locked": "Locked", "position": "50.0, 4.0"}
        current = {"locked": "Locked", "position": "50.0, 4.0"}
        assert diff_states(prev, current) == {}

    def test_single_change(self):
        prev = {"locked": "Locked", "position": "50.0, 4.0"}
        current = {"locked": "Closed", "position": "50.0, 4.0"}
        changes = diff_states(prev, current)
        assert changes == {"locked": {"old": "Locked", "new": "Closed"}}

    def test_multiple_changes(self):
        prev = {"locked": "Locked", "range": "200 km"}
        current = {"locked": "Closed", "range": "180 km"}
        changes = diff_states(prev, current)
        assert len(changes) == 2
        assert changes["locked"]["new"] == "Closed"
        assert changes["range"]["new"] == "180 km"

    def test_new_field(self):
        prev = {"locked": "Locked"}
        current = {"locked": "Locked", "battery": "80%"}
        changes = diff_states(prev, current)
        assert changes == {"battery": {"old": "?", "new": "80%"}}

    def test_ignores_vehicle_and_maps(self):
        prev = {"vehicle": "A", "maps": "url1", "locked": "Locked"}
        current = {"vehicle": "B", "maps": "url2", "locked": "Locked"}
        assert diff_states(prev, current) == {}

    def test_empty_prev(self):
        current = {"locked": "Locked", "range": "200 km"}
        changes = diff_states({}, current)
        assert len(changes) == 2


class TestCheckVehicles:
    def _make_vehicle(self, vin="WAUTEST", title="My Audi"):
        v = MagicMock()
        v.vin = vin
        v.title = title
        v.update = AsyncMock()
        v.get_brief = MagicMock(return_value={"vehicle": title, "locked": "Locked", "position": "50, 4"})
        return v

    @pytest.mark.asyncio
    async def test_initial_state_callback(self):
        vehicle = self._make_vehicle()
        prev_states = {}
        on_initial = AsyncMock()

        await check_vehicles([vehicle], prev_states, on_initial=on_initial)

        on_initial.assert_awaited_once()
        assert "WAUTEST" in prev_states

    @pytest.mark.asyncio
    async def test_change_callback(self):
        vehicle = self._make_vehicle()
        prev_states = {"WAUTEST": {"vehicle": "My Audi", "locked": "Locked", "position": "50, 4"}}
        on_change = AsyncMock()

        # Simulate lock state change
        vehicle.get_brief.return_value = {"vehicle": "My Audi", "locked": "Closed", "position": "50, 4"}

        await check_vehicles([vehicle], prev_states, on_change=on_change)

        on_change.assert_awaited_once()
        call_args = on_change.call_args[0]
        assert call_args[0] == vehicle
        assert "locked" in call_args[1]
        assert call_args[1]["locked"]["new"] == "Closed"

    @pytest.mark.asyncio
    async def test_no_change_no_callback(self):
        vehicle = self._make_vehicle()
        prev_states = {"WAUTEST": {"vehicle": "My Audi", "locked": "Locked", "position": "50, 4"}}
        on_change = AsyncMock()

        await check_vehicles([vehicle], prev_states, on_change=on_change)

        on_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_error_callback(self):
        vehicle = self._make_vehicle()
        vehicle.update = AsyncMock(side_effect=Exception("timeout"))
        prev_states = {}
        on_error = AsyncMock()

        await check_vehicles([vehicle], prev_states, on_error=on_error)

        on_error.assert_awaited_once()
        assert prev_states == {}  # Not updated on error

    @pytest.mark.asyncio
    async def test_vin_filter(self):
        v1 = self._make_vehicle(vin="VIN_A")
        v2 = self._make_vehicle(vin="VIN_B")
        prev_states = {}
        on_initial = AsyncMock()

        await check_vehicles([v1, v2], prev_states, on_initial=on_initial, target_vin="VIN_A")

        # Only v1 should be checked
        v1.update.assert_awaited_once()
        v2.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_state_persisted_after_check(self):
        vehicle = self._make_vehicle()
        prev_states = {}

        await check_vehicles([vehicle], prev_states)

        assert prev_states["WAUTEST"]["locked"] == "Locked"
