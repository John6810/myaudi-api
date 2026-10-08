"""Exercise retry boundaries through the real transport with mocked responses."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aioresponses import aioresponses
from tenacity import wait_none
from yarl import URL

from audi_connect.api import AudiAPI
from audi_connect.actions import AudiVehicleActions
from audi_connect.endpoints import AudiEndpoints
from audi_connect.exceptions import RequestTimeoutError
from audi_connect.vehicle import AudiVehicle


@pytest.mark.asyncio
@pytest.mark.parametrize("action,path", [
    ("unlock", "access/unlock"), ("start_climatisation", "climatisation/start"),
    ("start_preheater", "auxiliaryheating/start"),
])
async def test_non_idempotent_command_is_sent_only_once(action, path):
    async with aiohttp.ClientSession() as session:
        api = AudiAPI(session)
        actions = AudiVehicleActions(api, AudiEndpoints(api, "DE", 1),
                                     {"access_token": "fake"}, {}, "x", "DE", "1234", 1)
        auth = SimpleNamespace(
            set_vehicle_lock=actions.set_vehicle_lock,
            start_climate_control=actions.start_climate_control,
            start_preheater=actions.start_preheater,
        )
        v = AudiVehicle(auth, {"vin": "TEST"})
        url = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/TEST/" + path
        with aioresponses() as mocked:
            mocked.post(url, exception=TimeoutError("reply lost"), repeat=True)
            with pytest.raises(RequestTimeoutError):
                await getattr(v, action)()
            assert len(mocked.requests[("POST", URL(url))]) == 1


@pytest.mark.asyncio
async def test_lock_retries_at_one_layer_only(monkeypatch):
    monkeypatch.setattr(AudiVehicle.lock.retry, "wait", wait_none())
    async with aiohttp.ClientSession() as session:
        api = AudiAPI(session)
        actions = AudiVehicleActions(api, AudiEndpoints(api, "DE", 1),
                                     {"access_token": "fake"}, {}, "x", "DE", "1234", 1)
        v = AudiVehicle(SimpleNamespace(set_vehicle_lock=actions.set_vehicle_lock), {"vin": "TEST"})
        url = "https://emea.bff.cariad.digital/vehicle/v1/vehicles/TEST/access/lock"
        with aioresponses() as mocked:
            mocked.post(url, exception=TimeoutError("reply lost"), repeat=True)
            with pytest.raises(RequestTimeoutError):
                await v.lock()
            assert len(mocked.requests[("POST", URL(url))]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 429])
async def test_lock_never_retries_client_errors(status):
    error = aiohttp.ClientResponseError(None, (), status=status)
    auth = SimpleNamespace(set_vehicle_lock=AsyncMock(side_effect=error))
    v = AudiVehicle(auth, {"vin": "TEST"})
    with pytest.raises(aiohttp.ClientResponseError):
        await v.lock()
    auth.set_vehicle_lock.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_is_not_retried():
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.get("https://example.test/status", exception=asyncio.CancelledError(), repeat=True)
            with pytest.raises(asyncio.CancelledError):
                await AudiAPI(session).get("https://example.test/status")
            assert sum(map(len, mocked.requests.values())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 202, 204])
async def test_empty_success_response_is_accepted(status):
    async with aiohttp.ClientSession() as session:
        with aioresponses() as mocked:
            mocked.post("https://example.test/action", status=status, body="")
            assert await AudiAPI(session).request("POST", "https://example.test/action", data=None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", [
    "7200", format_datetime(datetime.now(timezone.utc) + timedelta(hours=2)), "invalid",
])
async def test_429_pauses_subsequent_requests(retry_after):
    async with aiohttp.ClientSession() as session:
        api = AudiAPI(session)
        with aioresponses() as mocked:
            mocked.post("https://example.test/token", status=429, headers={"Retry-After": retry_after})
            with pytest.raises(aiohttp.ClientResponseError) as exc:
                await api.request("POST", "https://example.test/token", data=None, rsp_wtxt=True)
            assert exc.value.status == 429
            assert api.retry_after >= (3599 if retry_after == "invalid" else 7190)
            with pytest.raises(aiohttp.ClientResponseError):
                await api.get("https://example.test/status")
            assert sum(map(len, mocked.requests.values())) == 1
