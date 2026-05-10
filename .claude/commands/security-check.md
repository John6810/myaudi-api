---
description: Run a security review specific to myaudi-api (auth, secrets, S-PIN, idempotency, rate limits)
allowed-tools: Read, Grep, Glob, Bash(git diff*), Bash(git log*)
---

Run a security review of the current changes (or working tree if nothing staged). This project has very specific risks — go through each one explicitly.

## 1. Credentials & secrets in logs

Search for any `_LOGGER.info`, `_LOGGER.debug`, `print()`, or `log.info` that might leak:
- `password`, `Password`, `PASSWORD`
- `spin`, `S-PIN`, `AUDI_SPIN`
- `access_token`, `refresh_token`, `id_token`, `bearer_token`
- `securityToken`, `securityPinHash`
- `xqmauth`, `X-QMAuth`

Run: `grep -rn "_LOGGER\|log\.\|print(" --include="*.py" audi_connect/ api.py main.py | grep -iE "token|password|spin|auth"`

Flag anything that interpolates these values into a log message.

## 2. REST API auth

The REST API at `:8000` has **no built-in auth**. Verify:
- No new endpoint was added without an explicit auth check (or comment justifying it)
- The README and CLAUDE.md still note this limitation
- If the user is deploying to K8s, suggest middleware (API key, Basic Auth) or Traefik forward-auth

## 3. CLI password handling

In `main.py cmd_setup`, the password is currently read with `input()` which echoes to terminal. Check whether it's been switched to `getpass.getpass()`. If not, flag it.

## 4. .env file handling

Verify:
- `.env` is in `.gitignore`
- `.env*` token cache files (`*.audi_connect_tokens.json`) are in `.gitignore`
- No code path reads `.env` other than `dotenv.load_dotenv()`
- `TokenStore` still chmod's to `0o600` on Unix

## 5. Webhook signing

Outgoing webhooks (`AUDI_WEBHOOK_URL`) are currently unsigned. If receiver auth was added, verify the HMAC computation. If not, it's a known gap — don't block on it but mention it.

## 6. Action idempotency

Re-check `_action_retry` in `vehicle.py`:
- Does it retry on `ClientResponseError`? That's a problem for non-idempotent actions (unlock, climate-start). Should restrict retry to `RequestTimeoutError`, `ConnectionError`, `OSError` only.

## 7. S-PIN handling

- `_generate_security_pin_hash` should still raise `SpinRequiredError` when spin is None — never silently use empty PIN
- The PIN bytes go through `to_byte_array(self._spin)` — verify the upstream call site doesn't ever pass a non-hex string

## 8. Rate limit defaults

Confirm:
- `MIN_WATCH_INTERVAL = 15 * 60` (15 min) in `api.py` and `main.py`
- `DATA_CACHE_TTL` defaults to 14400 (4h)
- slowapi limits: `30/minute` for reads, `5/minute` for actions

If any of these were lowered, that's a security issue (account lockout risk) — flag it.

## Output

Report findings as:
```
## Security Review

### Blockers
- [item] @ file:line — explanation

### Warnings
- [item] @ file:line — explanation

### Verified clean
- [list of checks that passed]
```

Be specific with file paths and line numbers. No vague "consider reviewing X" — point to the exact code.
