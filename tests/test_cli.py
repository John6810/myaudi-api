"""Tests for CLI error formatting and helpers."""

from main import _format_error, _resolve_vin
from audi_connect.exceptions import (
    AuthenticationError,
    DeviceGrantRejectedError,
    SpinRequiredError,
    CountryNotSupportedError,
    RequestTimeoutError,
    AudiConnectError,
    ActionFailedError,
)


class TestFormatError:
    def test_auth_error(self):
        msg = _format_error(AuthenticationError("bad token"))
        assert "bad token" in msg
        assert "Check your AUDI_USERNAME" not in msg

    def test_device_grant_refusal(self):
        msg = _format_error(DeviceGrantRejectedError("unauthorized_client"))
        assert "unauthorized_client" in msg
        assert "No reliable EU login from scratch" in msg
        assert "Keep your existing ~/.audi_connect_tokens.json" in msg
        assert "refresh tokens may still work" in msg
        assert "Play Integrity" in msg
        assert "Check your AUDI_USERNAME" not in msg

    def test_auth_error_redacts_server_detail(self):
        msg = _format_error(AuthenticationError(
            'invalid assertion headers: {"refresh_token": "secret-refresh"}'
        ))
        assert "invalid assertion headers" in msg
        assert "secret-refresh" not in msg
        assert "***" in msg

    def test_spin_error(self):
        msg = _format_error(SpinRequiredError("no pin"))
        assert "S-PIN" in msg
        assert "AUDI_SPIN" in msg

    def test_country_error(self):
        msg = _format_error(CountryNotSupportedError("XX not found"))
        assert "Country not supported" in msg

    def test_timeout_error(self):
        msg = _format_error(RequestTimeoutError("timed out"))
        assert "timed out" in msg.lower()

    def test_generic_audi_error(self):
        msg = _format_error(AudiConnectError("something broke"))
        assert "something broke" in msg

    def test_unexpected_error(self):
        msg = _format_error(ValueError("weird"))
        assert "Unexpected" in msg


class TestResolveVin:
    def test_explicit_vin(self):
        class Args:
            vin = "WAUEXPLICIT"
        assert _resolve_vin(Args()) == "WAUEXPLICIT"

    def test_no_vin(self):
        class Args:
            vin = None
        # DEFAULT_VIN depends on env, just check it doesn't crash
        result = _resolve_vin(Args())
        assert result is None or isinstance(result, str)
