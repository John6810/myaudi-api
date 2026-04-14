"""OAuth2/OIDC login flow for Audi Connect — 13-step authentication."""

import json
import uuid
import base64
import os
import re
import logging
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlparse, parse_qs, urlencode
from typing import Optional

import hmac
from bs4 import BeautifulSoup

from .api import AudiAPI
from .exceptions import AuthenticationError, CountryNotSupportedError

_LOGGER = logging.getLogger(__name__)


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
        region = "emea" if self._country.upper() != "US" else "na"
        base_url = f"https://{region}.bff.cariad.digital"
        action_path = path_and_query.format(**kwargs)
        return base_url.rstrip("/") + "/" + action_path.lstrip("/")

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

    async def login(self, user: str, password: str) -> dict:
        """Execute the full 13-step OAuth2 login flow.

        Returns a dict with all tokens and OAuth state needed by the client.
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
        openidcfg_url = self._get_cariad_url("/login/v1/idk/openid-configuration")
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

        token_endpoint = self._get_cariad_url("/login/v1/idk/token")
        if "token_endpoint" in openidcfg_json:
            token_endpoint = openidcfg_json["token_endpoint"]

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
