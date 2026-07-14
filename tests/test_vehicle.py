"""Tests for audi_connect.vehicle module."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from aiohttp import ClientResponseError

from audi_connect.vehicle import (
    AudiVehicle,
    MIN_CLIMATE_TEMP_C,
    MAX_CLIMATE_TEMP_C,
    MIN_HEATER_DURATION_MIN,
    MAX_HEATER_DURATION_MIN,
)
from audi_connect.exceptions import ActionFailedError, RequestTimeoutError
from audi_connect.models import VehicleDataResponse


def _http_error(status: int = 500) -> ClientResponseError:
    return ClientResponseError(request_info=None, history=(), status=status, message="boom")


def _make_auth_mock():
    auth = AsyncMock()
    auth.get_stored_vehicle_data = AsyncMock(return_value={})
    auth.get_stored_position = AsyncMock(return_value={"lat": 50.0, "lon": 4.0, "carCapturedTimestamp": "2024-01-01"})
    auth.get_tripdata = AsyncMock(return_value={})
    auth.start_climate_control = AsyncMock()
    auth.stop_climate_control = AsyncMock()
    auth.start_preheater = AsyncMock()
    auth.stop_preheater = AsyncMock()
    auth.set_vehicle_lock = AsyncMock()
    return auth


def _make_vehicle(auth=None, **info_overrides):
    if auth is None:
        auth = _make_auth_mock()
    vehicle_info = {"vin": "WAUTEST1234567890", "csid": "cs1", **info_overrides}
    return AudiVehicle(auth, vehicle_info)


def _set_access_data(vehicle, doors, windows=None):
    """Attach a CARIAD-shaped access response to a vehicle."""
    vehicle._vehicle_data = VehicleDataResponse({
        "access": {
            "accessStatus": {
                "value": {
                    "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                    "doors": [
                        {"name": name, "status": status}
                        for name, status in doors.items()
                    ],
                    "windows": [
                        {"name": name, "status": status}
                        for name, status in (windows or {}).items()
                    ],
                }
            }
        }
    })


def _all_access_points(lock="locked", closure="closed"):
    return {
        name: [lock, closure]
        for name in ("frontLeft", "frontRight", "rearLeft", "rearRight", "trunk")
    }


class TestVehicleInit:
    def test_basic_init(self):
        v = _make_vehicle()
        assert v.vin == "WAUTEST1234567890"
        assert v.csid == "cs1"

    def test_title_from_nickname(self):
        v = _make_vehicle(nickname="My Audi")
        assert v.title == "My Audi"

    def test_title_from_media(self):
        v = _make_vehicle(vehicle={"media": {"shortName": "A4"}, "core": {"modelYear": "2024"}})
        assert v.title == "A4"
        assert v.model_year == "2024"


class TestIsMoving:
    """Tests for the fixed is_moving logic."""

    @pytest.mark.asyncio
    async def test_not_moving_when_position_available(self):
        v = _make_vehicle()
        await v.update()
        assert v.is_moving is False
        assert v.position is not None

    @pytest.mark.asyncio
    async def test_moving_when_api_returns_none(self):
        auth = _make_auth_mock()
        auth.get_stored_position = AsyncMock(return_value=None)
        v = _make_vehicle(auth=auth)
        await v.update()
        # Position is None but fetch didn't fail → vehicle is moving
        assert v.is_moving is True

    @pytest.mark.asyncio
    async def test_not_moving_when_fetch_fails(self):
        auth = _make_auth_mock()
        auth.get_stored_position = AsyncMock(side_effect=Exception("network error"))
        v = _make_vehicle(auth=auth)
        await v.update()
        # Position is None because fetch failed → NOT moving (just an error)
        assert v.is_moving is False
        assert v._position_failed is True


class TestParallelUpdate:
    """Tests that update() fetches data in parallel."""

    @pytest.mark.asyncio
    async def test_update_calls_all_fetches(self):
        auth = _make_auth_mock()
        auth.get_tripdata = AsyncMock(return_value={
            "tripDataList": {"tripData": [{"overallMileage": 1000, "tripID": "1"}]}
        })
        v = _make_vehicle(auth=auth)
        await v.update()

        auth.get_stored_vehicle_data.assert_awaited_once()
        auth.get_stored_position.assert_awaited_once()
        assert auth.get_tripdata.await_count == 2

    @pytest.mark.asyncio
    async def test_update_continues_on_partial_failure(self):
        auth = _make_auth_mock()
        auth.get_stored_vehicle_data = AsyncMock(side_effect=Exception("fail"))
        auth.get_stored_position = AsyncMock(return_value={"lat": 50.0, "lon": 4.0, "carCapturedTimestamp": "t"})
        v = _make_vehicle(auth=auth)
        await v.update()

        # Position should still be fetched despite vehicle data failure
        assert v._position is not None
        assert v._vehicle_data is None


class TestSafeTripParsing:
    @pytest.mark.asyncio
    async def test_missing_trip_data_list(self):
        auth = _make_auth_mock()
        auth.get_tripdata = AsyncMock(return_value={"unexpected": "format"})
        v = _make_vehicle(auth=auth)
        await v.update()
        assert v.trip_shortterm is None
        assert v.trip_longterm is None

    @pytest.mark.asyncio
    async def test_empty_trip_data(self):
        auth = _make_auth_mock()
        auth.get_tripdata = AsyncMock(return_value={"tripDataList": {"tripData": []}})
        v = _make_vehicle(auth=auth)
        await v.update()
        assert v.trip_shortterm is None

    @pytest.mark.asyncio
    async def test_valid_trip_data(self):
        auth = _make_auth_mock()
        auth.get_tripdata = AsyncMock(return_value={
            "tripDataList": {
                "tripData": [
                    {"overallMileage": 500, "tripID": "1", "averageSpeed": 80},
                    {"overallMileage": 1000, "tripID": "2", "averageSpeed": 90},
                ]
            }
        })
        v = _make_vehicle(auth=auth)
        await v.update()
        # Should pick the one with highest overallMileage
        assert v.trip_shortterm is not None
        assert v.trip_shortterm.overall_mileage == 1000


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_climate_temp_too_low(self):
        v = _make_vehicle()
        with pytest.raises(ActionFailedError, match="Temperature"):
            await v.start_climatisation(temp_c=10.0)

    @pytest.mark.asyncio
    async def test_climate_temp_too_high(self):
        v = _make_vehicle()
        with pytest.raises(ActionFailedError, match="Temperature"):
            await v.start_climatisation(temp_c=35.0)

    @pytest.mark.asyncio
    async def test_climate_temp_valid(self):
        auth = _make_auth_mock()
        v = _make_vehicle(auth=auth)
        await v.start_climatisation(temp_c=22.0)
        auth.start_climate_control.assert_awaited_once_with(v.vin, temp_c=22.0)

    @pytest.mark.asyncio
    async def test_heater_duration_too_short(self):
        v = _make_vehicle()
        with pytest.raises(ActionFailedError, match="Duration"):
            await v.start_preheater(duration=5)

    @pytest.mark.asyncio
    async def test_heater_duration_too_long(self):
        v = _make_vehicle()
        with pytest.raises(ActionFailedError, match="Duration"):
            await v.start_preheater(duration=120)

    @pytest.mark.asyncio
    async def test_heater_duration_valid(self):
        auth = _make_auth_mock()
        v = _make_vehicle(auth=auth)
        await v.start_preheater(duration=30)
        auth.start_preheater.assert_awaited_once_with(v.vin, duration=30)

    @pytest.mark.asyncio
    async def test_boundary_values(self):
        auth = _make_auth_mock()
        v = _make_vehicle(auth=auth)
        # Exact min/max should be valid
        await v.start_climatisation(temp_c=MIN_CLIMATE_TEMP_C)
        await v.start_climatisation(temp_c=MAX_CLIMATE_TEMP_C)
        await v.start_preheater(duration=MIN_HEATER_DURATION_MIN)
        await v.start_preheater(duration=MAX_HEATER_DURATION_MIN)


class TestNullSafetyDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_with_no_data(self):
        v = _make_vehicle()
        dashboard = v.get_dashboard()
        assert "vehicle" in dashboard
        assert "vin" in dashboard
        # Should not crash even with no vehicle data

    @pytest.mark.asyncio
    async def test_dashboard_inspection_missing_km(self):
        """Regression: inspection_due shouldn't show 'None km' when km is unavailable."""
        auth = _make_auth_mock()
        # Return data with inspection days but no km
        auth.get_stored_vehicle_data = AsyncMock(return_value={
            "vehicleHealthInspection": {
                "maintenanceStatus": {
                    "value": {
                        "inspectionDue_days": 30,
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                    }
                }
            }
        })
        v = _make_vehicle(auth=auth)
        await v.update()
        dashboard = v.get_dashboard()
        # Should not have inspection_due because km is missing
        assert "inspection_due" not in dashboard


