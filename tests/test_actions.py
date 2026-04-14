"""Tests for audi_connect.actions module."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from audi_connect.actions import AudiVehicleActions
from audi_connect.exceptions import SpinRequiredError


def _make_actions(spin="1234", api_level=1):
    api = AsyncMock()
    client = AsyncMock()
    client._get_home_region = AsyncMock(return_value="https://mal-3a.prd.eu.dp.vwg-connect.com")
    client._get_home_region_setter = AsyncMock(return_value="https://mal-3a.prd.eu.dp.vwg-connect.com")
    client._get_cariad_url_for_vin = MagicMock(side_effect=lambda vin, path, **kw: f"https://emea.bff.cariad.digital/vehicle/v1/vehicles/{vin}/{path}")
    return AudiVehicleActions(
        api=api,
        client=client,
        bearer_token={"access_token": "bearer_test", "id_token": "id_test"},
        vw_token={"access_token": "vw_test"},
        xclient_id="xclient_test",
        country="DE",
        spin=spin,
        api_level=api_level,
    )


class TestSecurityPinHash:
    def test_spin_hash_generation(self):
        actions = _make_actions(spin="1234")
        result = actions._generate_security_pin_hash("abcd1234")
        # SHA-512 of (pin_bytes + challenge_bytes) should be uppercase hex
        assert isinstance(result, str)
        assert len(result) == 128  # SHA-512 = 64 bytes = 128 hex chars
        assert result == result.upper()

    def test_spin_required_error_when_none(self):
        actions = _make_actions(spin=None)
        with pytest.raises(SpinRequiredError, match="S-PIN"):
            actions._generate_security_pin_hash("abcd1234")

    def test_different_challenges_produce_different_hashes(self):
        actions = _make_actions(spin="1234")
        hash1 = actions._generate_security_pin_hash("aaaa1111")
        hash2 = actions._generate_security_pin_hash("bbbb2222")
        assert hash1 != hash2


class TestClimateControl:
    @pytest.mark.asyncio
    async def test_start_climate_cariad_api(self):
        actions = _make_actions(api_level=1)
        await actions.start_climate_control("WAUTEST", temp_c=22.0)
        actions._api.request.assert_awaited_once()
        call_args = actions._api.request.call_args
        assert call_args[0][0] == "POST"
        assert "climatisation/start" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_start_climate_legacy_api(self):
        actions = _make_actions(api_level=0)
        await actions.start_climate_control("WAUTEST", temp_c=21.0)
        actions._api.request.assert_awaited_once()
        call_args = actions._api.request.call_args
        assert call_args[0][0] == "POST"
        assert "climater/actions" in call_args[0][1]
        # Check temperature conversion: 21°C → 2941 deciKelvin
        import json
        data = json.loads(call_args[1]["data"])
        assert data["action"]["settings"]["targetTemperature"] == 2941

    @pytest.mark.asyncio
    async def test_stop_climate_cariad(self):
        actions = _make_actions(api_level=1)
        await actions.stop_climate_control("WAUTEST")
        actions._api.request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_climate_legacy(self):
        actions = _make_actions(api_level=0)
        await actions.stop_climate_control("WAUTEST")
        actions._api.request.assert_awaited_once()


class TestPreheater:
    @pytest.mark.asyncio
    async def test_start_preheater(self):
        actions = _make_actions()
        await actions.start_preheater("WAUTEST", duration=30)
        actions._api.request.assert_awaited_once()
        call_args = actions._api.request.call_args
        assert "auxiliaryheating/start" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_stop_preheater(self):
        actions = _make_actions()
        await actions.stop_preheater("WAUTEST")
        actions._api.request.assert_awaited_once()
        call_args = actions._api.request.call_args
        assert "auxiliaryheating/stop" in call_args[0][1]


class TestActionHeaders:
    def test_header_without_security_token(self):
        actions = _make_actions()
        headers = actions._get_vehicle_action_header("application/json", None)
        assert "x-securityToken" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_header_with_security_token(self):
        actions = _make_actions()
        headers = actions._get_vehicle_action_header("application/json", "sec_token_123")
        assert headers["x-securityToken"] == "sec_token_123"
