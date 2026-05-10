"""Tests for the AudiClient.ensure_auth refresh-first flow in server.py."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import server as api_module


@pytest.fixture
def client_in_refresh_window(monkeypatch):
    """Pre-authenticated AudiClient whose token-refresh window has elapsed."""
    c = api_module.client
    monkeypatch.setattr(c, "authenticated", True)
    monkeypatch.setattr(c, "_auth_time", time.time() - (api_module.TOKEN_REFRESH_INTERVAL + 60))
    monkeypatch.setattr(c, "_auth", MagicMock())
    return c


class TestEnsureAuthRefreshFirst:
    @pytest.mark.asyncio
    async def test_refresh_called_before_full_login(self, client_in_refresh_window, monkeypatch):
        """ensure_auth tries refresh_tokens before falling back to login."""
        c = client_in_refresh_window
        c._auth.refresh_tokens = AsyncMock(return_value=True)
        login_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(c, "login", login_mock)

        result = await c.ensure_auth()

        assert result is True
        c._auth.refresh_tokens.assert_awaited_once()
        login_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refresh_failure_falls_back_to_login(self, client_in_refresh_window, monkeypatch):
        """If refresh_tokens raises, ensure_auth falls back to login."""
        c = client_in_refresh_window
        c._auth.refresh_tokens = AsyncMock(side_effect=Exception("boom"))
        login_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(c, "login", login_mock)

        result = await c.ensure_auth()

        assert result is True
        c._auth.refresh_tokens.assert_awaited_once()
        login_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_refresh_when_no_auth_context(self, monkeypatch):
        """First-ever ensure_auth (no _auth) goes straight to login."""
        c = api_module.client
        monkeypatch.setattr(c, "authenticated", False)
        monkeypatch.setattr(c, "_auth", None)
        monkeypatch.setattr(c, "_auth_time", 0.0)
        login_mock = AsyncMock(return_value=True)
        monkeypatch.setattr(c, "login", login_mock)

        result = await c.ensure_auth()

        assert result is True
        login_mock.assert_awaited_once()
