"""Vehicle representation with properties and remote actions."""

import asyncio
import logging
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from aiohttp import ClientResponseError

from .auth import AudiAuth
from .exceptions import ActionFailedError, RequestTimeoutError
from .models import VehicleDataResponse, TripDataResponse, LockState, DoorState, WindowState
from .utils import parse_int, parse_float

_LOGGER = logging.getLogger(__name__)

# Retry config for vehicle actions (network errors only, not validation errors)
_action_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((RequestTimeoutError, ConnectionError, OSError, ClientResponseError)),
    reraise=True,
    before_sleep=lambda rs: _LOGGER.warning("Action failed, retrying (attempt %d)...", rs.attempt_number),
)

# Validation constants
MIN_CLIMATE_TEMP_C = 16.0
MAX_CLIMATE_TEMP_C = 30.0
MIN_HEATER_DURATION_MIN = 10
MAX_HEATER_DURATION_MIN = 60


class AudiVehicle:
    def __init__(self, auth: AudiAuth, vehicle_info: dict):
        self._auth = auth
        self.vin: str = vehicle_info.get("vin", "")
        self.csid: str = vehicle_info.get("csid", "")
        self.title: str = ""
        self.model: str = ""
        self.model_year: str = ""

        # Parse vehicle info
        if vehicle_info.get("nickname"):
            self.title = vehicle_info["nickname"]
        elif vehicle_info.get("vehicle", {}).get("media", {}).get("shortName"):
            self.title = vehicle_info["vehicle"]["media"]["shortName"]

        if vehicle_info.get("vehicle", {}).get("media", {}).get("longName"):
            self.model = vehicle_info["vehicle"]["media"]["longName"]

        if vehicle_info.get("vehicle", {}).get("core", {}).get("modelYear"):
            self.model_year = vehicle_info["vehicle"]["core"]["modelYear"]

        # Data storage
        self._vehicle_data: Optional[VehicleDataResponse] = None
        self._position: Optional[dict] = None
        self._position_failed: bool = False
        self._trip_shortterm: Optional[TripDataResponse] = None
        self._trip_longterm: Optional[TripDataResponse] = None

    async def update(self) -> None:
        """Fetch all vehicle data from the API in parallel."""
        _LOGGER.info("Updating data for %s (%s)...", self.title or self.vin, self.vin[-4:])

        results = await asyncio.gather(
            self._fetch_vehicle_data(),
            self._fetch_position(),
            self._fetch_trip("shortTerm"),
            self._fetch_trip("longTerm"),
            return_exceptions=True,
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                _LOGGER.error("Fetch task %d failed: %s", i, result)

    async def _fetch_vehicle_data(self) -> None:
        try:
            raw_data = await self._auth.get_stored_vehicle_data(self.vin)
            self._vehicle_data = VehicleDataResponse(raw_data)
        except Exception as e:
            _LOGGER.error("Failed to get vehicle data: %s", e)

    async def _fetch_position(self) -> None:
        try:
            self._position = await self._auth.get_stored_position(self.vin)
            self._position_failed = False
        except Exception as e:
            _LOGGER.debug("Failed to get position (may be moving): %s", e)
            self._position = None
            self._position_failed = True

    async def _fetch_trip(self, kind: str) -> None:
        try:
            trip_data = await self._auth.get_tripdata(self.vin, kind)
            trip_list = trip_data.get("tripDataList", {}).get("tripData", [])
            td_sorted = sorted(trip_list, key=lambda k: k.get("overallMileage", 0), reverse=True)
            if td_sorted:
                if kind == "shortTerm":
                    self._trip_shortterm = TripDataResponse(td_sorted[0])
                else:
                    self._trip_longterm = TripDataResponse(td_sorted[0])
        except Exception as e:
            _LOGGER.debug("Failed to get %s trip data: %s", kind, e)

    def _get_field(self, name: str) -> Optional[object]:
        if self._vehicle_data is None:
            return None
        for field in self._vehicle_data.data_fields:
            if field.name == name:
                return field
        return None

    def _get_state(self, name: str) -> Optional[dict]:
        if self._vehicle_data is None:
            return None
        for state in self._vehicle_data.states:
            if state["name"] == name:
                return state
        return None

    # --- Vehicle info ---

    @property
    def mileage(self) -> Optional[int]:
        field = self._get_field("UTC_TIME_AND_KILOMETER_STATUS")
        return parse_int(field.value) if field else None

    @property
    def range_km(self) -> Optional[int]:
        field = self._get_field("TOTAL_RANGE")
        return parse_int(field.value) if field else None

    @property
    def tank_level(self) -> Optional[int]:
        field = self._get_field("TANK_LEVEL_IN_PERCENTAGE")
        return parse_int(field.value) if field else None

    @property
    def oil_level(self) -> Optional[int]:
        field = self._get_field("OIL_LEVEL_DIPSTICKS_PERCENTAGE")
        return parse_int(field.value) if field else None

    @property
    def adblue_range(self) -> Optional[int]:
        field = self._get_field("ADBLUE_RANGE")
        return parse_int(field.value) if field else None

    # --- Position ---

    @property
    def position(self) -> Optional[dict]:
        if self._position and "data" in self._position:
            data = self._position["data"]
            return {
                "latitude": data.get("lat"),
                "longitude": data.get("lon"),
                "timestamp": data.get("carCapturedTimestamp"),
            }
        elif self._position:
            return {
                "latitude": self._position.get("lat"),
                "longitude": self._position.get("lon"),
                "timestamp": self._position.get("carCapturedTimestamp"),
            }
        return None

    @property
    def is_moving(self) -> bool:
        """True only when position is unavailable and the fetch didn't error out."""
        return self._position is None and not self._position_failed

    # --- Doors and locks ---

    def _is_door_unlocked(self, field_name: str) -> bool:
        field = self._get_field(field_name)
        return field is not None and field.value != LockState.LOCKED.value

    def _is_door_open(self, field_name: str) -> bool:
        field = self._get_field(field_name)
        return field is not None and field.value != DoorState.CLOSED.value

    @property
    def any_door_unlocked(self) -> bool:
        doors = [
            "LOCK_STATE_LEFT_FRONT_DOOR", "LOCK_STATE_LEFT_REAR_DOOR",
            "LOCK_STATE_RIGHT_FRONT_DOOR", "LOCK_STATE_RIGHT_REAR_DOOR",
        ]
        return any(self._is_door_unlocked(d) for d in doors)

    @property
    def any_door_open(self) -> bool:
        doors = [
            "OPEN_STATE_LEFT_FRONT_DOOR", "OPEN_STATE_LEFT_REAR_DOOR",
            "OPEN_STATE_RIGHT_FRONT_DOOR", "OPEN_STATE_RIGHT_REAR_DOOR",
        ]
        return any(self._is_door_open(d) for d in doors)

    @property
    def trunk_unlocked(self) -> bool:
        return self._is_door_unlocked("LOCK_STATE_TRUNK_LID")

    @property
    def trunk_open(self) -> bool:
        return self._is_door_open("OPEN_STATE_TRUNK_LID")

    @property
    def hood_open(self) -> bool:
        return self._is_door_open("OPEN_STATE_HOOD")

    @property
    def doors_trunk_status(self) -> str:
        if self.any_door_open or self.trunk_open:
            return "Open"
        if self.any_door_unlocked or self.trunk_unlocked:
            return "Closed"
        return "Locked"

    # --- Windows ---

    def _is_window_open(self, field_name: str) -> bool:
        field = self._get_field(field_name)
        return field is not None and field.value != WindowState.CLOSED.value

    @property
    def any_window_open(self) -> bool:
        windows = [
            "STATE_LEFT_FRONT_WINDOW", "STATE_LEFT_REAR_WINDOW",
            "STATE_RIGHT_FRONT_WINDOW", "STATE_RIGHT_REAR_WINDOW",
        ]
        return any(self._is_window_open(w) for w in windows)

    # --- Maintenance ---

    @property
    def service_inspection_days(self) -> Optional[int]:
        field = self._get_field("MAINTENANCE_INTERVAL_TIME_TO_INSPECTION")
        return parse_int(field.value) if field else None

    @property
    def service_inspection_km(self) -> Optional[int]:
        field = self._get_field("MAINTENANCE_INTERVAL_DISTANCE_TO_INSPECTION")
        return parse_int(field.value) if field else None

    @property
    def oil_change_days(self) -> Optional[int]:
        field = self._get_field("MAINTENANCE_INTERVAL_TIME_TO_OIL_CHANGE")
        return parse_int(field.value) if field else None

    @property
    def oil_change_km(self) -> Optional[int]:
        field = self._get_field("MAINTENANCE_INTERVAL_DISTANCE_TO_OIL_CHANGE")
        return parse_int(field.value) if field else None

    # --- Engine / Range ---

    @property
    def car_type(self) -> Optional[str]:
        state = self._get_state("carType")
        return state["value"] if state else None

    @property
    def primary_engine_type(self) -> Optional[str]:
        state = self._get_state("engineTypeFirstEngine")
        return state["value"] if state else None

    @property
    def primary_engine_range(self) -> Optional[int]:
        state = self._get_state("primaryEngineRange")
        return parse_int(state["value"]) if state else None

    @property
    def secondary_engine_type(self) -> Optional[str]:
        state = self._get_state("engineTypeSecondEngine")
        return state["value"] if state else None

    @property
    def secondary_engine_range(self) -> Optional[int]:
        state = self._get_state("secondaryEngineRange")
        return parse_int(state["value"]) if state else None

    # --- Charging (EV/PHEV) ---

    @property
    def state_of_charge(self) -> Optional[int]:
        state = self._get_state("stateOfCharge")
        return parse_int(state["value"]) if state else None

    @property
    def charging_state(self) -> Optional[str]:
        state = self._get_state("chargingState")
        return state["value"] if state else None

    @property
    def charging_mode(self) -> Optional[str]:
        state = self._get_state("chargeMode")
        return state["value"] if state else None

    @property
    def charging_power(self) -> Optional[float]:
        state = self._get_state("chargingPower")
        return parse_float(state["value"]) if state else None

    @property
    def remaining_charging_time(self) -> Optional[int]:
        state = self._get_state("remainingChargingTime")
        return parse_int(state["value"]) if state else None

    @property
    def plug_state(self) -> Optional[str]:
        state = self._get_state("plugState")
        return state["value"] if state else None

    @property
    def plug_lock_state(self) -> Optional[str]:
        state = self._get_state("plugLockState")
        return state["value"] if state else None

    # --- Climate ---

    @property
    def climatisation_state(self) -> Optional[str]:
        state = self._get_state("climatisationState")
        return state["value"] if state else None

    # --- Trip data ---

    @property
    def trip_shortterm(self) -> Optional[TripDataResponse]:
        return self._trip_shortterm

    @property
    def trip_longterm(self) -> Optional[TripDataResponse]:
        return self._trip_longterm

    # --- Actions ---

    @_action_retry
    async def lock(self) -> None:
        await self._auth.set_vehicle_lock(self.vin, lock=True)

    @_action_retry
    async def unlock(self) -> None:
        await self._auth.set_vehicle_lock(self.vin, lock=False)

    async def start_climatisation(self, temp_c: float = 21.0) -> None:
        if not MIN_CLIMATE_TEMP_C <= temp_c <= MAX_CLIMATE_TEMP_C:
            raise ActionFailedError(
                f"Temperature must be between {MIN_CLIMATE_TEMP_C} and {MAX_CLIMATE_TEMP_C}°C, got {temp_c}"
            )
        await self._do_start_climatisation(temp_c)

    @_action_retry
    async def _do_start_climatisation(self, temp_c: float) -> None:
        await self._auth.start_climate_control(self.vin, temp_c=temp_c)

    @_action_retry
    async def stop_climatisation(self) -> None:
        await self._auth.stop_climate_control(self.vin)

    async def start_preheater(self, duration: int = 30) -> None:
        if not MIN_HEATER_DURATION_MIN <= duration <= MAX_HEATER_DURATION_MIN:
            raise ActionFailedError(
                f"Duration must be between {MIN_HEATER_DURATION_MIN} and {MAX_HEATER_DURATION_MIN} min, got {duration}"
            )
        await self._do_start_preheater(duration)

    @_action_retry
    async def _do_start_preheater(self, duration: int) -> None:
        await self._auth.start_preheater(self.vin, duration=duration)

    @_action_retry
    async def stop_preheater(self) -> None:
        await self._auth.stop_preheater(self.vin)

    def get_brief(self) -> dict[str, str]:
        """Return only the 3 most important fields: locked status, position, range."""
        data: dict[str, str] = {}
        data["vehicle"] = f"{self.title or self.vin}"
        data["locked"] = self.doors_trunk_status

        pos = self.position
        if pos and pos.get("latitude"):
            data["position"] = f"{pos['latitude']}, {pos['longitude']}"
            data["maps"] = f"https://www.google.com/maps?q={pos['latitude']},{pos['longitude']}"
        elif self.is_moving:
            data["position"] = "Vehicle is moving"
        else:
            data["position"] = "Unknown"

        if self.range_km is not None:
            data["range"] = f"{self.range_km} km"
        if self.state_of_charge is not None:
            data["battery"] = f"{self.state_of_charge}%"
        elif self.tank_level is not None:
            data["fuel"] = f"{self.tank_level}%"

        return data

    def get_dashboard(self) -> dict[str, str]:
        """Return a dict of human-readable vehicle status fields."""
        data: dict[str, str] = {}
        data["vehicle"] = f"{self.title} - {self.model} ({self.model_year})"
        data["vin"] = self.vin

        if self.mileage is not None:
            data["mileage"] = f"{self.mileage:,} km"
        if self.range_km is not None:
            data["range"] = f"{self.range_km} km"
        if self.tank_level is not None:
            data["fuel_level"] = f"{self.tank_level}%"
        if self.oil_level is not None:
            data["oil_level"] = f"{self.oil_level}%"
        if self.adblue_range is not None:
            data["adblue_range"] = f"{self.adblue_range} km"

        pos = self.position
        if pos and pos.get("latitude"):
            data["position"] = f"{pos['latitude']}, {pos['longitude']}"
        elif self.is_moving:
            data["position"] = "Vehicle is moving"

        data["doors_trunk"] = self.doors_trunk_status
        data["windows"] = "Open" if self.any_window_open else "Closed"
        if self.hood_open:
            data["hood"] = "Open"

        if self.primary_engine_type:
            data["primary_engine"] = f"{self.primary_engine_type}"
            if self.primary_engine_range is not None:
                data["primary_engine"] += f" ({self.primary_engine_range} km)"
        if self.secondary_engine_type:
            data["secondary_engine"] = f"{self.secondary_engine_type}"
            if self.secondary_engine_range is not None:
                data["secondary_engine"] += f" ({self.secondary_engine_range} km)"

        if self.state_of_charge is not None:
            data["battery_soc"] = f"{self.state_of_charge}%"
        if self.charging_state:
            data["charging"] = self.charging_state
        if self.charging_power:
            data["charging_power"] = f"{self.charging_power} kW"
        if self.remaining_charging_time:
            hours = self.remaining_charging_time // 60
            minutes = self.remaining_charging_time % 60
            data["charging_remaining"] = f"{hours}h {minutes}min"
        if self.plug_state:
            data["plug"] = self.plug_state

        if self.service_inspection_days is not None and self.service_inspection_km is not None:
            data["inspection_due"] = f"{self.service_inspection_days} days / {self.service_inspection_km} km"
        if self.oil_change_days is not None and self.oil_change_km is not None:
            data["oil_change_due"] = f"{self.oil_change_days} days / {self.oil_change_km} km"

        if self.climatisation_state:
            data["climatisation"] = self.climatisation_state

        if self._trip_shortterm:
            trip = self._trip_shortterm
            parts = []
            if trip.average_speed is not None:
                parts.append(f"avg {trip.average_speed} km/h")
            if trip.average_fuel_consumption is not None:
                parts.append(f"{trip.average_fuel_consumption} L/100km")
            if trip.mileage is not None:
                parts.append(f"{trip.mileage} km")
            if parts:
                data["trip_short"] = " | ".join(parts)

        return data
