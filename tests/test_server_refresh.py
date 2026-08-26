"""Regression tests for the server's periodic token refresh (ensure_auth).

The 45-min refresh interval is shorter than the MBB expiry (1h) that
AudiAuth.refresh_tokens' freshness gate keys on. ensure_auth must therefore
call with force=True — without it the gate declined every periodic call, the
timestamp was reset anyway, and the tokens silently died at the 1h mark
(every Audi call 401'd until a pod restart).
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import server as api_module


def _stale_client():
    """An AudiClient whose last refresh is past TOKEN_REFRESH_INTERVAL."""
    c = api_module.AudiClient()
    c.authenticated = True
    c._auth = MagicMock()
    c._auth_time = time.time() - api_module.TOKEN_REFRESH_INTERVAL - 60
    return c


class TestEnsureAuthRefresh:
    @pytest.mark.asyncio
    async def test_periodic_refresh_forces_past_the_freshness_gate(self):
        client = _stale_client()
        client._auth.refresh_tokens = AsyncMock(return_value=True)

        assert await client.ensure_auth() is True

        client._auth.refresh_tokens.assert_awaited_once()
        # force=True is what keeps the 45-min cycle actually refreshing.
        assert client._auth.refresh_tokens.await_args.kwargs.get("force") is True
        # Clock reset only after an actual refresh.
        assert time.time() - client._auth_time < 5

    @pytest.mark.asyncio
    async def test_refresh_declined_falls_back_to_full_login(self):
        # False now only means "no usable auth context" — must trigger login,
        # never a silent timestamp bump.
        client = _stale_client()
        client._auth.refresh_tokens = AsyncMock(return_value=False)
        client.login = AsyncMock(return_value=True)

        assert await client.ensure_auth() is True
        client.login.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fresh_tokens_skip_refresh_entirely(self):
        client = api_module.AudiClient()
        client.authenticated = True
        client._auth = MagicMock()
        client._auth.refresh_tokens = AsyncMock()
        client._auth_time = time.time()  # just refreshed

        assert await client.ensure_auth() is True
        client._auth.refresh_tokens.assert_not_awaited()
