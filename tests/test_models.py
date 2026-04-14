"""Tests for audi_connect.models module."""

import pytest
from datetime import datetime, timezone

from audi_connect.models import VehicleDataResponse, Field, TripDataResponse, VehiclesResponse, VehicleInfo, LockState, DoorState, WindowState


class TestField:
    def test_basic_field(self):
        f = Field({"textId": "TOTAL_RANGE", "value": 450, "unit": "km"})
        assert f.name == "TOTAL_RANGE"
        assert f.value == 450
        assert f.unit == "km"

    def test_str_with_unit(self):
        f = Field({"textId": "RANGE", "value": 100, "unit": "km"})
        assert str(f) == "RANGE: 100km"

    def test_str_without_unit(self):
        f = Field({"textId": "STATUS", "value": "ok"})
        assert str(f) == "STATUS: ok"

    def test_missing_fields(self):
        f = Field({})
        assert f.name is None
        assert f.value is None
        assert f.unit is None
        assert f.id is None

    def test_measure_time_from_tss(self):
        f = Field({"textId": "X", "tsTssReceivedUtc": "2024-01-01"})
        assert f.measure_time == "2024-01-01"

    def test_measure_time_fallback_to_car_captured(self):
        f = Field({"textId": "X", "tsCarCaptured": "2024-02-02"})
        assert f.measure_time == "2024-02-02"


class TestVehicleDataResponse:
    def _make_data(self, **overrides):
        """Build a minimal vehicle data dict."""
        base = {}
        base.update(overrides)
        return base

    def test_empty_data(self):
        vdr = VehicleDataResponse({})
        assert vdr.data_fields == []
        assert vdr.states == []

    def test_parses_total_range(self):
        data = {
            "fuelStatus": {
                "rangeStatus": {
                    "value": {
                        "totalRange_km": 500,
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                    }
                }
            }
        }
        vdr = VehicleDataResponse(data)
        range_fields = [f for f in vdr.data_fields if f.name == "TOTAL_RANGE"]
        assert len(range_fields) == 1
        assert range_fields[0].value == 500

    def test_parses_odometer(self):
        data = {
            "measurements": {
                "odometerStatus": {
                    "value": {
                        "odometer": 55000,
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                    }
                }
            }
        }
        vdr = VehicleDataResponse(data)
        odo_fields = [f for f in vdr.data_fields if f.name == "UTC_TIME_AND_KILOMETER_STATUS"]
        assert len(odo_fields) == 1
        assert odo_fields[0].value == 55000

    def test_parses_charging_state(self):
        data = {
            "charging": {
                "batteryStatus": {
                    "value": {
                        "currentSOC_pct": 80,
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                    }
                },
                "chargingStatus": {
                    "value": {
                        "chargingState": "charging",
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                    }
                },
            }
        }
        vdr = VehicleDataResponse(data)
        soc_states = [s for s in vdr.states if s["name"] == "stateOfCharge"]
        assert len(soc_states) == 1
        assert soc_states[0]["value"] == 80

    def test_parses_door_states(self):
        data = {
            "access": {
                "accessStatus": {
                    "value": {
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                        "doors": [
                            {"name": "frontLeft", "status": ["locked", "closed"]},
                            {"name": "frontRight", "status": ["locked", "closed"]},
                        ],
                        "windows": [],
                    }
                }
            }
        }
        vdr = VehicleDataResponse(data)
        lock_fields = [f for f in vdr.data_fields if "LOCK_STATE" in (f.name or "")]
        assert len(lock_fields) == 2
        # locked = "2"
        assert all(f.value == "2" for f in lock_fields)

    def test_parses_window_states(self):
        data = {
            "access": {
                "accessStatus": {
                    "value": {
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                        "doors": [],
                        "windows": [
                            {"name": "frontLeft", "status": ["closed"]},
                            {"name": "frontRight", "status": ["open"]},
                        ],
                    }
                }
            }
        }
        vdr = VehicleDataResponse(data)
        window_fields = [f for f in vdr.data_fields if "WINDOW" in (f.name or "")]
        assert len(window_fields) == 2
        closed_windows = [f for f in window_fields if f.value == "3"]
        open_windows = [f for f in window_fields if f.value == "0"]
        assert len(closed_windows) == 1
        assert len(open_windows) == 1


