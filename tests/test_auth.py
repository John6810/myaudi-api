"""Tests for audi_connect.auth module."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from audi_connect.auth import AudiAuth
from audi_connect.oauth import AudiOAuth
from audi_connect.exceptions import AuthenticationError, TokenRefreshError


def _make_token_store(cached=None):
    store = MagicMock()
    store.load.return_value = cached
    store.save = MagicMock()
    store.clear = MagicMock()
    return store


def _make_tokens():
    return {
        "bearer_token": {"access_token": "bearer_abc", "refresh_token": "br_ref"},
        "audi_token": {"access_token": "audi_abc"},
        "vw_token": {"access_token": "vw_abc"},
        "mbb_oauth_token": {"refresh_token": "mbb_ref", "expires_in": 3600},
        "xclient_id": "xclient_123",
        "client_id": "client_456",
        "token_endpoint": "https://example.com/token",
        "authorization_server_base_url": "https://example.com/auth",
        "mbb_oauth_base_url": "https://example.com/mbb",
        "language": "fr",
    }


class TestAudiAuthDelegates:
    def test_client_raises_before_login(self):
        api = MagicMock()
        auth = AudiAuth(api, country="DE")
        with pytest.raises(AuthenticationError, match="Not authenticated"):
            _ = auth.client

    def test_actions_raises_before_login(self):
        api = MagicMock()
        auth = AudiAuth(api, country="DE")
        with pytest.raises(AuthenticationError, match="Not authenticated"):
            _ = auth.actions


class TestTokenRestore:
    def test_restore_from_cache(self):
        api = MagicMock()
        api.set_xclient_id = MagicMock()
        tokens = _make_tokens()
        store = _make_token_store(cached=tokens)

        auth = AudiAuth(api, country="DE", token_store=store)
        result = auth._try_restore_tokens()

        assert result is True
        assert auth._bearer_token_json == tokens["bearer_token"]
        assert auth.vw_token == tokens["vw_token"]
        assert auth._client is not None
        assert auth._actions is not None

    def test_restore_fails_with_missing_keys(self):
        api = MagicMock()
        api.set_xclient_id = MagicMock()
        incomplete = {"bearer_token": {"access_token": "x"}}  # Missing keys
        store = _make_token_store(cached=incomplete)

        auth = AudiAuth(api, country="DE", token_store=store)
        result = auth._try_restore_tokens()

        assert result is False
        store.clear.assert_called_once()

    def test_restore_fails_with_no_cache(self):
        api = MagicMock()
        store = _make_token_store(cached=None)

        auth = AudiAuth(api, country="DE", token_store=store)
        result = auth._try_restore_tokens()

        assert result is False


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_with_valid_cache(self):
        api = MagicMock()
        api.set_xclient_id = MagicMock()
        tokens = _make_tokens()
        store = _make_token_store(cached=tokens)

        auth = AudiAuth(api, country="DE", token_store=store)

        # Mock the client's get_vehicle_list to succeed
        with patch.object(auth, '_try_restore_tokens', return_value=True):
            mock_client = AsyncMock()
            mock_client.get_vehicle_list = AsyncMock(return_value=[{"vin": "TEST"}])
            auth._client = mock_client
            await auth.login("user@test.com", "password123")

        assert auth._cached_vehicle_list == [{"vin": "TEST"}]

    @pytest.mark.asyncio
    async def test_login_fresh_when_no_cache(self):
        api = MagicMock()
        api.set_xclient_id = MagicMock()
        store = _make_token_store(cached=None)
        tokens = _make_tokens()

        auth = AudiAuth(api, country="DE", token_store=store)

        # Mock the oauth login
        auth._oauth = AsyncMock()
        auth._oauth.login = AsyncMock(return_value=tokens)

        await auth.login("user@test.com", "password123")

        auth._oauth.login.assert_awaited_once_with("user@test.com", "password123")
        store.save.assert_called_once()
        assert auth._client is not None


class TestRefreshTokens:
    @pytest.mark.asyncio
    async def test_no_refresh_when_not_expired(self):
        api = MagicMock()
        auth = AudiAuth(api, country="DE")
        auth.mbb_oauth_token = {"refresh_token": "x", "expires_in": 3600}
        # elapsed_sec=100, threshold=100+300=400 < 3600 → no refresh
        result = await auth.refresh_tokens(elapsed_sec=100)
        assert result is False

    @pytest.mark.asyncio
    async def test_no_refresh_without_token(self):
        api = MagicMock()
        auth = AudiAuth(api, country="DE")
        auth.mbb_oauth_token = None
        result = await auth.refresh_tokens(elapsed_sec=9999)
        assert result is False

    @pytest.mark.asyncio
    async def test_refresh_calls_oauth(self):
        api = MagicMock()
        api.set_xclient_id = MagicMock()
        tokens = _make_tokens()
        store = _make_token_store()

        auth = AudiAuth(api, country="DE", token_store=store)
        auth._apply_tokens(tokens)
        auth._build_delegates()

        fresh_tokens = {
            "bearer_token": {"access_token": "new_bearer", "refresh_token": "new_ref"},
            "audi_token": {"access_token": "new_audi"},
            "vw_token": {"access_token": "new_vw"},
            "mbb_oauth_token": {"refresh_token": "new_mbb", "expires_in": 3600},
        }
        auth._oauth = AsyncMock()
        auth._oauth.refresh_tokens = AsyncMock(return_value=fresh_tokens)

        # elapsed > expires_in - 5min → should refresh
        result = await auth.refresh_tokens(elapsed_sec=3500)

        assert result is True
        assert auth._bearer_token_json["access_token"] == "new_bearer"
        store.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_failure_raises(self):
        api = MagicMock()
        api.set_xclient_id = MagicMock()
        tokens = _make_tokens()

        auth = AudiAuth(api, country="DE")
        auth._apply_tokens(tokens)
        auth._build_delegates()

        auth._oauth = AsyncMock()
        auth._oauth.refresh_tokens = AsyncMock(side_effect=Exception("network"))

        with pytest.raises(TokenRefreshError, match="network"):
            await auth.refresh_tokens(elapsed_sec=3500)


class TestAudiOAuthHelpers:
    def test_calculate_x_qmauth(self):
        result = AudiOAuth._calculate_x_qmauth()
        assert result.startswith("v1:01da27b0:")
        # HMAC hex is 64 chars
        assert len(result.split(":")[-1]) == 64

    def test_get_cariad_url_eu(self):
        api = MagicMock()
        oauth = AudiOAuth(api, country="DE")
        url = oauth._get_cariad_url("/login/v1/idk/openid-configuration")
        assert "emea.bff.cariad.digital" in url
        assert "openid-configuration" in url

    def test_get_cariad_url_us(self):
        api = MagicMock()
        oauth = AudiOAuth(api, country="US")
        url = oauth._get_cariad_url("/login/v1/idk/openid-configuration")
        assert "na.bff.cariad.digital" in url

    def test_get_post_url_absolute(self):
        html = '<form action="https://identity.vwgroup.io/submit"><input type="hidden" name="a" value="1"></form>'
        result = AudiOAuth._get_post_url(html, "https://example.com")
        assert result == "https://identity.vwgroup.io/submit"

    def test_get_post_url_relative(self):
        html = '<form action="/submit"><input type="hidden" name="a" value="1"></form>'
        result = AudiOAuth._get_post_url(html, "https://identity.vwgroup.io/login")
        assert result == "https://identity.vwgroup.io/submit"

    def test_get_hidden_form_data(self):
        html = '<form><input type="hidden" name="csrf" value="abc123"><input type="hidden" name="relay" value="xyz"></form>'
        result = AudiOAuth._get_hidden_html_input_form_data(html, {"email": "test@test.com"})
        assert result["email"] == "test@test.com"
        assert result["csrf"] == "abc123"
        assert result["relay"] == "xyz"
