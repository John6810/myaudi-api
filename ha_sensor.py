#!/usr/bin/env python3
"""
Home Assistant command_line sensor script.
Outputs vehicle data as JSON to stdout.
"""

import asyncio
import json
import sys
import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from audi_connect.connection import create_session, connect_and_get_vehicles


async def get_vehicle_data() -> dict:
    async with create_session() as session:
        _, vehicles = await connect_and_get_vehicles(
            session,
            username=os.getenv("AUDI_USERNAME"),
            password=os.getenv("AUDI_PASSWORD"),
            country=os.getenv("AUDI_COUNTRY", "DE"),
            spin=os.getenv("AUDI_SPIN"),
            api_level=int(os.getenv("AUDI_API_LEVEL", "1")),
        )

        if not vehicles:
            return {}

        vehicle = vehicles[0]
        await vehicle.update()

        pos = vehicle.position
        return {
            "vin": vehicle.vin,
            "model": vehicle.model,
            "mileage": vehicle.mileage,
            "range": vehicle.range_km,
            "battery_soc": vehicle.state_of_charge,
            "charging_state": vehicle.charging_state,
            "plug_state": vehicle.plug_state,
            "latitude": pos.get("latitude") if pos else None,
            "longitude": pos.get("longitude") if pos else None,
            "doors_locked": not vehicle.any_door_unlocked,
            "windows_closed": not vehicle.any_window_open,
            "climatisation": vehicle.climatisation_state,
        }


def main():
    try:
        data = asyncio.run(get_vehicle_data())
        print(json.dumps(data))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
