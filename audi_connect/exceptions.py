"""Custom exceptions for the Audi Connect client."""


class AudiConnectError(Exception):
    """Base exception for all Audi Connect errors."""


class AuthenticationError(AudiConnectError):
    """Raised when authentication fails (bad credentials, expired session, etc.)."""


class DeviceGrantRejectedError(AuthenticationError):
    """Audi/VW refused this client's device grant before user authentication."""


class TokenRefreshError(AudiConnectError):
    """Raised when token refresh fails."""


class VehicleNotFoundError(AudiConnectError):
    """Raised when the requested VIN is not found."""


class ActionFailedError(AudiConnectError):
    """Raised when a vehicle action (lock, climate, etc.) fails."""


class SpinRequiredError(AudiConnectError):
    """Raised when an action requires S-PIN but none was provided."""


class CountryNotSupportedError(AudiConnectError):
    """Raised when the configured country is not in Audi's market list."""


class RequestTimeoutError(AudiConnectError):
    """Raised when an API request times out."""
