"""Data models for parsing Audi Connect API responses."""

import logging
from enum import Enum
from typing import Any, Optional

from .utils import get_attr

_LOGGER = logging.getLogger(__name__)


class LockState(str, Enum):
    """Door lock states from the Audi API."""
    UNKNOWN = "0"
    LOCKED = "2"
    CLOSED = "3"


class DoorState(str, Enum):
    """Door open/close states from the Audi API."""
    UNKNOWN = "0"
    CLOSED = "3"


class WindowState(str, Enum):
    """Window open/close states from the Audi API."""
    OPEN = "0"
    CLOSED = "3"


class VehicleDataResponse:
    OLDAPI_MAPPING = {
        "frontRightLock": "LOCK_STATE_RIGHT_FRONT_DOOR",
        "frontRightOpen": "OPEN_STATE_RIGHT_FRONT_DOOR",
        "frontLeftLock": "LOCK_STATE_LEFT_FRONT_DOOR",
        "frontLeftOpen": "OPEN_STATE_LEFT_FRONT_DOOR",
        "rearRightLock": "LOCK_STATE_RIGHT_REAR_DOOR",
        "rearRightOpen": "OPEN_STATE_RIGHT_REAR_DOOR",
        "rearLeftLock": "LOCK_STATE_LEFT_REAR_DOOR",
        "rearLeftOpen": "OPEN_STATE_LEFT_REAR_DOOR",
        "trunkLock": "LOCK_STATE_TRUNK_LID",
        "trunkOpen": "OPEN_STATE_TRUNK_LID",
        "bonnetLock": "LOCK_STATE_HOOD",
        "bonnetOpen": "OPEN_STATE_HOOD",
        "sunRoofWindow": "STATE_SUN_ROOF_MOTOR_COVER",
        "frontLeftWindow": "STATE_LEFT_FRONT_WINDOW",
        "frontRightWindow": "STATE_RIGHT_FRONT_WINDOW",
        "rearLeftWindow": "STATE_LEFT_REAR_WINDOW",
        "rearRightWindow": "STATE_RIGHT_REAR_WINDOW",
        "roofCoverWindow": "STATE_ROOF_COVER_WINDOW",
    }

    def __init__(self, data: dict):
        self.data_fields: list["Field"] = []
        self.states: list[dict] = []

        # Range and fuel
        self._try_append_field(data, "TOTAL_RANGE", ["fuelStatus", "rangeStatus", "value", "totalRange_km"])
        self._try_append_field(data, "TANK_LEVEL_IN_PERCENTAGE", ["measurements", "fuelLevelStatus", "value", "currentFuelLevel_pct"])
        self._try_append_field(data, "UTC_TIME_AND_KILOMETER_STATUS", ["measurements", "odometerStatus", "value", "odometer"])

        # Maintenance
        self._try_append_field(data, "MAINTENANCE_INTERVAL_TIME_TO_INSPECTION", ["vehicleHealthInspection", "maintenanceStatus", "value", "inspectionDue_days"])
        self._try_append_field(data, "MAINTENANCE_INTERVAL_DISTANCE_TO_INSPECTION", ["vehicleHealthInspection", "maintenanceStatus", "value", "inspectionDue_km"])
        self._try_append_field(data, "MAINTENANCE_INTERVAL_TIME_TO_OIL_CHANGE", ["vehicleHealthInspection", "maintenanceStatus", "value", "oilServiceDue_days"])
        self._try_append_field(data, "MAINTENANCE_INTERVAL_DISTANCE_TO_OIL_CHANGE", ["vehicleHealthInspection", "maintenanceStatus", "value", "oilServiceDue_km"])
        self._try_append_field(data, "OIL_LEVEL_DIPSTICKS_PERCENTAGE", ["oilLevel", "oilLevelStatus", "value", "value"])
        self._try_append_field(data, "ADBLUE_RANGE", ["measurements", "rangeStatus", "value", "adBlueRange"])

        # Lights
        self._try_append_field(data, "LIGHT_STATUS", ["vehicleLights", "lightsStatus", "value", "lights"])

        # Windows and doors
        self._append_window_state(data)
        self._append_door_state(data)

        # Engine info
        self._try_append_state(data, "carType", -1, ["fuelStatus", "rangeStatus", "value", "carType"])
        self._try_append_state(data, "engineTypeFirstEngine", -2, ["fuelStatus", "rangeStatus", "value", "primaryEngine", "type"])
        self._try_append_state(data, "primaryEngineRange", -2, ["fuelStatus", "rangeStatus", "value", "primaryEngine", "remainingRange_km"])
        self._try_append_state(data, "primaryEngineRangePercent", -2, ["fuelStatus", "rangeStatus", "value", "primaryEngine", "currentSOC_pct"])
        self._try_append_state(data, "engineTypeSecondEngine", -2, ["fuelStatus", "rangeStatus", "value", "secondaryEngine", "type"])
        self._try_append_state(data, "secondaryEngineRange", -2, ["fuelStatus", "rangeStatus", "value", "secondaryEngine", "remainingRange_km"])
        self._try_append_state(data, "secondaryEngineRangePercent", -2, ["fuelStatus", "rangeStatus", "value", "secondaryEngine", "currentSOC_pct"])
        self._try_append_state(data, "hybridRange", -1, ["fuelStatus", "rangeStatus", "value", "totalRange_km"])

        # Charging
        self._try_append_state(data, "stateOfCharge", -1, ["charging", "batteryStatus", "value", "currentSOC_pct"])
        self._try_append_state(data, "chargingState", -1, ["charging", "chargingStatus", "value", "chargingState"])
        self._try_append_state(data, "chargeMode", -1, ["charging", "chargingStatus", "value", "chargeMode"])
        self._try_append_state(data, "chargingPower", -1, ["charging", "chargingStatus", "value", "chargePower_kW"])
        self._try_append_state(data, "actualChargeRate", -1, ["charging", "chargingStatus", "value", "chargeRate_kmph"])
        self._try_append_state(data, "chargeType", -1, ["charging", "chargingStatus", "value", "chargeType"])
        self._try_append_state(data, "targetstateOfCharge", -1, ["charging", "chargingSettings", "value", "targetSOC_pct"])
        self._try_append_state(data, "plugState", -1, ["charging", "plugStatus", "value", "plugConnectionState"])
        self._try_append_state(data, "remainingChargingTime", -1, ["charging", "chargingStatus", "value", "remainingChargingTimeToComplete_min"])
        self._try_append_state(data, "plugLockState", -1, ["charging", "plugStatus", "value", "plugLockState"])
        self._try_append_state(data, "externalPower", -1, ["charging", "plugStatus", "value", "externalPower"])
        self._try_append_state(data, "plugledColor", -1, ["charging", "plugStatus", "value", "ledColor"])

        # Climate
        self._try_append_state(data, "climatisationState", -1, ["climatisation", "auxiliaryHeatingStatus", "value", "climatisationState"])
        self._try_append_state(data, "climatisationState", -1, ["climatisation", "climatisationStatus", "value", "climatisationState"])
        self._try_append_state(data, "remainingClimatisationTime", -1, ["climatisation", "climatisationStatus", "value", "remainingClimatisationTime_min"])

    def _get_from_json(self, json_data: dict, loc: list[str]) -> Any:
        child = json_data
        for key in loc:
            if key not in child:
                return None
            child = child[key]
        return child

    def _try_append_field(self, json_data: dict, text_id: str, loc: list[str]) -> None:
        val = self._get_from_json(json_data, loc)
        if val is not None:
            ts_loc = loc[:-1] + ["carCapturedTimestamp"]
            ts = self._get_from_json(json_data, ts_loc)
            if ts:
                self.data_fields.append(Field({
                    "textId": text_id,
                    "value": val,
                    "tsCarCaptured": ts,
                }))

    def _try_append_state(self, json_data: dict, name: str, ts_offset: int, loc: list[str]) -> None:
        val = self._get_from_json(json_data, loc)
        if val is not None:
            ts_loc = loc[:ts_offset] + ["carCapturedTimestamp"]
            ts = self._get_from_json(json_data, ts_loc)
            if ts:
                self.states.append({"name": name, "value": val, "measure_time": ts})

    def _append_door_state(self, data: dict) -> None:
        doors = get_attr(data, "access.accessStatus.value.doors", [])
        ts = get_attr(data, "access.accessStatus.value.carCapturedTimestamp")
        for door in doors:
            status = door.get("status", [])
            name = door.get("name", "")
            if name + "Lock" not in self.OLDAPI_MAPPING:
                continue
            lock = LockState.UNKNOWN.value
            open_state = DoorState.UNKNOWN.value
            unsupported = False
            for state in status:
                if state == "unsupported":
                    unsupported = True
                if state == "locked":
                    lock = LockState.LOCKED.value
                if state == "closed":
                    open_state = DoorState.CLOSED.value
            if not unsupported:
                self.data_fields.append(Field({"textId": self.OLDAPI_MAPPING[name + "Lock"], "value": lock, "tsCarCaptured": ts}))
                self.data_fields.append(Field({"textId": self.OLDAPI_MAPPING[name + "Open"], "value": open_state, "tsCarCaptured": ts}))

    def _append_window_state(self, data: dict) -> None:
        windows = get_attr(data, "access.accessStatus.value.windows", [])
        ts = get_attr(data, "access.accessStatus.value.carCapturedTimestamp")
        for window in windows:
            name = window.get("name", "")
            status = window.get("status", [])
            if not status or status[0] == "unsupported" or name + "Window" not in self.OLDAPI_MAPPING:
                continue
            self.data_fields.append(Field({
                "textId": self.OLDAPI_MAPPING[name + "Window"],
                "value": WindowState.CLOSED.value if status[0] == "closed" else WindowState.OPEN.value,
                "tsCarCaptured": ts,
            }))


