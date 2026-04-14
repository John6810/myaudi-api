#!/usr/bin/env python3
"""
Audi Connect - Standalone connection script
Based on https://github.com/audiconnect/audi_connect_ha/
"""

import asyncio
import argparse
import logging
import sys
import os
import webbrowser

from dotenv import load_dotenv

from audi_connect.connection import create_session, connect_and_get_vehicles
from audi_connect.watcher import check_vehicles
from audi_connect.exceptions import (
    AudiConnectError,
    AuthenticationError,
    SpinRequiredError,
    CountryNotSupportedError,
    RequestTimeoutError,
)

load_dotenv()

# Default VIN from .env (avoids --vin on every call for single-vehicle users)
DEFAULT_VIN = os.getenv("AUDI_DEFAULT_VIN")


def _format_error(e: Exception) -> str:
    """Convert exceptions to user-friendly messages."""
    if isinstance(e, AuthenticationError):
        return "Authentication failed. Check your AUDI_USERNAME and AUDI_PASSWORD."
    if isinstance(e, SpinRequiredError):
        return "This action requires an S-PIN. Set AUDI_SPIN in your .env file."
    if isinstance(e, CountryNotSupportedError):
        return f"Country not supported: {e}"
    if isinstance(e, RequestTimeoutError):
        return "Request timed out. Check your internet connection and try again."
    if isinstance(e, AudiConnectError):
        return f"Audi Connect error: {e}"
    return f"Unexpected error: {e}"


def _resolve_vin(args) -> str | None:
    """Return the VIN to use: explicit --vin > env AUDI_DEFAULT_VIN > None."""
    return args.vin or DEFAULT_VIN


async def for_each_vehicle(args, callback):
    """Helper: connect, filter by VIN, and call callback for each vehicle."""
    async with create_session() as session:
        auth, vehicles = await connect_and_get_vehicles(
            session, args.username, args.password, args.country, args.spin, args.api_level
        )
        target_vin = _resolve_vin(args)
        for vehicle in vehicles:
            if target_vin and vehicle.vin.upper() != target_vin.upper():
                continue
            await callback(vehicle, args)


async def cmd_status(args):
    """Display vehicle status (full or brief)."""
    async def _show_status(vehicle, args):
        await vehicle.update()

        if args.brief:
            brief = vehicle.get_brief()
            print(f"  {brief.get('vehicle', vehicle.vin)}")
            print(f"  Locked:   {brief.get('locked', '?')}")
            print(f"  Position: {brief.get('position', '?')}")
            if "range" in brief:
                print(f"  Range:    {brief['range']}")
            if "battery" in brief:
                print(f"  Battery:  {brief['battery']}")
            if "fuel" in brief:
                print(f"  Fuel:     {brief['fuel']}")
            print()
            return

        print(f"{'=' * 60}")
        print(f"  {vehicle.title or 'Vehicle'} - {vehicle.model}")
        print(f"  VIN: {vehicle.vin}")
        print(f"{'=' * 60}\n")

        dashboard = vehicle.get_dashboard()
        max_key_len = max(len(k) for k in dashboard) if dashboard else 0

        for key, value in dashboard.items():
            if key in ("vehicle", "vin"):
                continue
            label = key.replace("_", " ").title()
            print(f"  {label:<{max_key_len + 5}} {value}")
        print()

    await for_each_vehicle(args, _show_status)


async def cmd_position(args):
    """Display vehicle position."""
    async def _show_position(vehicle, args):
        await vehicle.update()
        pos = vehicle.position

        print(f"Vehicle: {vehicle.title} ({vehicle.vin})")
        if pos and pos.get("latitude"):
            lat, lon = pos["latitude"], pos["longitude"]
            print(f"Position: {lat}, {lon}")
            maps_url = f"https://www.google.com/maps?q={lat},{lon}"
            print(f"Google Maps: {maps_url}")
            if args.open_maps:
                webbrowser.open(maps_url)
                print("(Opened in browser)")
        elif vehicle.is_moving:
            print("Vehicle is moving")
        else:
            print("Position not available")
        print()

    await for_each_vehicle(args, _show_position)


async def cmd_lock(args):
    """Lock the vehicle."""
    async def _lock(vehicle, args):
        print(f"Locking {vehicle.title} ({vehicle.vin})...")
        await vehicle.lock()
        print("Lock command sent!")
        if args.confirm:
            print("Waiting for confirmation...")
            await asyncio.sleep(5)
            await vehicle.update()
            status = vehicle.doors_trunk_status
            print(f"Vehicle status: {status}")

    await for_each_vehicle(args, _lock)


