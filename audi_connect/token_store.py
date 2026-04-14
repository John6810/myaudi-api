"""Token persistence - save and load OAuth tokens to avoid re-authenticating every time."""

import json
import logging
import os
import stat
import sys
import time
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

DEFAULT_TOKEN_FILE = os.path.join(Path.home(), ".audi_connect_tokens.json")


class TokenStore:
    """Persists OAuth tokens to a JSON file for reuse across sessions."""

    def __init__(self, filepath: str = DEFAULT_TOKEN_FILE):
        self._filepath = filepath

    def save(
        self,
        bearer_token: dict,
        audi_token: dict,
        vw_token: dict,
        mbb_oauth_token: dict,
        xclient_id: str,
        client_id: str,
        token_endpoint: str,
        authorization_server_base_url: str,
        mbb_oauth_base_url: str,
        language: str,
    ) -> None:
        """Save all tokens and OAuth state to disk."""
        data = {
            "bearer_token": bearer_token,
            "audi_token": audi_token,
            "vw_token": vw_token,
            "mbb_oauth_token": mbb_oauth_token,
            "xclient_id": xclient_id,
            "client_id": client_id,
            "token_endpoint": token_endpoint,
            "authorization_server_base_url": authorization_server_base_url,
            "mbb_oauth_base_url": mbb_oauth_base_url,
            "language": language,
            "saved_at": time.time(),
        }
        try:
            with open(self._filepath, "w") as f:
                json.dump(data, f, default=str)
            # Restrict file permissions to owner-only (skip on Windows where chmod is limited)
            if sys.platform != "win32":
                os.chmod(self._filepath, stat.S_IRUSR | stat.S_IWUSR)
            _LOGGER.debug("Tokens saved to %s", self._filepath)
        except OSError as e:
            _LOGGER.warning("Failed to save tokens: %s", e)

    def load(self, max_age_seconds: int = 3600) -> Optional[dict]:
        """Load tokens from disk if they exist and are not too old.

        Args:
            max_age_seconds: Maximum age of saved tokens in seconds (default: 1 hour).

        Returns:
            Token data dict or None if unavailable/expired.
        """
        if not os.path.exists(self._filepath):
            return None

        try:
            with open(self._filepath, "r") as f:
                data = json.load(f)

            saved_at = data.get("saved_at", 0)
            age = time.time() - saved_at

            if age > max_age_seconds:
                _LOGGER.debug("Saved tokens expired (age: %.0fs, max: %ds)", age, max_age_seconds)
                self.clear()
                return None

            _LOGGER.debug("Loaded tokens from %s (age: %.0fs)", self._filepath, age)
            return data

        except (OSError, json.JSONDecodeError, KeyError) as e:
            _LOGGER.warning("Failed to load tokens: %s", e)
            return None

    def clear(self) -> None:
        """Delete the saved token file."""
        try:
            if os.path.exists(self._filepath):
                os.remove(self._filepath)
                _LOGGER.debug("Token file removed: %s", self._filepath)
        except OSError as e:
            _LOGGER.warning("Failed to remove token file: %s", e)
