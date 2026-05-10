"""Authentication coordinator for Audi Connect — manages tokens and delegates to client/actions."""

import logging
from typing import Optional

from .api import AudiAPI
from .client import AudiVehicleClient
from .actions import AudiVehicleActions
from .endpoints import AudiEndpoints
from .oauth import AudiOAuth
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
        """Try to restore tokens from cache. Returns True if successful."""
        cached = self._token_store.load()
        if cached is None:
            return False

        try:
            self._set_state(OAuthState.from_dict(cached))
            self._build_delegates()
            _LOGGER.info("Restored tokens from cache")
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

    async def login(self, user: str, password: str) -> list[dict]:
        """Full authentication flow (13 steps). Uses cached tokens if available.

        Returns the validated vehicle list (so callers can avoid an extra
        get_vehicle_list() round-trip; the list is fetched as part of token
        validation either way).
        """
        if self._try_restore_tokens():
            try:
                return await self.client.get_vehicle_list()
            except Exception as e:
                _LOGGER.info("Cached tokens expired or invalid: %s. Re-authenticating...", e)
                self._token_store.clear()
                self._client = None
                self._actions = None

        _LOGGER.info("Starting login to Audi Connect...")
        tokens = await self._oauth.login(user, password)
        self._set_state(OAuthState.from_dict(tokens))
        self._build_delegates()
        self._save_tokens()
        _LOGGER.info("Login successful!")
        return await self.client.get_vehicle_list()

    async def refresh_tokens(self, elapsed_sec: int) -> bool:
        """Refresh all tokens if they are about to expire.

        TODO: wire this from api.py AudiClient.ensure_auth() — currently
        ensure_auth re-runs the full 13-step login every TOKEN_REFRESH_INTERVAL,
        which burns ~10 upstream round-trips per cycle. Calling this method
        instead would cost only 3 (MBB + IDK + AZS).
        """
        if self._state is None or self._state.mbb_oauth_token is None:
            return False
        if "refresh_token" not in self._state.mbb_oauth_token:
            return False
        if "expires_in" not in self._state.mbb_oauth_token:
            return False
        if (elapsed_sec + 5 * 60) < self._state.mbb_oauth_token["expires_in"]:
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