class Field:
    def __init__(self, data: dict):
        self.name: Optional[str] = data.get("textId")
        self.id: Optional[str] = data.get("id")
        self.unit: Optional[str] = data.get("unit")
        self.value: Any = data.get("value")
        self.measure_time: Any = data.get("tsTssReceivedUtc") or data.get("tsCarCaptured")
        self.send_time: Any = data.get("tsCarSentUtc")

    def __str__(self):
        s = f"{self.name}: {self.value}"
        if self.unit:
            s += self.unit
        return s


class TripDataResponse:
    def __init__(self, data: dict):
        self.trip_id: Optional[str] = data.get("tripID")
        self.average_electric_consumption: Optional[float] = (
            float(data["averageElectricEngineConsumption"]) / 10
            if "averageElectricEngineConsumption" in data else None
        )
        self.average_fuel_consumption: Optional[float] = (
            float(data["averageFuelConsumption"]) / 10
            if "averageFuelConsumption" in data else None
        )
        self.average_speed: Optional[int] = int(data["averageSpeed"]) if "averageSpeed" in data else None
        self.mileage: Optional[int] = int(data["mileage"]) if "mileage" in data else None
        self.start_mileage: Optional[int] = int(data["startMileage"]) if "startMileage" in data else None
        self.travel_time: Optional[int] = int(data["traveltime"]) if "traveltime" in data else None
        self.timestamp: Any = data.get("timestamp")
        self.overall_mileage: Optional[int] = int(data["overallMileage"]) if "overallMileage" in data else None
        self.zero_emission_distance: Optional[int] = int(data["zeroEmissionDistance"]) if "zeroEmissionDistance" in data else None


class VehiclesResponse:
    def __init__(self):
        self.vehicles: list["VehicleInfo"] = []

    def parse(self, data: dict) -> None:
        user_vehicles = data.get("userVehicles")
        if user_vehicles is None:
            return
        for item in user_vehicles:
            vehicle = VehicleInfo()
            vehicle.parse(item)
            self.vehicles.append(vehicle)


class VehicleInfo:
    def __init__(self):
        self.vin: str = ""
        self.csid: str = ""
        self.model: str = ""
        self.model_year: str = ""
        self.title: str = ""

    def parse(self, data: dict) -> None:
        self.vin = data.get("vin")
        self.csid = data.get("csid")
        if data.get("vehicle") and data["vehicle"].get("media"):
            self.model = data["vehicle"]["media"].get("longName", "")
        if data.get("vehicle") and data["vehicle"].get("core"):
            self.model_year = data["vehicle"]["core"].get("modelYear", "")
        if data.get("nickname") and len(data["nickname"]) > 0:
            self.title = data["nickname"]
        elif data.get("vehicle") and data["vehicle"].get("media"):
            self.title = data["vehicle"]["media"].get("shortName", "")

    def __str__(self):
        return f"{self.title} ({self.model} {self.model_year}) - VIN: {self.vin}"
