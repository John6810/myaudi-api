"""URL building and home-region resolution shared by client, actions, and OAuth.

Encapsulates the two pieces of cross-module knowledge that previously caused
client and actions to reach into each other's private methods:
  - CARIAD URL composition (region by country)
  - Legacy MBB home-region discovery (per-VIN, cached)
"""

from typing import Optional

from .api import AudiAPI


def cariad_url(country: str, path_and_query: str, **kwargs: object) -> str:
    """Build a CARIAD API URL for the given country region.

    Stateless. Used during OAuth (before any per-vehicle context exists).
    """
    region = "emea" if country.upper() != "US" else "na"
    base_url = f"https://{region}.bff.cariad.digital"
    return base_url.rstrip("/") + "/" + path_and_query.format(**kwargs).lstrip("/")


class AudiEndpoints:
    """Resolves Audi/CARIAD URLs and per-VIN home regions.

    Owns the home-region cache so client and actions share the same lookups.
    The VW token used for home-region discovery is set after login (and
    refreshed via :meth:`set_vw_token`).
    """

    def __init__(self, api: AudiAPI, country: str, api_level: int = 1):
        self._api = api
        self._country = (country or "DE").upper()
        self._api_level = api_level
        self._vw_token: Optional[dict] = None
        self._home_region: dict[str, str] = {}
        self._home_region_setter: dict[str, str] = {}

    def set_vw_token(self, token: Optional[dict]) -> None:
        self._vw_token = token

    # --- URL builders (stateless) ---

    def cariad_url(self, path_and_query: str, **kwargs: object) -> str:
        return cariad_url(self._country, path_and_query, **kwargs)

    def cariad_url_for_vin(self, vin: str, path_and_query: str, **kwargs: object) -> str:
        base = self.cariad_url("/vehicle/v1/vehicles/{vin}", vin=vin.upper())
        return base.rstrip("/") + "/" + path_and_query.format(**kwargs).lstrip("/")

    # --- Home region (cached, one upstream call per VIN) ---

    async def home_region(self, vin: str) -> str:
        if self._home_region.get(vin) is None:
            await self._fill(vin)
        return self._home_region[vin]

    async def home_region_setter(self, vin: str) -> str:
        if self._home_region_setter.get(vin) is None:
            await self._fill(vin)
        return self._home_region_setter[vin]

    async def _fill(self, vin: str) -> None:
        if self._country != "US" and self._api_level == 1:
            self._home_region[vin] = "https://mal-3a.prd.eu.dp.vwg-connect.com"
            self._home_region_setter[vin] = "https://mal-3a.prd.eu.dp.vwg-connect.com"
            return

        self._home_region[vin] = "https://msg.volkswagen.de"
        self._home_region_setter[vin] = "https://mal-1a.prd.ece.vwg-connect.com"

        try:
            self._api.use_token(self._vw_token)
            res = await self._api.get(
                f"https://mal-1a.prd.ece.vwg-connect.com/api/cs/vds/v1/vehicles/{vin}/homeRegion"
            )
            if (
                res is not None
                and res.get("homeRegion") is not None
                and res["homeRegion"].get("baseUri") is not None
                and res["homeRegion"]["baseUri"].get("content") is not None
            ):
                uri = res["homeRegion"]["baseUri"]["content"]
                if uri != "https://mal-1a.prd.ece.vwg-connect.com/api":
                    self._home_region_setter[vin] = uri.split("/api")[0]
                    self._home_region[vin] = self._home_region_setter[vin].replace("mal-", "fal-")
        except Exception:
            pass