class TestStructuredAccessState:
    def test_locked_vehicle_with_open_liftgate(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["trunk"] = ["locked", "open"]
        _set_access_data(v, doors)

        access = v.get_dashboard()["access"]
        assert access["lock_status"] == "locked"
        assert access["closure_status"] == "open"
        assert access["liftgate"] == {"locked": True, "open": True}
        # Legacy combined fields intentionally retain their historical values.
        assert v.get_dashboard()["doors_trunk"] == "Open"
        assert v.get_brief()["locked"] == "Open"

    def test_unlocked_vehicle_with_all_closures_closed(self):
        v = _make_vehicle()
        _set_access_data(v, _all_access_points(lock="unlocked"))

        access = v.access_state
        assert access["lock_status"] == "unlocked"
        assert access["closure_status"] == "closed"
        assert all(door["locked"] is False for door in access["doors"].values())
        assert access["liftgate"]["locked"] is False
        assert v.doors_trunk_status == "Closed"

    def test_mixed_lock_states(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["rearRight"] = ["unlocked", "closed"]
        _set_access_data(v, doors)

        assert v.lock_status == "mixed"
        assert v.front_left_door_locked is True
        assert v.rear_right_door_locked is False

    def test_one_door_open_while_locked(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["frontLeft"] = ["locked", "open"]
        _set_access_data(v, doors)

        assert v.lock_status == "locked"
        assert v.closure_status == "open"
        assert v.front_left_door_open is True
        assert v.front_right_door_open is False

    def test_hood_open_is_independent(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["bonnet"] = ["open"]
        _set_access_data(v, doors)

        access = v.access_state
        assert access["closure_status"] == "closed"
        assert access["hood"] == {"open": True}
        assert v.hood_open_state is True
        assert v.hood_open is True
        assert v.get_dashboard()["hood"] == "Open"

    def test_hood_open_retains_boolean_compatibility_for_unknown(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["bonnet"] = ["unknown"]
        _set_access_data(v, doors)

        assert v.hood_open_state is None
        assert v.hood_open is True

        missing = _make_vehicle()
        missing._vehicle_data = VehicleDataResponse({})
        assert missing.hood_open_state is None
        assert missing.hood_open is False

    def test_unknown_lock_field_makes_uniform_aggregate_unknown(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["frontLeft"] = ["unknown", "closed"]
        _set_access_data(v, doors)

        assert v.front_left_door_locked is None
        assert v.lock_status == "unknown"
        assert v.closure_status == "closed"
        assert v.any_door_unlocked is True
        assert v.get_dashboard()["doors_trunk"] == "Closed"
        assert v.get_brief()["locked"] == "Closed"

    def test_unknown_closure_field_is_not_inferred_open_or_closed(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["frontLeft"] = ["locked", "unknown"]
        _set_access_data(v, doors)

        assert v.front_left_door_open is None
        assert v.closure_status == "unknown"
        assert v.any_door_open is True
        assert v.get_dashboard()["doors_trunk"] == "Open"

    def test_all_access_fields_missing(self):
        v = _make_vehicle()
        v._vehicle_data = VehicleDataResponse({})

        access = v.get_dashboard()["access"]
        assert access["lock_status"] == "unknown"
        assert access["closure_status"] == "unknown"
        assert access["liftgate"] == {"locked": None, "open": None}
        assert access["hood"] == {"open": None}
        assert all(
            value is None
            for door in access["doors"].values()
            for value in door.values()
        )
        assert all(
            window["open"] is None for window in access["windows"].values()
        )
        # These unsafe defaults are preserved only for backward compatibility.
        assert v.get_dashboard()["doors_trunk"] == "Locked"
        assert v.get_dashboard()["windows"] == "Closed"
        assert v.get_brief()["locked"] == "Locked"

    def test_each_ordinary_window_has_explicit_tristate_property(self):
        v = _make_vehicle()
        _set_access_data(
            v,
            _all_access_points(),
            windows={
                "frontLeft": ["open"],
                "frontRight": ["closed"],
                "rearLeft": ["unknown"],
                # rearRight deliberately missing
            },
        )

        assert v.front_left_window_open is True
        assert v.front_right_window_open is False
        assert v.rear_left_window_open is None
        assert v.rear_right_window_open is None
        assert v.any_window_open is True
        assert v.access_state["windows"] == {
            "front_left": {"open": True},
            "front_right": {"open": False},
            "rear_left": {"open": None},
            "rear_right": {"open": None},
        }

    def test_unknown_windows_preserve_legacy_open_behavior(self):
        v = _make_vehicle()
        _set_access_data(
            v,
            _all_access_points(),
            windows={"frontLeft": ["unknown"], "frontRight": []},
        )

        assert v.front_left_window_open is None
        assert v.front_right_window_open is None
        assert v.any_window_open is True
        assert v.get_dashboard()["windows"] == "Open"

    def test_later_closed_window_state_does_not_change_legacy_first_status_behavior(self):
        v = _make_vehicle()
        _set_access_data(
            v,
            _all_access_points(),
            windows={"frontLeft": ["unknown", "closed"]},
        )

        assert v.front_left_window_open is False
        assert v.any_window_open is True
        assert v.get_dashboard()["windows"] == "Open"

    def test_contradictory_door_states_do_not_change_legacy_combined_behavior(self):
        v = _make_vehicle()
        doors = _all_access_points()
        doors["frontLeft"] = ["locked", "unlocked", "closed"]
        _set_access_data(v, doors)

        assert v.front_left_door_locked is None
        assert v.lock_status == "unknown"
        assert v.any_door_unlocked is False
        assert v.doors_trunk_status == "Locked"

        doors["frontLeft"] = ["locked", "open", "closed"]
        _set_access_data(v, doors)
        assert v.front_left_door_open is None
        assert v.closure_status == "unknown"
        assert v.any_door_open is False
        assert v.doors_trunk_status == "Locked"


class TestBrief:
    def test_brief_with_position(self):
        v = _make_vehicle(nickname="My A4")
        v._position = {"lat": 50.123, "lon": 4.456, "carCapturedTimestamp": "t"}
        brief = v.get_brief()
        assert brief["vehicle"] == "My A4"
        assert brief["locked"] == "Locked"
        assert "50.123" in brief["position"]
        assert "maps" in brief
        assert "google.com/maps" in brief["maps"]

    def test_brief_moving(self):
        v = _make_vehicle()
        v._position = None
        v._position_failed = False
        brief = v.get_brief()
        assert brief["position"] == "Vehicle is moving"

    def test_brief_position_failed(self):
        v = _make_vehicle()
        v._position = None
        v._position_failed = True
        brief = v.get_brief()
        assert brief["position"] == "Unknown"

    def test_brief_no_maps_when_no_position(self):
        v = _make_vehicle()
        v._position = None
        brief = v.get_brief()
        assert "maps" not in brief


class TestPosition:
    def test_position_with_data_wrapper(self):
        v = _make_vehicle()
        v._position = {"data": {"lat": 50.0, "lon": 4.0, "carCapturedTimestamp": "t"}}
        pos = v.position
        assert pos["latitude"] == 50.0
        assert pos["longitude"] == 4.0

    def test_position_without_data_wrapper(self):
        v = _make_vehicle()
        v._position = {"lat": 50.0, "lon": 4.0, "carCapturedTimestamp": "t"}
        pos = v.position
        assert pos["latitude"] == 50.0

    def test_position_none(self):
        v = _make_vehicle()
        v._position = None
        assert v.position is None


class TestActionRetryPolicy:
    """Verify retry is applied only to idempotent actions.

    lock / stop_climatisation / stop_preheater retry up to 3x on transport
    or 5xx errors. unlock / start_climatisation / start_preheater do NOT
    retry — duplicates can re-trigger notifications, extend timers, or
    burn S-PIN security tokens against the ~6 req/h Audi budget.
    """

    @pytest.mark.asyncio
    async def test_lock_retries_on_timeout(self):
        auth = _make_auth_mock()
        auth.set_vehicle_lock = AsyncMock(
            side_effect=[RequestTimeoutError("t1"), RequestTimeoutError("t2"), None]
        )
        v = _make_vehicle(auth=auth)
        await v.lock()
        assert auth.set_vehicle_lock.await_count == 3

    @pytest.mark.asyncio
    async def test_lock_retries_on_client_response_error(self):
        auth = _make_auth_mock()
        auth.set_vehicle_lock = AsyncMock(side_effect=_http_error(503))
        v = _make_vehicle(auth=auth)
        with pytest.raises(ClientResponseError):
            await v.lock()
        assert auth.set_vehicle_lock.await_count == 3

    @pytest.mark.asyncio
    async def test_unlock_does_not_retry_on_timeout(self):
        auth = _make_auth_mock()
        auth.set_vehicle_lock = AsyncMock(side_effect=RequestTimeoutError("timeout"))
        v = _make_vehicle(auth=auth)
        with pytest.raises(RequestTimeoutError):
            await v.unlock()
        assert auth.set_vehicle_lock.await_count == 1

    @pytest.mark.asyncio
    async def test_unlock_does_not_retry_on_client_response_error(self):
        auth = _make_auth_mock()
        auth.set_vehicle_lock = AsyncMock(side_effect=_http_error(500))
        v = _make_vehicle(auth=auth)
        with pytest.raises(ClientResponseError):
            await v.unlock()
        assert auth.set_vehicle_lock.await_count == 1

    @pytest.mark.asyncio
    async def test_start_climatisation_does_not_retry(self):
        auth = _make_auth_mock()
        auth.start_climate_control = AsyncMock(side_effect=_http_error(500))
        v = _make_vehicle(auth=auth)
        with pytest.raises(ClientResponseError):
            await v.start_climatisation(temp_c=21.0)
        assert auth.start_climate_control.await_count == 1

    @pytest.mark.asyncio
    async def test_start_climatisation_validation_still_raises_without_call(self):
        auth = _make_auth_mock()
        v = _make_vehicle(auth=auth)
        with pytest.raises(ActionFailedError):
            await v.start_climatisation(temp_c=10.0)  # below MIN_CLIMATE_TEMP_C
        # Validation must short-circuit before any network call.
        auth.start_climate_control.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_climatisation_retries_on_timeout(self):
        auth = _make_auth_mock()
        auth.stop_climate_control = AsyncMock(
            side_effect=[RequestTimeoutError("t1"), RequestTimeoutError("t2"), None]
        )
        v = _make_vehicle(auth=auth)
        await v.stop_climatisation()
        assert auth.stop_climate_control.await_count == 3

    @pytest.mark.asyncio
    async def test_start_preheater_does_not_retry(self):
        auth = _make_auth_mock()
        auth.start_preheater = AsyncMock(side_effect=_http_error(500))
        v = _make_vehicle(auth=auth)
        with pytest.raises(ClientResponseError):
            await v.start_preheater(duration=30)
        assert auth.start_preheater.await_count == 1

    @pytest.mark.asyncio
    async def test_start_preheater_validation_still_raises_without_call(self):
        auth = _make_auth_mock()
        v = _make_vehicle(auth=auth)
        with pytest.raises(ActionFailedError):
            await v.start_preheater(duration=5)  # below MIN_HEATER_DURATION_MIN
        auth.start_preheater.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stop_preheater_retries_on_timeout(self):
        auth = _make_auth_mock()
        auth.stop_preheater = AsyncMock(
            side_effect=[RequestTimeoutError("t1"), RequestTimeoutError("t2"), None]
        )
        v = _make_vehicle(auth=auth)
        await v.stop_preheater()
        assert auth.stop_preheater.await_count == 3
