"""OAuth2/OIDC login flow for Audi Connect — 13-step authentication."""

import asyncio
import json
import uuid
import base64
import os
import re
import logging
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from urllib.parse import urlparse, parse_qs, urlencode
from typing import Optional

import hmac
from bs4 import BeautifulSoup

from .api import AudiAPI
from .endpoints import cariad_url
from .exceptions import AuthenticationError, CountryNotSupportedError

_LOGGER = logging.getLogger(__name__)

# OAuth 2.0 Device Authorization Grant (RFC 8628).
# Since July 2026 Audi enforces Play Integrity attestation on the password
# (authorization-code) token exchange in Europe, so the legacy login can no
# longer complete there. The device-code flow does not hit that exchange and is
# therefore the working path for EU accounts. US/CA/CN keep the password flow.
DEVICE_CODE_SCOPE = "openid mbb profile badge cars dealers vin"  # "mbb" needed for legacy lock/unlock/trips/climate
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_AUTH_ENDPOINT_FALLBACK = "https://identity.vwgroup.io/oidc/v1/device_authorization"
# Regions where attestation is NOT enforced and the legacy password flow still works.
PASSWORD_FLOW_REGIONS = frozenset({"US", "CA", "CN"})


def uses_device_code(country: Optional[str]) -> bool:
    """True if the region must authenticate via the device-code flow (Europe)."""
    return (country or "DE").upper() not in PASSWORD_FLOW_REGIONS


