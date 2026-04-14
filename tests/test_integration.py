"""Integration tests using aioresponses to mock the HTTP layer.

These tests exercise the real code path from AudiVehicleClient/AudiVehicleActions
through AudiAPI down to the HTTP layer, with only the network mocked.
"""

import json
import pytest
import pytest_asyncio
import aiohttp
from aioresponses import aioresponses

from audi_connect.api import AudiAPI
from audi_connect.client import AudiVehicleClient
from audi_connect.actions import AudiVehicleActions
from audi_connect.exceptions import RequestTimeoutError


@pytest_asyncio.fixture
async def session():
    """Create a real aiohttp session (requests will be intercepted by aioresponses)."""
    s = aiohttp.ClientSession()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def api(session):
    a = AudiAPI(session)
    a.use_token({"access_token": "test_bearer"})
    a.set_xclient_id("test_xclient")
    return a


@pytest_asyncio.fixture
async def client(api):
    return AudiVehicleClient(
        api=api,
        bearer_token={"access_token": "test_bearer"},
        vw_token={"access_token": "test_vw"},
        audi_token={"access_token": "test_audi"},
        xclient_id="test_xclient",
        country="DE",
        language="de",
        api_level=1,
    )


@pytest_asyncio.fixture
async def actions(api, client):
    return AudiVehicleActions(
        api=api,
        client=client,
        bearer_token={"access_token": "test_bearer"},
        vw_token={"access_token": "test_vw"},
        xclient_id="test_xclient",
        country="DE",
        spin="1234",
        api_level=1,
    )


class TestClientIntegration:
    @pytest.mark.asyncio
    async def test_get_vehicle_list(self, client):
        graphql_response = {
            "data": {
                "userVehicles": [
                    {
                        "vin": "WAUINTEGRATION01",
                        "csid": "cs1",
                        "nickname": "Test Car",
                        "vehicle": {
                            "media": {"shortName": "A4", "longName": "Audi A4"},
                            "core": {"modelYear": "2024"},
                        },
                    }
                ]
            }
        }

        with aioresponses() as m:
            m.post(
                "https://app-api.live-my.audi.com/vgql/v1/graphql",
                payload=graphql_response,
            )
            vehicles = await client.get_vehicle_list()

        assert len(vehicles) == 1
        assert vehicles[0]["vin"] == "WAUINTEGRATION01"
        assert vehicles[0]["nickname"] == "Test Car"

    @pytest.mark.asyncio
    async def test_get_stored_vehicle_data(self, client):
        import re
        vehicle_data = {
            "fuelStatus": {
                "rangeStatus": {
                    "value": {
                        "totalRange_km": 450,
                        "carType": "hybrid",
                        "carCapturedTimestamp": "2024-06-15T10:00:00Z",
                    }
                }
            }
        }

        with aioresponses() as m:
            # URL contains query params with comma-separated jobs that get encoded
            m.get(
                re.compile(r"https://emea\.bff\.cariad\.digital/vehicle/v1/vehicles/WAUTEST/selectivestatus.*"),
                payload=vehicle_data,
            )
            result = await client.get_stored_vehicle_data("wautest")

        assert result["fuelStatus"]["rangeStatus"]["value"]["totalRange_km"] == 450

    @pytest.mark.asyncio
    async def test_get_stored_position(self, client):
        position_data = {
            "data": {
                "lat": 50.8503,
                "lon": 4.3517,
                "carCapturedTimestamp": "2024-06-15T10:00:00Z",
            }
        }

        with aioresponses() as m:
            m.get(
                "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/parkingposition",
                payload=position_data,
            )
            result = await client.get_stored_position("wautest")

        assert result["data"]["lat"] == 50.8503

    @pytest.mark.asyncio
    async def test_get_stored_position_returns_none_on_error(self, client):
        with aioresponses() as m:
            m.get(
                "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/parkingposition",
                status=404,
            )
            result = await client.get_stored_position("wautest")

        assert result is None


class TestActionsIntegration:
    @pytest.mark.asyncio
    async def test_start_climate_cariad(self, actions):
        with aioresponses() as m:
            m.post(
                "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/climatisation/start",
                payload={"status": "accepted"},
            )
            await actions.start_climate_control("wautest", temp_c=22.0)

        # Verify the request was made
        assert len(m.requests) == 1

    @pytest.mark.asyncio
    async def test_stop_climate_cariad(self, actions):
        with aioresponses() as m:
            m.post(
                "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/climatisation/stop",
                payload={"status": "accepted"},
            )
            await actions.stop_climate_control("wautest")

    @pytest.mark.asyncio
    async def test_start_preheater(self, actions):
        with aioresponses() as m:
            m.post(
                "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/auxiliaryheating/start",
                payload={"status": "accepted"},
            )
            await actions.start_preheater("wautest", duration=30)

    @pytest.mark.asyncio
    async def test_stop_preheater(self, actions):
        with aioresponses() as m:
            m.post(
                "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/auxiliaryheating/stop",
                payload={"status": "accepted"},
            )
            await actions.stop_preheater("wautest")


class TestAPIRetry:
    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, api):
        """Verify that the low-level API retries on timeout."""
        with aioresponses() as m:
            # First call times out, second succeeds
            m.get("https://example.com/test", exception=TimeoutError("timeout"))
            m.get("https://example.com/test", payload={"ok": True})

            result = await api.get("https://example.com/test")

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self, api):
        """Verify retry on connection errors."""
        with aioresponses() as m:
            m.get("https://example.com/test", exception=ConnectionError("refused"))
            m.get("https://example.com/test", payload={"recovered": True})

            result = await api.get("https://example.com/test")

        assert result["recovered"] is True
