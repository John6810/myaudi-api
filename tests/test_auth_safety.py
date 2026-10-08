"""Preserve reusable sessions and rotated refresh tokens through outages."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

from audi_connect.api import AudiAPI
from audi_connect.auth import AudiAuth
from audi_connect.client import AudiVehicleClient
from audi_connect.exceptions import TokenRefreshError
from audi_connect.oauth_state import OAuthState
from audi_connect.token_store import TokenStore
from tests.test_auth import _make_tokens, _make_token_store


@pytest.mark.asyncio
async def test_transient_restore_refresh_failure_keeps_cache():
    cached = {**_make_tokens(), "saved_at": time.time() - 7200}
    store = _make_token_store(cached)
    auth = AudiAuth(MagicMock(), "DE", token_store=store)
    auth._oauth = AsyncMock()
    auth._oauth.refresh_tokens.side_effect = OSError("unavailable")
    with pytest.raises(TokenRefreshError):
        await auth.login("unused", "unused")
    auth._oauth.refresh_tokens.assert_awaited_once()
    auth._oauth.login_device_code.assert_not_awaited()
    store.clear.assert_not_called()


@pytest.mark.asyncio
async def test_network_failure_validating_vehicle_list_does_not_refresh_or_login():
    cached = {**_make_tokens(), "saved_at": time.time()}
    store = _make_token_store(cached)
    auth = AudiAuth(MagicMock(), "DE", token_store=store)
    auth._oauth = AsyncMock()
    with patch.object(AudiVehicleClient, "get_vehicle_list", AsyncMock(side_effect=OSError("down"))):
        with pytest.raises(OSError):
            await auth.login("unused", "unused")
    auth._oauth.refresh_tokens.assert_not_awaited()
    auth._oauth.login_device_code.assert_not_awaited()
    store.clear.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_stage", ["idk", "azs"])
async def test_partial_rotation_is_persisted_and_not_marked_fresh(tmp_path, failed_stage):
    path = tmp_path / "tokens.json"
    cached = _make_tokens()
    cached.update({
        "saved_at": time.time() - 7200,
        "token_endpoint": "https://idp.example/token",
        "mbb_oauth_base_url": "https://mbb.example",
        "authorization_server_base_url": "https://azs.example",
    })
    path.write_text(json.dumps(cached))
    async with aiohttp.ClientSession() as session:
        auth = AudiAuth(AudiAPI(session), "DE", token_store=TokenStore(str(path)))
        with aioresponses() as mocked:
            mocked.post("https://mbb.example/mobile/oauth2/v1/token", payload={
                "access_token": "rotated-mbb", "refresh_token": "rotated-mbb-refresh",
            })
            if failed_stage == "idk":
                mocked.post(cached["token_endpoint"], status=503, payload={"error": "temporarily_unavailable"})
            else:
                mocked.post(cached["token_endpoint"], payload={
                    "access_token": "rotated-idk", "refresh_token": "rotated-idk-refresh",
                })
                mocked.post("https://azs.example/token", status=503, payload={"error": "temporarily_unavailable"})
            with pytest.raises(TokenRefreshError):
                await auth.login("unused", "unused")
            assert sum(map(len, mocked.requests.values())) == (2 if failed_stage == "idk" else 3)
    persisted = json.loads(path.read_text())
    assert persisted["saved_at"] == cached["saved_at"]
    assert persisted["mbb_oauth_token"]["refresh_token"] == "rotated-mbb-refresh"
    assert persisted["bearer_token"]["refresh_token"] == (
        "rotated-idk-refresh" if failed_stage == "azs" else cached["bearer_token"]["refresh_token"]
    )
    assert persisted["audi_token"] == cached["audi_token"]


def test_failed_token_write_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    store = TokenStore(str(path))
    state = OAuthState.from_dict(_make_tokens())
    store.save(state)
    previous = path.read_bytes()
    monkeypatch.setattr("audi_connect.token_store.os.replace", MagicMock(side_effect=OSError("disk error")))
    store.save(state)
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_token_file_is_owner_only(tmp_path):
    path = tmp_path / "tokens.json"
    TokenStore(str(path)).save(OAuthState.from_dict(_make_tokens()))
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_503_validating_cached_session_does_not_trigger_token_exchange(tmp_path):
    cached = {**_make_tokens(), "saved_at": time.time()}
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(cached))
    original = path.read_bytes()
    async with aiohttp.ClientSession() as session:
        auth = AudiAuth(AudiAPI(session), "DE", token_store=TokenStore(str(path)))
        with aioresponses() as mocked:
            mocked.post("https://app-api.live-my.audi.com/vgql/v1/graphql", status=503,
                        payload={"errors": [{"message": "temporarily unavailable"}]})
            with pytest.raises(aiohttp.ClientResponseError) as exc:
                await auth.login("unused", "unused")
            assert exc.value.status == 503
            assert sum(map(len, mocked.requests.values())) == 1
    assert path.read_bytes() == original
