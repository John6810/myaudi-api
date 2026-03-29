"""Vehicle state watcher — shared logic for CLI watch mode and API background polling."""

import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable

from .vehicle import AudiVehicle

_LOGGER = logging.getLogger(__name__)

# Keys to ignore when comparing states (not actual vehicle state)
_IGNORED_KEYS = frozenset({"vehicle", "maps"})


def diff_states(prev: dict, current: dict) -> dict[str, dict]:
    """Compare two brief state dicts and return the changed fields.

    Returns:
        Dict of {field: {"old": ..., "new": ...}} for each changed field.
    """
    changes = {}
    for key in current:
        if key in _IGNORED_KEYS:
            continue
        if prev.get(key) != current.get(key):
            changes[key] = {"old": prev.get(key, "?"), "new": current[key]}
    return changes


async def check_vehicles(
    vehicles: list[AudiVehicle],
    prev_states: dict[str, dict],
    on_change: Optional[Callable[[AudiVehicle, dict, dict], Awaitable[None]]] = None,
    on_initial: Optional[Callable[[AudiVehicle, dict], Awaitable[None]]] = None,
    on_error: Optional[Callable[[AudiVehicle, Exception], Awaitable[None]]] = None,
    target_vin: Optional[str] = None,
) -> None:
    """Poll each vehicle, compute diffs, and fire callbacks on changes.

    Args:
        vehicles: List of vehicles to check.
        prev_states: Mutable dict of {vin: brief_state} — updated in-place.
        on_change: Called with (vehicle, changes_dict, current_state) when state changes.
        on_initial: Called with (vehicle, current_state) on first poll.
        on_error: Called with (vehicle, exception) when update fails.
        target_vin: If set, only check this VIN.
    """
    for vehicle in vehicles:
        if target_vin and vehicle.vin.upper() != target_vin.upper():
            continue

        try:
            await vehicle.update()
        except Exception as e:
            _LOGGER.error("Update failed for %s: %s", vehicle.vin, e)
            if on_error:
                await on_error(vehicle, e)
            continue

        current = vehicle.get_brief()
        prev = prev_states.get(vehicle.vin, {})

        if not prev:
            if on_initial:
                await on_initial(vehicle, current)
        else:
            changes = diff_states(prev, current)
            if changes and on_change:
                await on_change(vehicle, changes, current)

        prev_states[vehicle.vin] = current