async def cmd_unlock(args):
    """Unlock the vehicle."""
    async def _unlock(vehicle, args):
        print(f"Unlocking {vehicle.title} ({vehicle.vin})...")
        await vehicle.unlock()
        print("Unlock command sent!")
        if args.confirm:
            print("Waiting for confirmation...")
            await asyncio.sleep(5)
            await vehicle.update()
            status = vehicle.doors_trunk_status
            print(f"Vehicle status: {status}")

    await for_each_vehicle(args, _unlock)


async def cmd_climate_start(args):
    """Start climate control."""
    async def _start(vehicle, args):
        temp = args.temp or 21.0
        print(f"Starting climate control ({temp}°C) for {vehicle.title}...")
        await vehicle.start_climatisation(temp_c=temp)
        print("Climate control command sent!")

    await for_each_vehicle(args, _start)


async def cmd_climate_stop(args):
    """Stop climate control."""
    async def _stop(vehicle, args):
        print(f"Stopping climate control for {vehicle.title}...")
        await vehicle.stop_climatisation()
        print("Climate stop command sent!")

    await for_each_vehicle(args, _stop)


async def cmd_heater_start(args):
    """Start auxiliary heater."""
    async def _start(vehicle, args):
        duration = args.duration or 30
        print(f"Starting heater ({duration} min) for {vehicle.title}...")
        await vehicle.start_preheater(duration=duration)
        print("Heater command sent!")

    await for_each_vehicle(args, _start)


async def cmd_heater_stop(args):
    """Stop auxiliary heater."""
    async def _stop(vehicle, args):
        print(f"Stopping heater for {vehicle.title}...")
        await vehicle.stop_preheater()
        print("Heater stop command sent!")

    await for_each_vehicle(args, _stop)


async def cmd_watch(args):
    """Watch vehicle status and print changes."""
    interval = args.interval
    webhook_url = os.getenv("AUDI_WEBHOOK_URL")
    print(f"Watching vehicle status every {interval}s (Ctrl+C to stop)\n")

    prev_states: dict[str, dict] = {}

    async def _on_initial(vehicle, state):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {vehicle.title} — Initial state:")
        for key, val in state.items():
            if key not in ("vehicle", "maps"):
                print(f"  {key}: {val}")

    async def _on_change(vehicle, changes, state):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {vehicle.title} — Changes detected:")
        for key, diff in changes.items():
            print(f"  {key}: {diff['old']} -> {diff['new']}")
        if webhook_url:
            await _send_webhook(webhook_url, vehicle, state, changes)

    async def _on_error(vehicle, exc):
        print(f"[!] Update failed for {vehicle.vin}: {exc}")

    async with create_session() as session:
        auth, vehicles = await connect_and_get_vehicles(
            session, args.username, args.password, args.country, args.spin, args.api_level
        )
        target_vin = _resolve_vin(args)

        while True:
            await check_vehicles(
                vehicles, prev_states,
                on_change=_on_change,
                on_initial=_on_initial,
                on_error=_on_error,
                target_vin=target_vin,
            )
            await asyncio.sleep(interval)


