"""Logging utilities — secret redaction filter and helpers.

Install once at startup:

    import logging
    from audi_connect.logging_utils import RedactingFilter
    for h in logging.getLogger().handlers:
        h.addFilter(RedactingFilter())
"""

import logging
import re
from typing import Any

_JSON_KEY_PATTERN = re.compile(
    r'("(?:access_token|refresh_token|id_token|securityToken|'
    r'securityPinHash|hmac|password|spin|client_secret|code_verifier)"\s*:\s*")'
    r'[^"]+(")',
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r'(Bearer\s+)[A-Za-z0-9._\-]+', re.IGNORECASE)
_QMAUTH_PATTERN = re.compile(r'(v1:[a-f0-9]+:)[a-f0-9]{8,}', re.IGNORECASE)
_EMAIL_PATTERN = re.compile(
    r'([A-Za-z0-9._%+\-]{1,3})[A-Za-z0-9._%+\-]*(@[A-Za-z0-9.\-]+)'
)


def redact(value: Any) -> Any:
    """Redact sensitive substrings in a value's string form.
    Non-strings are returned untouched."""
    if not isinstance(value, str):
        return value
    s = _JSON_KEY_PATTERN.sub(r'\1***\2', value)
    s = _BEARER_PATTERN.sub(r'\1***', s)
    s = _QMAUTH_PATTERN.sub(r'\1***', s)
    s = _EMAIL_PATTERN.sub(r'\1***\2', s)
    return s


class RedactingFilter(logging.Filter):
    """Logging filter that redacts secrets from message and args.

    Mutates the LogRecord in place. Always returns True (never drops
    records — emit unredacted is preferable to swallowing on error).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: redact(v) for k, v in record.args.items()}
                else:
                    record.args = tuple(redact(a) for a in record.args)
        except Exception:
            pass
        return True
