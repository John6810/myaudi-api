"""Shared connection helpers for creating sessions and connecting to Audi Connect."""

import ssl
from typing import Optional

import aiohttp
import certifi

from .api import AudiAPI
from .auth import AudiAuth
from .vehicle import AudiVehicle


def create_session() -> aiohttp.ClientSession:
    """Create an aiohttp session with proper SSL using certifi CA bundle."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    return aiohttp.ClientSession(connector=connector)


async def connect_and_get_vehicles(
    session: aiohttp.ClientSession,
    username: str,
    password: str,
    country: str = "DE",
    spin: Optional[str] = None,
    api_level: int = 1,
) -> tuple[AudiAuth, list[AudiVehicle]]:
    """Authenticate to Audi Connect and return (auth, vehicles)."""
    api = AudiAPI(session)
    auth = AudiAuth(api, country=country, spin=spin, api_level=api_level)

    print("Connecting to Audi Connect...")
    await auth.login(username, password)
    print("Connected!\n")

    print("Fetching vehicles...")
    vehicle_list = await auth.get_vehicle_list()
    print(f"{len(vehicle_list)} vehicle(s) found\n")

    vehicles = [AudiVehicle(auth, v_info) for v_info in vehicle_list]
    return auth, vehicles