async def _send_webhook(url: str, vehicle, state: dict, changes: dict) -> None:
    """POST state changes to a webhook URL."""
    import aiohttp
    payload = {
        "event": "state_change",
        "vin": vehicle.vin,
        "title": vehicle.title,
        "changes": changes,
        "state": state,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status < 300:
                    print(f"  [webhook] Sent to {url} (HTTP {resp.status})")
                else:
                    print(f"  [webhook] Failed: HTTP {resp.status}")
    except Exception as e:
        print(f"  [webhook] Error: {e}")


def cmd_setup(args):
    """Interactive setup — create .env file with credentials."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    if os.path.exists(env_path):
        print(f".env file already exists at {env_path}")
        resp = input("Overwrite? [y/N] ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    print("\n--- Audi Connect Setup ---\n")
    username = input("myAudi email: ").strip()
    password = input("myAudi password: ").strip()
    country = input("Country code [DE]: ").strip().upper() or "DE"
    spin = input("S-PIN (for lock/unlock, leave empty to skip): ").strip()
    api_level = input("API level (0=legacy, 1=new CARIAD) [1]: ").strip() or "1"
    default_vin = input("Default VIN (leave empty to skip): ").strip()
    webhook_url = input("Webhook URL for notifications (leave empty to skip): ").strip()

    lines = [
        f"AUDI_USERNAME={username}",
        f"AUDI_PASSWORD={password}",
        f"AUDI_COUNTRY={country}",
    ]
    if spin:
        lines.append(f"AUDI_SPIN={spin}")
    lines.append(f"AUDI_API_LEVEL={api_level}")
    if default_vin:
        lines.append(f"AUDI_DEFAULT_VIN={default_vin}")
    if webhook_url:
        lines.append(f"AUDI_WEBHOOK_URL={webhook_url}")

    with open(env_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n.env saved to {env_path}")
    print("Test your connection with: python main.py status --brief")


def main():
    parser = argparse.ArgumentParser(
        description="Audi Connect - Control your Audi vehicle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py setup                      # Interactive setup
  python main.py status --brief             # Quick status (locked, position, range)
  python main.py status                     # Full vehicle status
  python main.py position --open-maps       # Show position and open in browser
  python main.py lock --confirm             # Lock and wait for confirmation
  python main.py climate-start --temp 22
  python main.py watch --interval 300       # Monitor changes every 5 min

Environment variables (or .env file):
  AUDI_USERNAME      myAudi account email
  AUDI_PASSWORD      Password
  AUDI_COUNTRY       Country code (DE, FR, US, etc.)
  AUDI_SPIN          S-PIN (for lock/unlock)
  AUDI_API_LEVEL     API level (0=legacy, 1=new)
  AUDI_DEFAULT_VIN   Default VIN (skip --vin for single-vehicle)
  AUDI_WEBHOOK_URL   Webhook URL for watch mode notifications
""",
    )

    parser.add_argument("-u", "--username", default=os.getenv("AUDI_USERNAME"), help="myAudi account email")
    parser.add_argument("-p", "--password", default=os.getenv("AUDI_PASSWORD"), help="Password")
    parser.add_argument("-c", "--country", default=os.getenv("AUDI_COUNTRY", "DE"), help="Country code (default: DE)")
    parser.add_argument("--spin", default=os.getenv("AUDI_SPIN"), help="S-PIN for lock/unlock")
    parser.add_argument("--api-level", type=int, default=int(os.getenv("AUDI_API_LEVEL", "1")), choices=[0, 1], help="API level (0=legacy, 1=new CARIAD)")
    parser.add_argument("--vin", help="Specific VIN (if multiple vehicles)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug mode")

    # Shared parent so -v works both before and after the subcommand
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("-v", "--verbose", action="store_true", help="Debug mode")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Setup
    subparsers.add_parser("setup", parents=[shared], help="Interactive setup — create .env file")

    # Status
    status_parser = subparsers.add_parser("status", parents=[shared], help="Show vehicle status")
    status_parser.add_argument("--brief", action="store_true", help="Show only essentials (locked, position, range)")

    # Position
    position_parser = subparsers.add_parser("position", parents=[shared], help="Show vehicle position")
    position_parser.add_argument("--open-maps", action="store_true", help="Open Google Maps in browser")

    # Lock / Unlock
    lock_parser = subparsers.add_parser("lock", parents=[shared], help="Lock the vehicle (requires S-PIN)")
    lock_parser.add_argument("--confirm", action="store_true", help="Wait and confirm action")
    unlock_parser = subparsers.add_parser("unlock", parents=[shared], help="Unlock the vehicle (requires S-PIN)")
    unlock_parser.add_argument("--confirm", action="store_true", help="Wait and confirm action")

    # Climate
    climate_start = subparsers.add_parser("climate-start", parents=[shared], help="Start climate control")
    climate_start.add_argument("--temp", type=float, default=21.0, help="Temperature in °C (default: 21)")
    subparsers.add_parser("climate-stop", parents=[shared], help="Stop climate control")

    # Heater
    heater_start = subparsers.add_parser("heater-start", parents=[shared], help="Start auxiliary heater")
    heater_start.add_argument("--duration", type=int, default=30, help="Duration in minutes (default: 30)")
    subparsers.add_parser("heater-stop", parents=[shared], help="Stop auxiliary heater")

    # Watch
    watch_parser = subparsers.add_parser("watch", parents=[shared], help="Monitor vehicle and report changes")
    watch_parser.add_argument("--interval", type=int, default=300, help="Poll interval in seconds (default: 300)")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Setup doesn't need credentials
    if args.command == "setup":
        cmd_setup(args)
        return

    if not args.username or not args.password:
        print("Error: Username and password are required.")
        print("Run 'python main.py setup' to create a .env file, or use --username/--password.")
        sys.exit(1)

    commands = {
        "status": cmd_status,
        "position": cmd_position,
        "lock": cmd_lock,
        "unlock": cmd_unlock,
        "climate-start": cmd_climate_start,
        "climate-stop": cmd_climate_stop,
        "heater-start": cmd_heater_start,
        "heater-stop": cmd_heater_stop,
        "watch": cmd_watch,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        try:
            asyncio.run(cmd_func(args))
        except KeyboardInterrupt:
            print("\nStopped.")
        except Exception as e:
            print(f"\n{_format_error(e)}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
