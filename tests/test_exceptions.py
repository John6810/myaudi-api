"""Tests for audi_connect.exceptions module."""

from audi_connect.exceptions import (
    AudiConnectError,
    AuthenticationError,
    TokenRefreshError,
    VehicleNotFoundError,
    ActionFailedError,
    SpinRequiredError,
    CountryNotSupportedError,
    RequestTimeoutError,
)


def test_all_exceptions_inherit_from_base():
    for exc_class in [
        AuthenticationError,
        TokenRefreshError,
        VehicleNotFoundError,
        ActionFailedError,
        SpinRequiredError,
        CountryNotSupportedError,
        RequestTimeoutError,
    ]:
        assert issubclass(exc_class, AudiConnectError)
        assert issubclass(exc_class, Exception)


def test_exception_messages():
    e = AuthenticationError("bad creds")
    assert str(e) == "bad creds"

    e = SpinRequiredError("need pin")
    assert "pin" in str(e)
