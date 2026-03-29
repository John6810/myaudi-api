"""Tests for audi_connect.utils module."""

import pytest
from datetime import datetime, timezone

from audi_connect.utils import get_attr, to_byte_array, parse_int, parse_float, parse_datetime


class TestGetAttr:
    def test_simple_key(self):
        assert get_attr({"a": 1}, "a") == 1

    def test_nested_keys(self):
        data = {"a": {"b": {"c": 42}}}
        assert get_attr(data, "a.b.c") == 42

    def test_missing_key_returns_default(self):
        assert get_attr({"a": 1}, "b") is None

    def test_missing_nested_key_returns_default(self):
        assert get_attr({"a": {"b": 1}}, "a.c") is None

    def test_custom_default(self):
        assert get_attr({"a": 1}, "b", default="nope") == "nope"

    def test_non_dict_intermediate(self):
        data = {"a": "not_a_dict"}
        assert get_attr(data, "a.b") is None

    def test_empty_dict(self):
        assert get_attr({}, "a") is None

    def test_returns_list(self):
        data = {"a": {"b": [1, 2, 3]}}
        assert get_attr(data, "a.b") == [1, 2, 3]


class TestToByteArray:
    def test_simple_hex(self):
        assert to_byte_array("0a0b0c") == [10, 11, 12]

    def test_empty_string(self):
        assert to_byte_array("") == []

    def test_ff(self):
        assert to_byte_array("ff") == [255]

    def test_multi_bytes(self):
        assert to_byte_array("deadbeef") == [0xDE, 0xAD, 0xBE, 0xEF]


class TestParseInt:
    def test_valid_int(self):
        assert parse_int(42) == 42

    def test_valid_string(self):
        assert parse_int("123") == 123

    def test_float_string(self):
        # int("1.5") raises ValueError
        assert parse_int("1.5") is None

    def test_none(self):
        assert parse_int(None) is None

    def test_invalid_string(self):
        assert parse_int("abc") is None

    def test_zero(self):
        assert parse_int(0) == 0

    def test_negative(self):
        assert parse_int("-5") == -5


class TestParseFloat:
    def test_valid_float(self):
        assert parse_float(3.14) == 3.14

    def test_valid_string(self):
        assert parse_float("2.5") == 2.5

    def test_int_string(self):
        assert parse_float("10") == 10.0

    def test_none(self):
        assert parse_float(None) is None

    def test_invalid_string(self):
        assert parse_float("abc") is None


class TestParseDatetime:
    def test_datetime_passthrough(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert parse_datetime(dt) is dt

    def test_iso_format_with_z(self):
        result = parse_datetime("2024-06-15T10:30:00.000Z")
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.hour == 10

    def test_format_with_timezone(self):
        result = parse_datetime("2024-06-15 10:30:00+0000")
        assert result is not None
        assert result.year == 2024

    def test_invalid_string(self):
        assert parse_datetime("not a date") is None

    def test_none(self):
        assert parse_datetime(None) is None

    def test_int(self):
        assert parse_datetime(12345) is None