class TestTripDataResponse:
    def test_full_data(self):
        data = {
            "tripID": "123",
            "averageElectricEngineConsumption": 150,
            "averageFuelConsumption": 65,
            "averageSpeed": 80,
            "mileage": 250,
            "startMileage": 100,
            "traveltime": 180,
            "timestamp": "2024-01-01",
            "overallMileage": 50000,
            "zeroEmissionDistance": 50,
        }
        trip = TripDataResponse(data)
        assert trip.trip_id == "123"
        assert trip.average_electric_consumption == 15.0
        assert trip.average_fuel_consumption == 6.5
        assert trip.average_speed == 80
        assert trip.mileage == 250
        assert trip.start_mileage == 100
        assert trip.travel_time == 180
        assert trip.overall_mileage == 50000
        assert trip.zero_emission_distance == 50

    def test_missing_optional_fields(self):
        trip = TripDataResponse({})
        assert trip.trip_id is None
        assert trip.average_electric_consumption is None
        assert trip.average_fuel_consumption is None
        assert trip.average_speed is None
        assert trip.mileage is None


class TestVehiclesResponse:
    def test_parse_vehicles(self):
        data = {
            "userVehicles": [
                {
                    "vin": "WAUTEST123",
                    "csid": "cs1",
                    "nickname": "My Audi",
                    "vehicle": {
                        "media": {"shortName": "A4", "longName": "Audi A4 Avant"},
                        "core": {"modelYear": "2024"},
                    },
                }
            ]
        }
        vr = VehiclesResponse()
        vr.parse(data)
        assert len(vr.vehicles) == 1
        v = vr.vehicles[0]
        assert v.vin == "WAUTEST123"
        assert v.title == "My Audi"
        assert v.model == "Audi A4 Avant"
        assert v.model_year == "2024"

    def test_empty_response(self):
        vr = VehiclesResponse()
        vr.parse({})
        assert len(vr.vehicles) == 0


class TestEnums:
    def test_lock_state_values(self):
        assert LockState.UNKNOWN.value == "0"
        assert LockState.LOCKED.value == "2"
        assert LockState.CLOSED.value == "3"

    def test_door_state_values(self):
        assert DoorState.UNKNOWN.value == "0"
        assert DoorState.CLOSED.value == "3"

    def test_window_state_values(self):
        assert WindowState.OPEN.value == "0"
        assert WindowState.CLOSED.value == "3"

    def test_enum_string_comparison(self):
        """Enums are str subclasses, so they can be compared to plain strings."""
        assert LockState.LOCKED == "2"
        assert WindowState.CLOSED == "3"

    def test_door_states_used_in_parsing(self):
        """Verify that parsed door data uses enum-compatible values."""
        data = {
            "access": {
                "accessStatus": {
                    "value": {
                        "carCapturedTimestamp": "2024-01-01T00:00:00+0000",
                        "doors": [
                            {"name": "frontLeft", "status": ["locked", "closed"]},
                        ],
                        "windows": [],
                    }
                }
            }
        }
        vdr = VehicleDataResponse(data)
        lock_fields = [f for f in vdr.data_fields if f.name == "LOCK_STATE_LEFT_FRONT_DOOR"]
        assert len(lock_fields) == 1
        assert lock_fields[0].value == LockState.LOCKED.value


class TestVehicleInfo:
    def test_str(self):
        vi = VehicleInfo()
        vi.title = "My Audi"
        vi.model = "A4"
        vi.model_year = "2024"
        vi.vin = "WAUTEST"
        assert "My Audi" in str(vi)
        assert "WAUTEST" in str(vi)
