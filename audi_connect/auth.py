"""Authentication coordinator for Audi Connect — manages tokens and delegates to client/actions."""

import logging
import time
from typing import Optional

from .api import AudiAPI
from .client import AudiVehicleClient
from .actions import AudiVehicleActions
from .endpoints import AudiEndpoints
from .oauth import AudiOAuth, uses_device_code
from .oauth_state import OAuthState
from .token_store import TokenStore
from .exceptions import AuthenticationError, TokenRefreshError

_LOGGER = logging.getLogger(__name__)


class AudiAuth:
    """Manages Audi Connect authentication state and delegates to client/actions."""

    def __init__(self, api: AudiAPI, country: str, spin: Optional[str] = None, api_level: int = 1, token_store: Optional[TokenStore] = None):
        self._api = api
        self._country = country or "DE"
        self._language: Optional[str] = None
        self._spin = spin
        self._api_level = api_level if api_level is not None else 0
        self._token_store = token_store or TokenStore()
        self._endpoints = AudiEndpoints(api, country=self._country, api_level=self._api_level)
        self._oauth = AudiOAuth(api, country)

        self._state: Optional[OAuthState] = None
        self._restored_age_sec: int = 0  # age of cache-restored tokens (0 = fresh login)

        # Delegates (created after login)
        self._client: Optional[AudiVehicleClient] = None
        self._actions: Optional[AudiVehicleActions] = None

    @property
    def client(self) -> AudiVehicleClient:
        if self._client is None:
            raise AuthenticationError("Not authenticated - call login() first")
        return self._client

    @property
    def actions(self) -> AudiVehicleActions:
        if self._actions is None:
            raise AuthenticationError("Not authenticated - call login() first")
        return self._actions

    # --- Backwards-compat property proxies (read-only) ---
    # TODO: remove in a follow-up once external readers are gone.
    @property
    def vw_token(self) -> Optional[dict]:
        return self._state.vw_token if self._state else None

    @property
    def audi_token(self) -> Optional[dict]:
        return self._state.audi_token if self._state else None

    @property
    def mbb_oauth_token(self) -> Optional[dict]:
        return self._state.mbb_oauth_token if self._state else None

    @property
    def xclient_id(self) -> Optional[str]:
        return self._state.xclient_id if self._state else None

    def _set_state(self, state: OAuthState) -> None:
        """Adopt a new OAuth state and propagate to api and endpoints."""
        self._state = state
        self._language = state.language
        self._api.set_xclient_id(state.xclient_id)

    def _build_delegates(self) -> None:
        """Create client and actions instances after successful auth."""
        assert self._state is not None
        self._endpoints.set_vw_token(self._state.vw_token)
        self._client = AudiVehicleClient(
            api=self._api,
            endpoints=self._endpoints,
            bearer_token=self._state.bearer_token,
            vw_token=self._state.vw_token,
            audi_token=self._state.audi_token,
            xclient_id=self._state.xclient_id,
            country=self._country,
            language=self._language,
            api_level=self._api_level,
        )
        self._actions = AudiVehicleActions(
            api=self._api,
            endpoints=self._endpoints,
            bearer_token=self._state.bearer_token,
            vw_token=self._state.vw_token,
            xclient_id=self._state.xclient_id,
            country=self._country,
            spin=self._spin,
            api_level=self._api_level,
        )

    # --- Convenience methods (delegate to client/actions) ---

    async def get_vehicle_list(self) -> list[dict]:
        return await self.client.get_vehicle_list()

    async def get_stored_vehicle_data(self, vin: str) -> dict:
        return await self.client.get_stored_vehicle_data(vin)

    async def get_stored_position(self, vin: str) -> Optional[dict]:
        return await self.client.get_stored_position(vin)

    async def get_tripdata(self, vin: str, kind: str) -> dict:
        return await self.client.get_tripdata(vin, kind)

    async def set_vehicle_lock(self, vin: str, lock: bool) -> None:
        await self.actions.set_vehicle_lock(vin, lock)

    async def start_climate_control(self, vin: str, temp_c: float = 21.0) -> None:
        await self.actions.start_climate_control(vin, temp_c)

    async def stop_climate_control(self, vin: str) -> None:
        await self.actions.stop_climate_control(vin)

    async def start_preheater(self, vin: str, duration: int = 30) -> None:
        await self.actions.start_preheater(vin, duration)

    async def stop_preheater(self, vin: str) -> None:
        await self.actions.stop_preheater(vin)

    # --- Token persistence ---

    def _try_restore_tokens(self) -> bool:
        """Try to restore tokens from cache. Returns True if successful.

        Records the cache age in ``_restored_age_sec`` so login() can refresh
        stale access tokens instead of falling back to a full (interactive on
        EU) login.
        """
        cached = self._token_store.load()
        if cached is None:
            return False

        try:
            self._set_state(OAuthState.from_dict(cached))
            self._build_delegates()
            self._restored_age_sec = int(time.time() - cached.get("saved_at", 0))
            _LOGGER.info("Restored tokens from cache (age: %ds)", self._restored_age_sec)
            return True
        except (KeyError, TypeError) as e:
            _LOGGER.debug("Failed to restore cached tokens: %s", e)
            self._token_store.clear()
            return False

    def _save_tokens(self) -> None:
        """Persist current tokens to cache."""
        if self._state is not None:
            self._token_store.save(self._state)

    # --- Login ---

    async def login(self, user: str, password: str, on_verification=None) -> list[dict]:
        """Full authentication flow. Uses cached tokens if available.

        For European regions this uses the device-code flow (Audi enforces Play
        Integrity attestation on the password flow there since July 2026), which
        requires a one-time manual approval; `on_verification` is called with the
        approval prompt details (see AudiOAuth.login_device_code). US/CA/CN keep
        the username/password flow.

        Returns the validated vehicle list (so callers can avoid an extra
        get_vehicle_list() round-trip; the list is fetched as part of token
        validation either way).
        """
        if self._try_restore_tokens():
            try:
                # Stale access tokens are refreshed via the persisted refresh
                # tokens rather than falling through to a full login — which on
                # EU would demand a manual device-code re-approval.
                # refresh_tokens() self-gates on the MBB expiry: fresh caches
                # skip the upstream calls entirely.
                await self.refresh_tokens(self._restored_age_sec)
                return await self.client.get_vehicle_list()
            except TokenRefreshError as e:
                _LOGGER.info("Token refresh failed: %s. Re-authenticating...", e)
                self._reset_auth_state()
            except Exception as e:
                # The freshness gate keys off the MBB expiry (1h), but the AZS
                # token dies much sooner (~10min) — a restore can pass the gate
                # and still fail validation here. Force-refresh all 3 tokens and
                # retry once before surrendering to a full login, which on EU
                # would cost an interactive device-code approval.
                _LOGGER.info("Cached tokens rejected (%s) — forcing a token refresh...", e)
                try:
                    await self.refresh_tokens(self._restored_age_sec, force=True)
                    return await self.client.get_vehicle_list()
                except Exception as e2:
                    _LOGGER.info("Forced refresh failed: %s. Re-authenticating...", e2)
                    self._reset_auth_state()

        _LOGGER.info("Starting login to Audi Connect...")
        if uses_device_code(self._country):
            tokens = await self._oauth.login_device_code(on_verification=on_verification)
        else:
            tokens = await self._oauth.login(user, password)
        self._set_state(OAuthState.from_dict(tokens))
        self._build_delegates()
        self._save_tokens()
        _LOGGER.info("Login successful!")
        return await self.client.get_vehicle_list()

    def _reset_auth_state(self) -> None:
        """Drop cached tokens and delegates ahead of a fresh full login."""
        self._token_store.clear()
        self._client = None
        self._actions = None

    async def refresh_tokens(self, elapsed_sec: int, force: bool = False) -> bool:
        """Refresh all tokens if they are about to expire.

        Called by AudiClient.ensure_auth() before falling back to a
        full login. Costs 3 upstream round-trips (MBB + IDK + AZS)
        vs ~10 for a full login.

        ``force=True`` bypasses the freshness gate (which keys off the MBB
        expiry) — used when a restored session fails validation because a
        shorter-lived token (AZS, ~10min) already died.

        Returns True if a refresh actually happened, False if the
        existing tokens are still valid enough not to need refreshing.
        """
        if self._state is None or self._state.mbb_oauth_token is None:
            return False
        if "refresh_token" not in self._state.mbb_oauth_token:
            return False
        if "expires_in" not in self._state.mbb_oauth_token:
            return False
        if not force and (elapsed_sec + 5 * 60) < self._state.mbb_oauth_token["expires_in"]:
            return False

        try:
            _LOGGER.info("Refreshing tokens...")
            refreshed = await self._oauth.refresh_tokens(
                mbb_oauth_token=self._state.mbb_oauth_token,
                bearer_token=self._state.bearer_token,
                client_id=self._state.client_id,
                token_endpoint=self._state.token_endpoint,
                authorization_server_base_url=self._state.authorization_server_base_url,
                mbb_oauth_base_url=self._state.mbb_oauth_base_url,
                xclient_id=self._state.xclient_id,
            )
            self._set_state(self._state.with_refresh(refreshed))
            self._build_delegates()
            self._save_tokens()
            _LOGGER.info("Token refresh successful!")
            return True

        except Exception as e:
            raise TokenRefreshError(f"Token refresh failed: {e}") from e
