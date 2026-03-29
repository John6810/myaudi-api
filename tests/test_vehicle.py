"""Tests for audi_connect.vehicle module."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from audi_connect.vehicle import (
    AudiVehicle,
    MIN_CLIMATE_TEMP_C,
    MAX_CLIMATE_TEMP_C,
    MIN_HEATER_DURATION_MIN,
    MAX_HEATER_DURATION_MIN,
)
from audi_connect.exceptions import ActionFailedError
from audi_connect.models import VehicleDataResponse


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
