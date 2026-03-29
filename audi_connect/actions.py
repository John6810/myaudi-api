"""Vehicle actions - lock, unlock, climate control, preheater, charge mode."""

import json
import logging
from hashlib import sha512
from typing import Optional

from .api import AudiAPI
from .client import AudiVehicleClient
from .exceptions import SpinRequiredError
from .utils import to_byte_array

_LOGGER = logging.getLogger(__name__)


class AudiVehicleActions:
    """Handles all write/action vehicle API calls."""

    def __init__(
        self,
        api: AudiAPI,
        client: AudiVehicleClient,
        bearer_token: dict,
        vw_token: dict,
        xclient_id: str,
        country: str,
        spin: Optional[str],
        api_level: int,
    ):
        self._api = api
        self._client = client
        self._bearer_token = bearer_token
        self._vw_token = vw_token
        self._xclient_id = xclient_id
        self._country = country
        self._type = "Audi"
        self._spin = spin
        self._api_level = api_level

    async def set_vehicle_lock(self, vin: str, lock: bool) -> None:
        """Lock or unlock the vehicle. Requires S-PIN."""
        security_token = await self._get_security_token(
            vin, "rlu_v1/operations/" + ("LOCK" if lock else "UNLOCK")
        )
        headers = self._get_vehicle_action_header(
            "application/vnd.vwg.mbb.RemoteLockUnlock_v1_0_0+xml", security_token
        )
        await self._api.request(
            "POST",
            "{home}/api/bs/rlu/v1/vehicles/{vin}/{action}".format(
                home=await self._client._get_home_region_setter(vin.upper()),
                vin=vin.upper(),
                action="lock" if lock else "unlock",
            ),
            headers=headers, data=None,
        )

    async def start_climate_control(self, vin: str, temp_c: float = 21.0) -> None:
        """Start climate control at the given temperature."""
        if self._api_level == 1:
            data = json.dumps({
                "climatisationMode": "comfort",
                "targetTemperature": int(temp_c),
                "targetTemperatureUnit": "celsius",
                "climatisationWithoutExternalPower": True,
                "climatizationAtUnlock": False,
                "windowHeatingEnabled": False,
                "zoneFrontLeftEnabled": True,
                "zoneFrontRightEnabled": True,
                "zoneRearLeftEnabled": False,
                "zoneRearRightEnabled": False,
            })
            headers = {"Authorization": "Bearer " + self._bearer_token["access_token"]}
            await self._api.request(
                "POST",
                self._client._get_cariad_url_for_vin(vin, "climatisation/start"),
                headers=headers, data=data,
            )
        else:
            # Legacy MBB API expects temperature in deciKelvin:
            # Celsius → Kelvin: +273.15, then ×10 for deciKelvin (e.g. 21°C → 2941)
            target_temp = int(temp_c * 10 + 2731)
            data = json.dumps({
                "action": {
                    "type": "startClimatisation",
                    "settings": {
                        "targetTemperature": target_temp,
                        "climatisationWithoutHVpower": True,
                        "heaterSource": "electric",
                        "climaterElementSettings": {
                            "isClimatisationAtUnlock": False,
                            "isMirrorHeatingEnabled": False,
                        },
                    },
                }
            })
            headers = self._get_vehicle_action_header("application/json", None)
            await self._api.request(
                "POST",
                "{home}/fs-car/bs/climatisation/v1/{type}/{country}/vehicles/{vin}/climater/actions".format(
                    home=await self._client._get_home_region(vin.upper()),
                    type=self._type, country=self._country, vin=vin.upper(),
                ),
                headers=headers, data=data,
            )

    async def stop_climate_control(self, vin: str) -> None:
        """Stop climate control."""
        if self._api_level == 1:
            headers = {"Authorization": "Bearer " + self._bearer_token["access_token"]}
            await self._api.request(
                "POST",
                self._client._get_cariad_url_for_vin(vin, "climatisation/stop"),
                headers=headers, data=None,
            )
        else:
            data = '{"action":{"type": "stopClimatisation"}}'
            headers = self._get_vehicle_action_header("application/json", None)
            await self._api.request(
                "POST",
                "{home}/fs-car/bs/climatisation/v1/{type}/{country}/vehicles/{vin}/climater/actions".format(
                    home=await self._client._get_home_region(vin.upper()),
                    type=self._type, country=self._country, vin=vin.upper(),
                ),
                headers=headers, data=data,
            )

    async def set_charge_mode(self, vin: str, timer: bool = False) -> None:
        """Set charge mode to timer or manual."""
        data = json.dumps({"preferredChargeMode": "timer" if timer else "manual"})
        headers = {"Authorization": "Bearer " + self._bearer_token["access_token"]}
        await self._api.request(
            "PUT",
            self._client._get_cariad_url_for_vin(vin, "charging/mode"),
            headers=headers, data=data,
        )

    async def start_preheater(self, vin: str, duration: int = 30) -> None:
        """Start auxiliary heater for the given duration (minutes)."""
        data = json.dumps({"duration_min": duration, "spin": self._spin})
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._bearer_token["access_token"],
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
        }
        await self._api.request(
            "POST",
            self._client._get_cariad_url_for_vin(vin, "auxiliaryheating/start"),
            headers=headers, data=data,
        )

    async def stop_preheater(self, vin: str) -> None:
        """Stop auxiliary heater."""
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._bearer_token["access_token"],
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
        }
        await self._api.request(
            "POST",
            self._client._get_cariad_url_for_vin(vin, "auxiliaryheating/stop"),
            headers=headers, data=None,
        )

    # --- Security helpers ---

    async def _get_security_token(self, vin: str, action: str) -> str:
        headers = {
            "User-Agent": "okhttp/3.7.0",
            "X-App-Version": "3.14.0",
            "X-App-Name": "myAudi",
            "Accept": "application/json",
            "Authorization": "Bearer " + self._vw_token.get("access_token"),
        }
        body = await self._api.request(
            "GET",
            "{home}/api/rolesrights/authorization/v2/vehicles/{vin}/services/{action}/security-pin-auth-requested".format(
                home=await self._client._get_home_region_setter(vin.upper()),
                vin=vin.upper(), action=action,
            ),
            headers=headers, data=None,
        )
        sec_token = body["securityPinAuthInfo"]["securityToken"]
        challenge = body["securityPinAuthInfo"]["securityPinTransmission"]["challenge"]

        pin_hash = self._generate_security_pin_hash(challenge)
        data = {
            "securityPinAuthentication": {
                "securityPin": {
                    "challenge": challenge,
                    "securityPinHash": pin_hash,
                },
                "securityToken": sec_token,
            }
        }
        headers["Content-Type"] = "application/json"
        body = await self._api.request(
            "POST",
            "{home}/api/rolesrights/authorization/v2/security-pin-auth-completed".format(
                home=await self._client._get_home_region_setter(vin.upper())
            ),
            headers=headers, data=json.dumps(data),
        )
        return body["securityToken"]

    def _generate_security_pin_hash(self, challenge: str) -> str:
        if self._spin is None:
            raise SpinRequiredError("S-PIN is required for this action (lock/unlock)")
        pin = to_byte_array(self._spin)
        byte_challenge = to_byte_array(challenge)
        b = bytes(pin + byte_challenge)
        return sha512(b).hexdigest().upper()

    def _get_vehicle_action_header(self, content_type: str, security_token: Optional[str]) -> dict:
        headers = {
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "X-App-Name": "myAudi",
            "Authorization": "Bearer " + self._vw_token.get("access_token"),
            "Accept-charset": "UTF-8",
            "Content-Type": content_type,
            "Accept": "application/json",
        }
        if security_token:
            headers["x-securityToken"] = security_token
        return headers
