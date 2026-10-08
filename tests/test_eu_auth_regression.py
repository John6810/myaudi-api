"""EU cold-start refusal and persisted refresh, using the real HTTP/auth stack."""

import json
import time
from urllib.parse import parse_qs
from unittest.mock import MagicMock

import aiohttp
import pytest
from aioresponses import aioresponses
from yarl import URL

from audi_connect.api import AudiAPI
from audi_connect.auth import AudiAuth
from audi_connect.exceptions import DeviceGrantRejectedError
from audi_connect.oauth import AudiOAuth, DEVICE_AUTH_ENDPOINT_FALLBACK, DEVICE_CODE_SCOPE
from audi_connect.token_store import TokenStore


CLIENT_ID = "09b6cbec-cd19-4589-82fd-363dfa8c24da@apps_vw-dilab_com"
GRAPHQL_URL = "https://app-api.live-my.audi.com/vgql/v1/graphql"
DESCRIPTION = "client is not allowed to use the device_code grant"


@pytest.mark.asyncio
async def test_market_configuration_can_rotate_discovery_and_audi_proxy():
    """Use the published production URLs without requiring a retired key."""
    discovery = "https://config.example/oidc/openid-configuration"
    async with aiohttp.ClientSession() as session:
        oauth = AudiOAuth(AudiAPI(session), country="BE")
        with aioresponses() as mock:
            mock.get(
                "https://content.app.my.audi.com/service/mobileapp/configurations/markets",
                payload={"countries": {"countrySpecifications": {"BE": {"defaultLanguage": "nl"}}}},
            )
            mock.get(
                "https://content.app.my.audi.com/service/mobileapp/configurations/market/BE/nl?v=4.23.1",
                payload={
                    "idkLoginServiceConfigurationURLProduction": discovery,
                    "myAudiAuthorizationServerProxyServiceURLProduction": "https://audi-proxy.example",
                },
            )
            mock.get(discovery, payload={"token_endpoint": "https://idp.example/token"})

            config = await oauth._fetch_login_config()

            assert config["token_endpoint"] == "https://idp.example/token"
            assert config["authorization_server_base_url"] == "https://audi-proxy.example"
            assert config["client_id"] == CLIENT_ID
            assert config["device_authorization_endpoint"] == DEVICE_AUTH_ENDPOINT_FALLBACK
            assert sum(len(calls) for calls in mock.requests.values()) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("discovered", [False, True])
@pytest.mark.parametrize("description", [None, DESCRIPTION])
async def test_cold_start_refusal_stops_before_approval(
    tmp_path, caplog, discovered, description,
):
    """A real 403 must propagate through AudiAuth without polling/password fallback."""
    endpoint = "https://idp.example/device" if discovered else DEVICE_AUTH_ENDPOINT_FALLBACK
    client_id = "discovered-client" if discovered else CLIENT_ID
    token_file = tmp_path / "tokens.json"
    callback = MagicMock()
    payload = {"error": "unauthorized_client", "refresh_token": "do-not-log"}
    if description is not None:
        payload["error_description"] = description

    async with aiohttp.ClientSession() as session:
        auth = AudiAuth(AudiAPI(session), country="DE", token_store=TokenStore(str(token_file)))
        with aioresponses() as mock:
            mock.get(
                "https://content.app.my.audi.com/service/mobileapp/configurations/markets",
                payload={"countries": {"countrySpecifications": {"DE": {"defaultLanguage": "de"}}}},
            )
            mock.get(
                "https://content.app.my.audi.com/service/mobileapp/configurations/market/DE/de?v=4.23.1",
                payload={"idkClientIDAndroidLive": client_id} if discovered else {},
            )
            mock.get(
                "https://emea.bff.cariad.digital/auth/v1/idk/oidc/openid-configuration",
                payload={"device_authorization_endpoint": endpoint} if discovered else {},
            )
            mock.post(endpoint, status=403, payload=payload)

            with pytest.raises(DeviceGrantRejectedError) as exc:
                await auth.login("unused@example.com", "unused-password", on_verification=callback)

            request = mock.requests[("POST", URL(endpoint))][0]
            assert parse_qs(request.kwargs["data"]) == {
                "client_id": [client_id], "scope": [DEVICE_CODE_SCOPE],
            }
            assert sum(len(calls) for calls in mock.requests.values()) == 4

    message = str(exc.value)
    assert "unauthorized_client" in message
    assert "before user authentication" in message
    assert "not a username/password or S-PIN error" in message
    if description:
        assert description in message
    assert "do-not-log" not in message + caplog.text
    callback.assert_not_called()
    assert not token_file.exists()


@pytest.mark.asyncio
async def test_existing_eu_session_refreshes_and_persists_rotated_tokens(tmp_path):
    """A cold-start block must not affect the separate refresh-token grant."""
    token_file = tmp_path / "tokens.json"
    cached = {
        "bearer_token": {"access_token": "old-idk", "refresh_token": "old-idk-refresh"},
        "audi_token": {"access_token": "old-azs"},
        "vw_token": {"access_token": "old-mbb"},
        "mbb_oauth_token": {"refresh_token": "old-mbb-refresh", "expires_in": 3600},
        "xclient_id": "cached-xclient",
        "client_id": CLIENT_ID,
        "token_endpoint": "https://idp.example/token",
        "authorization_server_base_url": "https://azs.example",
        "mbb_oauth_base_url": "https://mbb.example",
        "language": "de",
        "saved_at": time.time() - 7200,
    }
    token_file.write_text(json.dumps(cached))
    store = TokenStore(str(token_file))
    callback = MagicMock()
    async with aiohttp.ClientSession() as session:
        auth = AudiAuth(AudiAPI(session), country="DE", token_store=store)
        with aioresponses() as mock:
            mock.post(
                "https://mbb.example/mobile/oauth2/v1/token",
                payload={"access_token": "new-mbb", "refresh_token": "new-mbb-refresh"},
            )
            mock.post(
                cached["token_endpoint"],
                payload={"access_token": "new-idk", "refresh_token": "new-idk-refresh"},
            )
            mock.post("https://azs.example/token", payload={"access_token": "new-azs"})
            mock.post(GRAPHQL_URL, payload={"data": {"userVehicles": []}})
            vehicles = await auth.login("unused@example.com", "unused-password", on_verification=callback)

            idk_request = mock.requests[("POST", URL(cached["token_endpoint"]))][0]
            assert parse_qs(idk_request.kwargs["data"]) == {
                "client_id": [CLIENT_ID],
                "grant_type": ["refresh_token"],
                "refresh_token": ["old-idk-refresh"],
                "response_type": ["token id_token"],
            }
            assert mock.requests[("POST", URL(GRAPHQL_URL))][0].kwargs["headers"]["Authorization"] == "Bearer new-azs"
            # Only MBB/IDK/AZS refresh + vehicle validation; no discovery/device login.
            assert sum(len(calls) for calls in mock.requests.values()) == 4

    assert vehicles == []
    callback.assert_not_called()
    persisted = store.load()
    assert persisted["bearer_token"]["refresh_token"] == "new-idk-refresh"
    assert persisted["mbb_oauth_token"]["refresh_token"] == "new-mbb-refresh"
    assert persisted["saved_at"] > cached["saved_at"]
