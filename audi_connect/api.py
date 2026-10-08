"""Low-level HTTP client for Audi Connect API calls."""

import json
import logging
import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional, Union
from asyncio import TimeoutError
from aiohttp import ClientSession, ClientResponse, ClientResponseError, ClientConnectionError
from aiohttp.hdrs import METH_GET

from tenacity import retry, stop_after_attempt, wait_exponential

from .exceptions import RequestTimeoutError

TIMEOUT = 30
MAX_RETRIES = 3
RATE_LIMIT_BACKOFF = 60 * 60
_LOGGER = logging.getLogger(__name__)


def _retry_read_request(state) -> bool:
    """Only replay reads: a lost POST response may hide a completed action."""
    method = state.kwargs.get("method", state.args[1] if len(state.args) > 1 else "")
    return method.upper() in {"GET", "HEAD", "OPTIONS"} and isinstance(
        state.outcome.exception(),
        (RequestTimeoutError, ConnectionError, OSError, ClientConnectionError),
    )


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
        self._rate_limit_until = 0.0
        self._rate_limit_error: Optional[ClientResponseError] = None

    @property
    def retry_after(self) -> float:
        return max(0.0, self._rate_limit_until - time.monotonic())

    def _set_rate_limit(self, response: ClientResponse) -> None:
        delay = RATE_LIMIT_BACKOFF
        value = response.headers.get("Retry-After", "")
        try:
            delay = max(delay, int(value))
        except ValueError:
            try:
                until = parsedate_to_datetime(value)
                delay = max(delay, (until - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
        self._rate_limit_until = time.monotonic() + delay
        self._rate_limit_error = ClientResponseError(
            response.request_info, response.history, status=429,
            message="Audi rate limit reached; requests paused", headers=response.headers,
        )

    def use_token(self, token: Optional[dict]) -> None:
        self._token = token

    def set_xclient_id(self, xclient_id: Optional[str]) -> None:
        self._xclient_id = xclient_id

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=_retry_read_request,
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
    ) -> Union[dict, bytes, ClientResponse, tuple[ClientResponse, str], None]:
        if self.retry_after:
            raise self._rate_limit_error
        try:
            async with asyncio.timeout(TIMEOUT):
                async with self._session.request(
                    method, url, headers=headers, data=data, **kwargs
                ) as response:
                    if response.status == 429:
                        self._set_rate_limit(response)
                        raise self._rate_limit_error
                    if raw_reply:
                        return response

                    if rsp_wtxt:
                        txt = await response.text()
                        return response, txt

                    elif raw_contents:
                        return await response.read()

                    elif 200 <= response.status < 300:
                        raw_body = await response.text()
                        return json.loads(raw_body) if raw_body.strip() else None

                    else:
                        raise ClientResponseError(
                            response.request_info,
                            response.history,
                            status=response.status,
                            message=response.reason,
                            headers=response.headers,
                        )

        except TimeoutError:
            raise RequestTimeoutError(f"Request timed out after {TIMEOUT}s: {url}")

    async def get(self, url: str, raw_reply: bool = False, raw_contents: bool = False, **kwargs: Any) -> Any:
        headers = self._get_headers()
        return await self.request(
            METH_GET, url, data=None, headers=headers,
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
