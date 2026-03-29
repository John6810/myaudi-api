"""Tests for CLI error formatting and helpers."""

from main import _format_error, _resolve_vin
from audi_connect.exceptions import (
    AuthenticationError,
    SpinRequiredError,
    CountryNotSupportedError,
    RequestTimeoutError,
    AudiConnectError,
    ActionFailedError,
)


class TestFormatError:
    def test_auth_error(self):
        msg = _format_error(AuthenticationError("bad token"))
        assert "AUDI_USERNAME" in msg
        assert "AUDI_PASSWORD" in msg

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
