"""Utility functions for Audi Connect."""

from functools import reduce
from datetime import datetime, timezone
from typing import Any, Optional
import logging

_LOGGER = logging.getLogger(__name__)


def get_attr(dictionary: dict, keys: str, default: Any = None) -> Any:
    """Access a deeply nested dict value using dot-separated keys."""
    return reduce(
        lambda d, key: d.get(key, default) if isinstance(d, dict) else default,
        keys.split("."),
        dictionary,
    )


def to_byte_array(hex_string: str) -> list[int]:
    """Convert a hex string to a list of integers."""
    result = []
    for i in range(0, len(hex_string), 2):
        result.append(int(hex_string[i : i + 2], 16))
    return result


def parse_int(val: Any) -> Optional[int]:
    """Safely parse a value to int, returning None on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def parse_float(val: Any) -> Optional[float]:
    """Safely parse a value to float, returning None on failure."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_datetime(time_value: Any) -> Optional[datetime]:
    """Parse various datetime formats to a timezone-aware datetime."""
    if isinstance(time_value, datetime):
        return time_value
    elif isinstance(time_value, str):
        formats = [
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(time_value, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None
