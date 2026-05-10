"""Vehicle data client - fetches vehicle status, position, and trip data from Audi/VW APIs."""

import json
import logging
from datetime import timedelta, datetime, timezone
from typing import Optional

from .api import AudiAPI
from .endpoints import AudiEndpoints
from .exceptions import AuthenticationError

_LOGGER = logging.getLogger(__name__)


class AudiVehicleClient:
    """Handles all read-only vehicle data API calls."""

    def __init__(
        self,
        api: AudiAPI,
        endpoints: AudiEndpoints,
        bearer_token: dict,
        vw_token: dict,
        audi_token: dict,
        xclient_id: str,
        country: str,
        language: str,
        api_level: int,
    ):
        self._api = api
        self._endpoints = endpoints
        self._bearer_token = bearer_token
        self._vw_token = vw_token
        self._audi_token = audi_token
        self._xclient_id = xclient_id
        self._country = country
        self._language = language
        self._type = "Audi"
        self._api_level = api_level

    async def get_vehicle_list(self) -> list[dict]:
        """Fetch the list of vehicles from the GraphQL API."""
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Name": "myAudi",
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "Accept-Language": f"{self._language}-{self._country.upper()}",
            "X-User-Country": self._country.upper(),
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Authorization": "Bearer " + self._audi_token["access_token"],
            "Content-Type": "application/json; charset=utf-8",
        }
        graphql_query = {
            "query": (
                "query vehicleList {\n userVehicles {\n vin\n mappingVin\n "
                "vehicle { core { modelYear\n }\n media { shortName\n longName }\n }\n "
                "csid\n commissionNumber\n type\n devicePlatform\n mbbConnect\n "
                "userRole {\n role\n }\n vehicle {\n classification {\n driveTrain\n }\n }\n "
                "nickname\n }\n}"
            )
        }
        graphql_url = (
            "https://app-api.my.aoa.audi.com/vgql/v1/graphql"
            if self._country.upper() == "US"
            else "https://app-api.live-my.audi.com/vgql/v1/graphql"
        )

        _, rsptxt = await self._api.request(
            "POST", graphql_url, json.dumps(graphql_query),
            headers=headers, allow_redirects=False, rsp_wtxt=True,
        )
        vins = json.loads(rsptxt)

        if "errors" in vins:
            raise AuthenticationError(f"GraphQL API returned errors: {vins['errors']}")
        if "data" not in vins or vins["data"] is None:
            raise AuthenticationError("No data in API response")
        if vins["data"].get("userVehicles") is None:
            raise AuthenticationError("No vehicle data - possible authentication issue")

        return vins["data"]["userVehicles"]

    async def get_stored_vehicle_data(self, vin: str) -> dict:
        """Fetch selective vehicle status data from CARIAD API."""
        jobs = {
            "access", "activeVentilation", "auxiliaryHeating", "batteryChargingCare",
            "batterySupport", "charging", "chargingProfiles", "chargingTimers",
            "climatisation", "climatisationTimers", "departureProfiles",
            "departureTimers", "fuelStatus", "honkAndFlash",
            "hybridCarAuxiliaryHeating", "lvBattery", "measurements", "oilLevel",
            "readiness", "vehicleHealthInspection", "vehicleHealthWarnings",
            "vehicleLights",
        }
        self._api.use_token(self._bearer_token)
        return await self._api.get(
            self._endpoints.cariad_url_for_vin(
                vin, "selectivestatus?jobs={jobs}", jobs=",".join(jobs)
            )
        )

    async def get_stored_position(self, vin: str) -> Optional[dict]:
        """Fetch vehicle parking position."""
        self._api.use_token(self._bearer_token)
        try:
            return await self._api.get(
                self._endpoints.cariad_url_for_vin(vin, "parkingposition")
            )
        except Exception:
            return None

    async def get_tripdata(self, vin: str, kind: str) -> dict:
        """Fetch trip statistics (short-term or long-term)."""
        self._api.use_token(self._vw_token)
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Name": "myAudi",
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "X-Client-ID": self._xclient_id,
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Authorization": "Bearer " + self._vw_token["access_token"],
        }
        td_reqdata = {
            "type": "list",
            "from": "1970-01-01T00:00:00Z",
            "to": (datetime.now(timezone.utc) + timedelta(minutes=90)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        }
        return await self._api.request(
            "GET",
            "{home}/api/bs/tripstatistics/v1/vehicles/{vin}/tripdata/{kind}".format(
                home=await self._endpoints.home_region_setter(vin.upper()),
                vin=vin.upper(), kind=kind,
            ),
            None, params=td_reqdata, headers=headers,
        )
