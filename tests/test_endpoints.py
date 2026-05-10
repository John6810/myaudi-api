"""Tests for audi_connect.endpoints — URL building and home-region cache."""

from unittest.mock import AsyncMock

import pytest

from audi_connect.endpoints import AudiEndpoints, cariad_url


def test_cariad_url_eu_country_uses_emea_region():
    url = cariad_url("DE", "/login/v1/idk/openid-configuration")
    assert url.startswith("https://emea.bff.cariad.digital/")


def test_cariad_url_us_country_uses_na_region():
    url = cariad_url("US", "/login/v1/idk/openid-configuration")
    assert url.startswith("https://na.bff.cariad.digital/")


def test_cariad_url_for_vin_uppercases_vin():
    api = AsyncMock()
    ep = AudiEndpoints(api, country="DE", api_level=1)
    url = ep.cariad_url_for_vin("wautest1234567890", "selectivestatus")
    assert "/vehicles/WAUTEST1234567890/" in url
    assert url.endswith("/selectivestatus")


@pytest.mark.asyncio
async def test_home_region_eu_api1_returns_static_without_api_call():
    api = AsyncMock()
    ep = AudiEndpoints(api, country="DE", api_level=1)
    home = await ep.home_region("WAUTEST")
    assert home == "https://mal-3a.prd.eu.dp.vwg-connect.com"
    # Static path must not hit the network.
    api.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_home_region_caches_per_vin():
    # Force the legacy path so _fill calls api.get; verify second call is cached.
    api = AsyncMock()
    api.get = AsyncMock(return_value={
        "homeRegion": {"baseUri": {"content": "https://mal-3a.prd.eu.dp.vwg-connect.com/api"}}
    })
    ep = AudiEndpoints(api, country="US", api_level=0)  # legacy path triggers _fill

    await ep.home_region("WAUTEST")
    await ep.home_region("WAUTEST")  # second call must hit cache, not the API
    assert api.get.await_count == 1