class AudiOAuth:
    """Handles the full 13-step OAuth2/OIDC login flow for Audi Connect.

    Reverse-engineered from the Android myAudi app v4.31.0.
    Produces 3 tokens: IDK (CARIAD bearer), AZS (Audi), and MBB (VW Group).
    """

    def __init__(self, api: AudiAPI, country: str):
        self._api = api
        self._country = country or "DE"

    # --- HTML form helpers ---

    @staticmethod
    def _get_hidden_html_input_form_data(response: str, form_data: dict) -> dict:
        html = BeautifulSoup(response, "html.parser")
        form_inputs = html.find_all("input", attrs={"type": "hidden"})
        for form_input in form_inputs:
            name = form_input.get("name")
            form_data[name] = form_input.get("value")
        return form_data

    @staticmethod
    def _get_post_url(response: str, url: str) -> str:
        html = BeautifulSoup(response, "html.parser")
        form_tag = html.find("form")
        action = form_tag.get("action")
        if action.startswith("http"):
            return action
        elif action.startswith("/"):
            url_parts = urlparse(url)
            return url_parts.scheme + "://" + url_parts.netloc + action
        else:
            raise AuthenticationError("Unknown form action: " + action)

    def _get_cariad_url(self, path_and_query: str, **kwargs) -> str:
        # Thin wrapper so existing tests calling oauth._get_cariad_url(...) keep passing.
        return cariad_url(self._country, path_and_query, **kwargs)

    @staticmethod
    def _calculate_x_qmauth() -> str:
        """Compute X-QMAuth header using HMAC-SHA256.

        Uses a secret extracted from the myAudi Android APK. The timestamp
        is divided by 100 to create 100-second windows, ensuring the same
        HMAC value is produced for requests within the same window.
        """
        gmtime_100sec = int(
            datetime.now(timezone.utc).timestamp() / 100
        )
        # Secret key extracted from myAudi Android app v4.31.0 (obfuscated as byte array)
        xqmauth_secret = bytes(
            [
                26, 256 - 74, 256 - 103, 37, 256 - 84, 23, 256 - 102, 256 - 86,
                78, 256 - 125, 256 - 85, 256 - 26, 113, 256 - 87, 71, 109,
                23, 100, 24, 256 - 72, 91, 256 - 41, 6, 256 - 15,
                67, 108, 256 - 95, 91, 256 - 26, 71, 256 - 104, 256 - 100,
            ]
        )
        xqmauth_val = hmac.new(
            xqmauth_secret,
            str(gmtime_100sec).encode("ascii", "ignore"),
            digestmod="sha256",
        ).hexdigest()
        return "v1:01da27b0:" + xqmauth_val

    # --- Main login flow ---

    async def _fetch_login_config(self) -> dict:
        """Steps 1-3: market config, dynamic config, OpenID discovery.

        Shared by both the password and device-code login paths. Returns the
        endpoints and client id needed to complete either flow.
        """
        self._api.use_token(None)
        self._api.set_xclient_id(None)

        # Step 1: Get market configuration
        _LOGGER.debug("Step 1: Fetching market configuration...")
        markets_json = await self._api.request(
            "GET",
            "https://content.app.my.audi.com/service/mobileapp/configurations/markets",
            None,
        )
        if self._country.upper() not in markets_json["countries"]["countrySpecifications"]:
            raise CountryNotSupportedError(
                f"Country '{self._country}' not found in Audi markets. "
                f"Available: {list(markets_json['countries']['countrySpecifications'].keys())}"
            )
        language = markets_json["countries"]["countrySpecifications"][
            self._country.upper()
        ]["defaultLanguage"]

        # Step 2: Get dynamic config
        _LOGGER.debug("Step 2: Fetching dynamic configuration...")
        marketcfg_url = (
            f"https://content.app.my.audi.com/service/mobileapp/configurations/"
            f"market/{self._country}/{language}?v=4.23.1"
        )
        openidcfg_url = self._get_cariad_url("/auth/v1/idk/oidc/openid-configuration")
        marketcfg_json = await self._api.request("GET", marketcfg_url, None)

        client_id = "09b6cbec-cd19-4589-82fd-363dfa8c24da@apps_vw-dilab_com"
        if "idkClientIDAndroidLive" in marketcfg_json:
            client_id = marketcfg_json["idkClientIDAndroidLive"]

        authorization_server_base_url = self._get_cariad_url("/login/v1/audi")
        if "authorizationServerBaseURLLive" in marketcfg_json:
            authorization_server_base_url = marketcfg_json[
                "myAudiAuthorizationServerProxyServiceURLProduction"
            ]

        mbb_oauth_base_url = "https://mbboauth-1d.prd.ece.vwg-connect.com/mbbcoauth"
        if "mbbOAuthBaseURLLive" in marketcfg_json:
            mbb_oauth_base_url = marketcfg_json["mbbOAuthBaseURLLive"]

        # Step 3: Get OpenID configuration
        _LOGGER.debug("Step 3: Fetching OpenID configuration...")
        openidcfg_json = await self._api.request("GET", openidcfg_url, None)

        authorization_endpoint = "https://identity.vwgroup.io/oidc/v1/authorize"
        if "authorization_endpoint" in openidcfg_json:
            authorization_endpoint = openidcfg_json["authorization_endpoint"]

        token_endpoint = self._get_cariad_url("/auth/v1/idk/oidc/token")
        if "token_endpoint" in openidcfg_json:
            token_endpoint = openidcfg_json["token_endpoint"]

        device_authorization_endpoint = DEVICE_AUTH_ENDPOINT_FALLBACK
        if "device_authorization_endpoint" in openidcfg_json:
            device_authorization_endpoint = openidcfg_json["device_authorization_endpoint"]

        return {
            "language": language,
            "client_id": client_id,
            "authorization_endpoint": authorization_endpoint,
            "token_endpoint": token_endpoint,
            "device_authorization_endpoint": device_authorization_endpoint,
            "authorization_server_base_url": authorization_server_base_url,
            "mbb_oauth_base_url": mbb_oauth_base_url,
        }

    async def login(self, user: str, password: str) -> dict:
        """Password (authorization-code) login flow — non-device-code regions only.

        EU regions must use login_device_code() instead: Audi enforces Play
        Integrity attestation on the code-exchange step (step 9) there, which
        returns "invalid assertion headers". Returns a dict with all tokens and
        OAuth state needed by the client.
        """
        config = await self._fetch_login_config()
        language = config["language"]
        client_id = config["client_id"]
        authorization_endpoint = config["authorization_endpoint"]
        token_endpoint = config["token_endpoint"]
        authorization_server_base_url = config["authorization_server_base_url"]
        mbb_oauth_base_url = config["mbb_oauth_base_url"]

        # Step 4: Generate PKCE challenge
        _LOGGER.debug("Step 4: Generating PKCE code challenge...")
        code_verifier = str(
            base64.urlsafe_b64encode(os.urandom(32)), "utf-8"
        ).strip("=")
        code_challenge = str(
            base64.urlsafe_b64encode(
                sha256(code_verifier.encode("ascii", "ignore")).digest()
            ),
            "utf-8",
        ).strip("=")

        state = str(uuid.uuid4())
        nonce = str(uuid.uuid4())

        # Step 5: Get login page
        _LOGGER.debug("Step 5: Requesting login page...")
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "X-App-Name": "myAudi",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
        }
        idk_data = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": "myaudi:///",
            "scope": "address profile badge birthdate birthplace nationalIdentifier nationality profession email vin phone nickname name picture mbb gallery openid",
            "state": state,
            "nonce": nonce,
            "prompt": "login",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "ui_locales": "de-de de",
        }
        idk_rsp, idk_rsptxt = await self._api.request(
            "GET", authorization_endpoint, None,
            headers=headers, params=idk_data, rsp_wtxt=True,
        )

        # Step 6: Submit email
        _LOGGER.debug("Step 6: Submitting email...")
        submit_data = self._get_hidden_html_input_form_data(idk_rsptxt, {"email": user})
        submit_url = self._get_post_url(idk_rsptxt, authorization_endpoint)

        email_rsp, email_rsptxt = await self._api.request(
            "POST", submit_url, submit_data,
            headers=headers, cookies=idk_rsp.cookies,
            allow_redirects=True, rsp_wtxt=True,
        )

        # Step 7: Submit password
        _LOGGER.debug("Step 7: Submitting password...")
        regex_res = re.findall('"hmac"\\s*:\\s*"[0-9a-fA-F]+"', email_rsptxt)
        if regex_res:
            submit_url = submit_url.replace("identifier", "authenticate")
            submit_data["hmac"] = regex_res[0].split(":")[1].strip('"')
            submit_data["password"] = password
        else:
            submit_data = self._get_hidden_html_input_form_data(
                email_rsptxt, {"password": password}
            )
            submit_url = self._get_post_url(email_rsptxt, submit_url)

        pw_rsp, pw_rsptxt = await self._api.request(
            "POST", submit_url, submit_data,
            headers=headers, cookies=idk_rsp.cookies,
            allow_redirects=False, rsp_wtxt=True,
        )

        # Step 8: Follow redirects to get authorization code
        _LOGGER.debug("Step 8: Following redirects...")
        fwd1_rsp, _ = await self._api.request(
            "GET", pw_rsp.headers["Location"], None,
            headers=headers, cookies=idk_rsp.cookies,
            allow_redirects=False, rsp_wtxt=True,
        )
        fwd2_rsp, _ = await self._api.request(
            "GET", fwd1_rsp.headers["Location"], None,
            headers=headers, cookies=idk_rsp.cookies,
            allow_redirects=False, rsp_wtxt=True,
        )
        codeauth_rsp, _ = await self._api.request(
            "GET", fwd2_rsp.headers["Location"], None,
            headers=headers, cookies=fwd2_rsp.cookies,
            allow_redirects=False, rsp_wtxt=True,
        )

        authcode_parsed = urlparse(
            codeauth_rsp.headers["Location"][len("myaudi:///?"):]
        )
        authcode_strings = parse_qs(authcode_parsed.path)

        # Step 9: Exchange code for IDK bearer token
        _LOGGER.debug("Step 9: Exchanging authorization code for tokens...")
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-QMAuth": self._calculate_x_qmauth(),
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        tokenreq_data = {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": authcode_strings["code"][0],
            "redirect_uri": "myaudi:///",
            "response_type": "token id_token",
            "code_verifier": code_verifier,
        }
        encoded_tokenreq_data = urlencode(tokenreq_data, encoding="utf-8").replace("+", "%20")
        _, bearer_token_rsptxt = await self._api.request(
            "POST", token_endpoint, encoded_tokenreq_data,
            headers=headers, allow_redirects=False, rsp_wtxt=True,
        )
        bearer_token_json = json.loads(bearer_token_rsptxt)
        if "access_token" not in bearer_token_json:
            _LOGGER.error("Token exchange failed, response: %s", bearer_token_rsptxt)
            raise AuthenticationError(
                f"IDK token exchange did not return an access_token: {bearer_token_rsptxt[:500]}"
            )

        return await self._finalize_session(bearer_token_json, config)

    async def login_device_code(self, on_verification=None) -> dict:
        """Device Authorization Grant (RFC 8628) login — EU regions.

        Requires a one-time manual approval: the user opens the returned
        verification URL, signs in and approves. The resulting refresh token is
        then persisted so subsequent sessions refresh non-interactively.

        `on_verification`, if given, is called with a dict holding
        `verification_uri`, `verification_uri_complete`, `user_code` and
        `expires_in`, so the caller can render the approval prompt. When omitted,
        the prompt is logged at WARNING level. Returns the same token dict shape
        as login().
        """
        config = await self._fetch_login_config()
        client_id = config["client_id"]
        device_authorization_endpoint = config["device_authorization_endpoint"]
        token_endpoint = config["token_endpoint"]

        # Step D1: Request a device + user code (no attestation required here).
        _LOGGER.debug("Step D1: Requesting device authorization...")
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "X-App-Name": "myAudi",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        device_req_data = urlencode(
            {"client_id": client_id, "scope": DEVICE_CODE_SCOPE}, encoding="utf-8",
        ).replace("+", "%20")
        _, device_rsptxt = await self._api.request(
            "POST", device_authorization_endpoint, device_req_data,
            headers=headers, allow_redirects=False, rsp_wtxt=True,
        )
        device_init = json.loads(device_rsptxt)
        if "device_code" not in device_init:
            _LOGGER.error("Device authorization failed, response: %s", device_rsptxt)
            raise AuthenticationError(
                f"Device authorization did not return a device_code: {device_rsptxt[:500]}"
            )

        verification = {
            "verification_uri": device_init.get("verification_uri", ""),
            "verification_uri_complete": device_init.get("verification_uri_complete", ""),
            "user_code": device_init.get("user_code", ""),
            "expires_in": device_init.get("expires_in", 0),
        }
        self._notify_verification(verification, on_verification)

        # Step D2: Poll the token endpoint until the user approves.
        _LOGGER.debug("Step D2: Polling for device authorization...")
        bearer_token_json = await self._poll_device_token(
            token_endpoint, device_init["device_code"], client_id, device_init
        )

        return await self._finalize_session(bearer_token_json, config)

    @staticmethod
    def _notify_verification(verification: dict, on_verification) -> None:
        """Surface the device-approval prompt to the operator."""
        if on_verification is not None:
            on_verification(verification)
            return
        target = (
            verification.get("verification_uri_complete")
            or verification.get("verification_uri")
        )
        _LOGGER.warning(
            "Device approval required: open %s and sign in to approve (user code: %s).",
            target, verification.get("user_code"),
        )

    async def _poll_device_token(
        self, token_endpoint: str, device_code: str, client_id: str, device_init: dict
    ) -> dict:
        """Poll the token endpoint for the device-code grant until approved.

        Follows RFC 8628: sleep the server-provided interval, retry on
        `authorization_pending`, back off on `slow_down`, until an access token
        arrives or the request expires.
        """
        interval = max(int(device_init.get("interval", 5)), 1)
        expires_in = min(int(device_init.get("expires_in", 600)), 600)
        deadline = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        poll_data = urlencode(
            {
                "grant_type": DEVICE_CODE_GRANT_TYPE,
                "device_code": device_code,
                "client_id": client_id,
            },
            encoding="utf-8",
        ).replace("+", "%20")

        while datetime.now(timezone.utc) < deadline:
            await asyncio.sleep(interval)
            _, poll_rsptxt = await self._api.request(
                "POST", token_endpoint, poll_data,
                headers=headers, allow_redirects=False, rsp_wtxt=True,
            )
            poll = json.loads(poll_rsptxt)
            if "access_token" in poll:
                _LOGGER.debug("Device authorization approved.")
                return poll
            error = poll.get("error")
            _LOGGER.debug("Device poll status: %s", error or poll)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            raise AuthenticationError(
                f"Device authorization failed: "
                f"{poll.get('error_description') or error or poll}"
            )

        raise AuthenticationError(
            "Device authorization timed out before the user approved the request."
        )

    async def _finalize_session(self, bearer_token_json: dict, config: dict) -> dict:
        """Steps 10-13: derive AZS (Audi) + MBB (VW) tokens from the IDK bearer.

        Shared tail of both login paths — identical once an IDK bearer token has
        been obtained, whether via password or device code.
        """
        client_id = config["client_id"]
        token_endpoint = config["token_endpoint"]
        authorization_server_base_url = config["authorization_server_base_url"]
        mbb_oauth_base_url = config["mbb_oauth_base_url"]
        language = config["language"]

        # Step 10: Get AZS (Audi) token
        _LOGGER.debug("Step 10: Getting Audi AZS token...")
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "X-App-Name": "myAudi",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
        }
        asz_req_data = {
            "token": bearer_token_json["access_token"],
            "grant_type": "id_token",
            "stage": "live",
            "config": "myaudi",
        }
        _, azs_token_rsptxt = await self._api.request(
            "POST", authorization_server_base_url + "/token",
            json.dumps(asz_req_data), headers=headers,
            allow_redirects=False, rsp_wtxt=True,
        )
        audi_token = json.loads(azs_token_rsptxt)

        # Step 11: Register MBB OAuth client
        _LOGGER.debug("Step 11: Registering MBB OAuth client...")
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
        }
        mbboauth_reg_data = {
            "client_name": "SM-A405FN",  # Emulates Samsung Galaxy A40 (from Android APK)
            "platform": "google",
            "client_brand": "Audi",
            "appName": "myAudi",
            "appVersion": AudiAPI.HDR_XAPP_VERSION,
            "appId": "de.myaudi.mobile.assistant",
        }
        mbboauth_client_reg_rsp, mbboauth_client_reg_rsptxt = await self._api.request(
            "POST", mbb_oauth_base_url + "/mobile/register/v1",
            json.dumps(mbboauth_reg_data), headers=headers,
            allow_redirects=False, rsp_wtxt=True,
        )
        mbboauth_client_reg_json = json.loads(mbboauth_client_reg_rsptxt)
        xclient_id = mbboauth_client_reg_json["client_id"]

        # Step 12: Get MBB OAuth token
        _LOGGER.debug("Step 12: Getting MBB OAuth token...")
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Client-ID": xclient_id,
        }
        mbboauth_auth_data = {
            "grant_type": "id_token",
            "token": bearer_token_json["id_token"],
            "scope": "sc2:fal",
        }
        encoded_mbboauth_auth_data = urlencode(
            mbboauth_auth_data, encoding="utf-8"
        ).replace("+", "%20")
        _, mbboauth_auth_rsptxt = await self._api.request(
            "POST", mbb_oauth_base_url + "/mobile/oauth2/v1/token",
            encoded_mbboauth_auth_data, headers=headers,
            allow_redirects=False, rsp_wtxt=True,
        )
        mbboauth_auth_json = json.loads(mbboauth_auth_rsptxt)
        mbb_oauth_token = mbboauth_auth_json

        # Step 13: Refresh MBB token immediately (like the app does)
        _LOGGER.debug("Step 13: Refreshing MBB token...")
        mbboauth_refresh_data = {
            "grant_type": "refresh_token",
            "token": mbboauth_auth_json["refresh_token"],
            "scope": "sc2:fal",
        }
        encoded_mbboauth_refresh_data = urlencode(
            mbboauth_refresh_data, encoding="utf-8"
        ).replace("+", "%20")
        _, mbboauth_refresh_rsptxt = await self._api.request(
            "POST", mbb_oauth_base_url + "/mobile/oauth2/v1/token",
            encoded_mbboauth_refresh_data, headers=headers,
            allow_redirects=False, cookies=mbboauth_client_reg_rsp.cookies,
            rsp_wtxt=True,
        )
        vw_token = json.loads(mbboauth_refresh_rsptxt)

        return {
            "bearer_token": bearer_token_json,
            "audi_token": audi_token,
            "vw_token": vw_token,
            "mbb_oauth_token": mbb_oauth_token,
            "xclient_id": xclient_id,
            "client_id": client_id,
            "token_endpoint": token_endpoint,
            "authorization_server_base_url": authorization_server_base_url,
            "mbb_oauth_base_url": mbb_oauth_base_url,
            "language": language,
        }

    async def refresh_tokens(
        self,
        mbb_oauth_token: dict,
        bearer_token: dict,
        client_id: str,
        token_endpoint: str,
        authorization_server_base_url: str,
        mbb_oauth_base_url: str,
        xclient_id: str,
    ) -> dict:
        """Refresh all 3 tokens (MBB, IDK bearer, AZS).

        Returns a dict with fresh bearer_token, audi_token, vw_token, mbb_oauth_token.
        """
        # Refresh MBB token
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Client-ID": xclient_id,
        }
        mbboauth_refresh_data = {
            "grant_type": "refresh_token",
            "token": mbb_oauth_token["refresh_token"],
            "scope": "sc2:fal",
        }
        encoded = urlencode(mbboauth_refresh_data, encoding="utf-8").replace("+", "%20")
        _, rsptxt = await self._api.request(
            "POST", mbb_oauth_base_url + "/mobile/oauth2/v1/token",
            encoded, headers=headers, allow_redirects=False, rsp_wtxt=True,
        )
        vw_token = json.loads(rsptxt)

        if "refresh_token" in vw_token:
            mbb_oauth_token["refresh_token"] = vw_token["refresh_token"]

        # Refresh IDK bearer token
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-QMAuth": self._calculate_x_qmauth(),
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        tokenreq_data = {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": bearer_token.get("refresh_token"),
            "response_type": "token id_token",
        }
        encoded = urlencode(tokenreq_data, encoding="utf-8").replace("+", "%20")
        _, rsptxt = await self._api.request(
            "POST", token_endpoint, encoded,
            headers=headers, allow_redirects=False, rsp_wtxt=True,
        )
        new_bearer_token = json.loads(rsptxt)

        # Refresh AZS token
        headers = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Version": AudiAPI.HDR_XAPP_VERSION,
            "X-App-Name": "myAudi",
            "User-Agent": AudiAPI.HDR_USER_AGENT,
            "Content-Type": "application/json; charset=utf-8",
        }
        asz_req_data = {
            "token": new_bearer_token["access_token"],
            "grant_type": "id_token",
            "stage": "live",
            "config": "myaudi",
        }
        _, rsptxt = await self._api.request(
            "POST", authorization_server_base_url + "/token",
            json.dumps(asz_req_data), headers=headers,
            allow_redirects=False, rsp_wtxt=True,
        )
        audi_token = json.loads(rsptxt)

        return {
            "bearer_token": new_bearer_token,
            "audi_token": audi_token,
            "vw_token": vw_token,
            "mbb_oauth_token": mbb_oauth_token,
        }
