"""Tests for the OAuth device-code (RFC 8628) login flow — EU regions."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from audi_connect.oauth import (
    AudiOAuth,
    uses_device_code,
    DEVICE_CODE_GRANT_TYPE,
    DEVICE_CODE_SCOPE,
    DEVICE_AUTH_ENDPOINT_FALLBACK,
)
from audi_connect.exceptions import AuthenticationError


class TestUsesDeviceCode:
    @pytest.mark.parametrize("country", ["DE", "FR", "BE", "NL", "de", "es", None])
    def test_european_regions_use_device_code(self, country):
        assert uses_device_code(country) is True

    @pytest.mark.parametrize("country", ["US", "CA", "CN", "us", "ca"])
    def test_password_regions_do_not(self, country):
        assert uses_device_code(country) is False


def _oauth_with_requests(side_effect):
    """Build an AudiOAuth whose api.request yields the given side effects."""
    api = MagicMock()
    api.use_token = MagicMock()
    api.set_xclient_id = MagicMock()
    api.request = AsyncMock(side_effect=side_effect)
    return AudiOAuth(api, country="DE"), api


def _txt(obj):
    """A (response, text) tuple as returned for rsp_wtxt=True calls."""
    return (MagicMock(), json.dumps(obj))


class TestNotifyVerification:
    def test_callback_invoked_when_provided(self):
        cb = MagicMock()
        info = {"verification_uri": "https://x/verify", "user_code": "ABCD-1234"}
        AudiOAuth._notify_verification(info, cb)
        cb.assert_called_once_with(info)

    def test_logs_when_no_callback(self, caplog):
        info = {
            "verification_uri": "https://x/verify",
            "verification_uri_complete": "https://x/verify?code=ABCD-1234",
            "user_code": "ABCD-1234",
        }
        with caplog.at_level("WARNING"):
            AudiOAuth._notify_verification(info, None)
        assert "Device approval required" in caplog.text
        # Prefers the complete URL when present
        assert "code=ABCD-1234" in caplog.text


class TestPollDeviceToken:
    @pytest.mark.asyncio
    async def test_pending_then_success(self):
        oauth, api = _oauth_with_requests([
            _txt({"error": "authorization_pending"}),
            _txt({"error": "authorization_pending"}),
            _txt({"access_token": "idk_abc", "id_token": "id_abc", "refresh_token": "r"}),
        ])
        with patch("audi_connect.oauth.asyncio.sleep", new=AsyncMock()):
            token = await oauth._poll_device_token(
                "https://token", "devcode", "client", {"interval": 1, "expires_in": 600}
            )
        assert token["access_token"] == "idk_abc"
        assert api.request.await_count == 3
        # Poll body carries the RFC 8628 grant type + device code
        sent_body = api.request.await_args.args[2]
        assert DEVICE_CODE_GRANT_TYPE.replace(":", "%3A") in sent_body or "device_code" in sent_body

    @pytest.mark.asyncio
    async def test_slow_down_increases_interval(self):
        oauth, api = _oauth_with_requests([
            _txt({"error": "slow_down"}),
            _txt({"access_token": "idk_abc"}),
        ])
        sleep = AsyncMock()
        with patch("audi_connect.oauth.asyncio.sleep", new=sleep):
            token = await oauth._poll_device_token(
                "https://token", "devcode", "client", {"interval": 2, "expires_in": 600}
            )
        assert token["access_token"] == "idk_abc"
        # After a slow_down the interval bumped from 2 to 7
        assert sleep.await_args_list[-1].args[0] == 7

    @pytest.mark.asyncio
    async def test_hard_error_raises(self):
        oauth, _ = _oauth_with_requests([
            _txt({"error": "access_denied", "error_description": "user declined"}),
        ])
        with patch("audi_connect.oauth.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(AuthenticationError, match="user declined"):
                await oauth._poll_device_token(
                    "https://token", "devcode", "client", {"interval": 1, "expires_in": 600}
                )

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        # expires_in=0 → deadline already passed → loop never polls
        oauth, api = _oauth_with_requests([])
        with patch("audi_connect.oauth.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(AuthenticationError, match="timed out"):
                await oauth._poll_device_token(
                    "https://token", "devcode", "client", {"interval": 1, "expires_in": 0}
                )
        assert api.request.await_count == 0


class TestLoginDeviceCode:
    @pytest.mark.asyncio
    async def test_device_authorization_missing_device_code_raises(self):
        # _fetch_login_config makes 3 JSON calls, then the device-auth POST
        config_markets = {"countries": {"countrySpecifications": {"DE": {"defaultLanguage": "de"}}}}
        oauth, api = _oauth_with_requests([
            config_markets,                       # step 1 markets (json)
            {},                                   # step 2 marketcfg (json)
            {},                                   # step 3 openid (json)
            _txt({"error": "invalid_client"}),    # device authorization (rsp_wtxt)
        ])
        with pytest.raises(AuthenticationError, match="did not return a device_code"):
            await oauth.login_device_code()

    @pytest.mark.asyncio
    async def test_full_device_flow_calls_finalize(self):
        config_markets = {"countries": {"countrySpecifications": {"DE": {"defaultLanguage": "de"}}}}
        cb = MagicMock()
        oauth, api = _oauth_with_requests([
            config_markets,   # markets
            {},               # marketcfg
            {"device_authorization_endpoint": "https://idp/device", "token_endpoint": "https://idp/token"},
            _txt({
                "device_code": "DEV", "user_code": "ABCD-1234",
                "verification_uri": "https://idp/verify",
                "verification_uri_complete": "https://idp/verify?code=ABCD-1234",
                "interval": 1, "expires_in": 600,
            }),
            _txt({"access_token": "idk_abc", "id_token": "id_abc", "refresh_token": "r"}),  # poll
        ])
        # Stub the shared tail so we only exercise the device-code front half.
        oauth._finalize_session = AsyncMock(return_value={"bearer_token": {"access_token": "idk_abc"}})
        with patch("audi_connect.oauth.asyncio.sleep", new=AsyncMock()):
            result = await oauth.login_device_code(on_verification=cb)

        cb.assert_called_once()
        assert cb.call_args.args[0]["user_code"] == "ABCD-1234"
        oauth._finalize_session.assert_awaited_once()
        bearer_arg = oauth._finalize_session.await_args.args[0]
        assert bearer_arg["access_token"] == "idk_abc"
        assert result["bearer_token"]["access_token"] == "idk_abc"
