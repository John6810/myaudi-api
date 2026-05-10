"""Tests for audi_connect.logging_utils — secret redaction."""

import logging

import pytest

from audi_connect.logging_utils import RedactingFilter, redact


class TestRedact:
    def test_redact_bearer_token(self):
        out = redact("Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig")
        assert "Bearer ***" in out
        assert "eyJhbGciOiJSUzI1NiJ9" not in out

    def test_redact_json_access_token(self):
        out = redact('{"access_token": "abc123secret", "expires_in": 3600}')
        assert '"access_token": "***"' in out
        assert "abc123secret" not in out
        # Non-secret value left alone.
        assert "3600" in out

    def test_redact_json_refresh_token(self):
        out = redact('{"refresh_token":"r3fr3sh_v4lue"}')
        assert '"refresh_token":"***"' in out
        assert "r3fr3sh_v4lue" not in out

    def test_redact_json_password_case_insensitive(self):
        out = redact('{"PASSWORD": "hunter2"}')
        assert "hunter2" not in out
        assert "***" in out

    def test_redact_qmauth(self):
        out = redact("X-QMAuth: v1:01da27b0:abcdef0123456789deadbeef")
        assert "abcdef0123456789deadbeef" not in out
        assert "v1:01da27b0:***" in out

    def test_redact_email_keeps_first_3_chars(self):
        out = redact("Connecting as jaerts085@gmail.com to server")
        assert "jae***@gmail.com" in out
        assert "jaerts085" not in out

    def test_redact_non_string_passthrough(self):
        assert redact(42) == 42
        assert redact(None) is None
        # Dicts pass through unchanged — only string fields get redacted.
        d = {"x": "Bearer abc"}
        assert redact(d) is d

    def test_redact_combined_in_one_string(self):
        msg = (
            'POST {"access_token":"sekret"} '
            "Authorization: Bearer eyJabc.def "
            "user=jaerts085@gmail.com"
        )
        out = redact(msg)
        assert "sekret" not in out
        assert "eyJabc" not in out
        assert "jaerts085" not in out
        assert '"access_token":"***"' in out
        assert "Bearer ***" in out
        assert "jae***@gmail.com" in out


class TestRedactingFilter:
    def test_filter_returns_true_always(self):
        f = RedactingFilter()
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_redacts_record_msg_and_args(self):
        f = RedactingFilter()
        record = logging.LogRecord(
            name="t", level=logging.INFO, pathname="", lineno=0,
            msg='Authorization=%s body=%s',
            args=("Bearer eyJsecret.value", '{"access_token":"abc"}'),
            exc_info=None,
        )
        f.filter(record)
        # Each arg should have been redacted independently in place.
        assert record.args[0] == "Bearer ***"
        assert record.args[1] == '{"access_token":"***"}'
