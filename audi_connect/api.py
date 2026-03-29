"""Low-level HTTP client for Audi Connect API calls."""

import json
import logging
import asyncio
from datetime import datetime
from typing import Any, Optional, Union
from asyncio import TimeoutError, CancelledError
from aiohttp import ClientSession, ClientResponse, ClientResponseError
from aiohttp.hdrs import METH_GET, METH_POST, METH_PUT

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .exceptions import RequestTimeoutError

TIMEOUT = 30
MAX_RETRIES = 3
_LOGGER = logging.getLogger(__name__)


class AudiAPI:
    HDR_XAPP_VERSION: str = "4.31.0"
    HDR_USER_AGENT: str = (
        "Android/4.31.0 (Build 800341641.root project "
        "'myaudi_android'.ext.buildTime) Android/13"
    )

    def __init__(self, session: ClientSession, proxy: Optional[str] = None):
        self._token: Optional[dict] = None
        self._xclient_id: Optional[str] = None
        self._session = session
        self._proxy: Optional[dict] = {"http": proxy, "https": proxy} if proxy else None

    def use_token(self, token: Optional[dict]) -> None:
        self._token = token

    def set_xclient_id(self, xclient_id: Optional[str]) -> None:
        self._xclient_id = xclient_id

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((RequestTimeoutError, ConnectionError, OSError)),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        url: str,
        data: Any,
        headers: Optional[dict] = None,
        raw_reply: bool = False,
        raw_contents: bool = False,
        rsp_wtxt: bool = False,
        **kwargs: Any,
    ) -> Union[dict, bytes, ClientResponse, tuple[ClientResponse, str]]:
        try:
            async with asyncio.timeout(TIMEOUT):
                async with self._session.request(
                    method, url, headers=headers, data=data, **kwargs
                ) as response:
                    if raw_reply:
                        return response

                    if rsp_wtxt:
                        txt = await response.text()
                        return response, txt

                    elif raw_contents:
                        return await response.read()

                    elif response.status in (200, 202, 207):
                        raw_body = await response.text()
                        return json_loads(raw_body)

                    else:
                        raise ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=response.reason,
                        )

        except CancelledError:
            raise RequestTimeoutError(f"Request cancelled/timed out: {url}")
        except TimeoutError:
            raise RequestTimeoutError(f"Request timed out after {TIMEOUT}s: {url}")

    async def get(self, url: str, raw_reply: bool = False, raw_contents: bool = False, **kwargs: Any) -> Any:
        headers = self._get_headers()
        return await self.request(
            METH_GET, url, data=None, headers=headers,
            raw_reply=raw_reply, raw_contents=raw_contents, **kwargs,
        )

    async def put(self, url: str, data: Any = None, headers: Optional[dict] = None) -> Any:
        full_headers = self._get_headers()
        if headers:
            full_headers.update(headers)
        return await self.request(METH_PUT, url, headers=full_headers, data=data)

    async def post(
        self,
        url: str,
        data: Any = None,
        headers: Optional[dict] = None,
        use_json: bool = True,
        raw_reply: bool = False,
        raw_contents: bool = False,
        **kwargs: Any,
    ) -> Any:
        full_headers = self._get_headers()
        if headers:
            full_headers.update(headers)
        if use_json and data is not None:
            data = json.dumps(data)
        return await self.request(
            METH_POST, url, headers=full_headers, data=data,
            raw_reply=raw_reply, raw_contents=raw_contents, **kwargs,
        )

    def _get_headers(self) -> dict[str, str]:
        data = {
            "Accept": "application/json",
            "Accept-Charset": "utf-8",
            "X-App-Version": self.HDR_XAPP_VERSION,
            "X-App-Name": "myAudi",
            "User-Agent": self.HDR_USER_AGENT,
        }
        if self._token is not None:
            data["Authorization"] = "Bearer " + self._token.get("access_token")
        if self._xclient_id is not None:
            data["X-Client-ID"] = self._xclient_id
        return data


def obj_parser(obj: dict) -> dict:
    for key, val in obj.items():
        try:
            obj[key] = datetime.strptime(val, "%Y-%m-%dT%H:%M:%S%z")
        except (TypeError, ValueError):
            pass
    return obj


def json_loads(s: str) -> Any:
    return json.loads(s, object_hook=obj_parser)
