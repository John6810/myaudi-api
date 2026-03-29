"""Authentication coordinator for Audi Connect — manages tokens and delegates to client/actions."""

import logging
from typing import Optional

from .api import AudiAPI
from .client import AudiVehicleClient
from .actions import AudiVehicleActions
from .oauth import AudiOAuth
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
        self._oauth = AudiOAuth(api, country)

        # OAuth state
        self.mbb_oauth_base_url: Optional[str] = None
        self.mbb_oauth_token: Optional[dict] = None
        self.xclient_id: Optional[str] = None
        self._token_endpoint: str = ""
        self._bearer_token_json: Optional[dict] = None
        self._client_id: str = ""
        self._authorization_server_base_url: str = ""

        # Tokens
        self.vw_token: Optional[dict] = None
        self.audi_token: Optional[dict] = None

        # Delegates (created after login)
        self._client: Optional[AudiVehicleClient] = None
        self._actions: Optional[AudiVehicleActions] = None

        # Cached vehicle list from token validation
        self._cached_vehicle_list: Optional[list[dict]] = None

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

    def _apply_tokens(self, tokens: dict) -> None:
        """Apply a token dict (from login or cache) to internal state."""
        self._bearer_token_json = tokens["bearer_token"]
        self.audi_token = tokens["audi_token"]
        self.vw_token = tokens["vw_token"]
        self.mbb_oauth_token = tokens["mbb_oauth_token"]
        self.xclient_id = tokens["xclient_id"]
        self._client_id = tokens["client_id"]
        self._token_endpoint = tokens["token_endpoint"]
        self._authorization_server_base_url = tokens["authorization_server_base_url"]
        self.mbb_oauth_base_url = tokens["mbb_oauth_base_url"]
        self._language = tokens["language"]
        self._api.set_xclient_id(self.xclient_id)

    def _build_delegates(self) -> None:
        """Create client and actions instances after successful auth."""
        self._client = AudiVehicleClient(
            api=self._api,
            bearer_token=self._bearer_token_json,
            vw_token=self.vw_token,
            audi_token=self.audi_token,
            xclient_id=self.xclient_id,
            country=self._country,
            language=self._language,
            api_level=self._api_level,
        )
        self._actions = AudiVehicleActions(
            api=self._api,
            client=self._client,
            bearer_token=self._bearer_token_json,
            vw_token=self.vw_token,
            xclient_id=self.xclient_id,
            country=self._country,
            spin=self._spin,
            api_level=self._api_level,
        )

    # --- Convenience methods (delegate to client/actions) ---

    async def get_vehicle_list(self) -> list[dict]:
        if self._cached_vehicle_list is not None:
            result = self._cached_vehicle_list
            self._cached_vehicle_list = None
            return result
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
            self._apply_tokens(cached)
            self._build_delegates()
            _LOGGER.info("Restored tokens from cache")
            return True
        except (KeyError, TypeError) as e:
            _LOGGER.debug("Failed to restore cached tokens: %s", e)
            self._token_store.clear()
            return False

    def _save_tokens(self) -> None:
        """Persist current tokens to cache."""
        self._token_store.save(
            bearer_token=self._bearer_token_json,
            audi_token=self.audi_token,
            vw_token=self.vw_token,
            mbb_oauth_token=self.mbb_oauth_token,
            xclient_id=self.xclient_id,
            client_id=self._client_id,
            token_endpoint=self._token_endpoint,
            authorization_server_base_url=self._authorization_server_base_url,
            mbb_oauth_base_url=self.mbb_oauth_base_url,
            language=self._language,
        )

    # --- Login ---

    async def login(self, user: str, password: str) -> None:
        """Full authentication flow (13 steps). Uses cached tokens if available."""
        if self._try_restore_tokens():
            # Validate cached tokens by fetching vehicle list
            try:
                self._cached_vehicle_list = await self.client.get_vehicle_list()
                return
            except Exception as e:
                _LOGGER.info("Cached tokens expired or invalid: %s. Re-authenticating...", e)
                self._token_store.clear()
                self._client = None
                self._actions = None

        _LOGGER.info("Starting login to Audi Connect...")
        tokens = await self._oauth.login(user, password)
        self._apply_tokens(tokens)
        self._api.set_xclient_id(self.xclient_id)
        self._build_delegates()
        self._save_tokens()
        _LOGGER.info("Login successful!")

    async def refresh_tokens(self, elapsed_sec: int) -> bool:
        """Refresh all tokens if they are about to expire."""
        if self.mbb_oauth_token is None:
            return False
        if "refresh_token" not in self.mbb_oauth_token:
            return False
        if "expires_in" not in self.mbb_oauth_token:
            return False
        if (elapsed_sec + 5 * 60) < self.mbb_oauth_token["expires_in"]:
            return False

        try:
            _LOGGER.info("Refreshing tokens...")
            tokens = await self._oauth.refresh_tokens(
                mbb_oauth_token=self.mbb_oauth_token,
                bearer_token=self._bearer_token_json,
                client_id=self._client_id,
                token_endpoint=self._token_endpoint,
                authorization_server_base_url=self._authorization_server_base_url,
                mbb_oauth_base_url=self.mbb_oauth_base_url,
                xclient_id=self.xclient_id,
            )
            self._bearer_token_json = tokens["bearer_token"]
            self.audi_token = tokens["audi_token"]
            self.vw_token = tokens["vw_token"]
            self.mbb_oauth_token = tokens["mbb_oauth_token"]

            self._build_delegates()
            self._save_tokens()
            _LOGGER.info("Token refresh successful!")
            return True

        except Exception as e:
            raise TokenRefreshError(f"Token refresh failed: {e}") from e
