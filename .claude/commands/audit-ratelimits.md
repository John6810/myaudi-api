---
description: Audit all rate-limit-related constants and confirm they're conservative enough for Audi's ~6 req/h budget
allowed-tools: Read, Grep, Glob
---

Audit every rate-limit-related constant in the codebase. Audi's API enforces approximately 6 requests per hour per account — exceeding it locks both this client AND the user's official myAudi app until the window resets.

## What to check

### Cache & polling intervals
Find these values in `api.py` (root) and `main.py`:
- `DATA_CACHE_TTL` — should default to ≥ 14400 (4h)
- `MIN_WATCH_INTERVAL` — should be ≥ 900 (15 min)
- `TOKEN_REFRESH_INTERVAL` — should be ≥ 2700 (45 min)
- `_raw_watch_interval` clamping logic — must still warn and clamp values below the minimum

### slowapi limits
In `api.py` (root), check the `@limiter.limit(...)` decorators:
- Read endpoints (`/health`, `/vehicles`, `/status`, `/brief`, `/position`) → should be `30/minute` or stricter
- Action endpoints (`/{vin}/lock`, `/unlock`, `/climate/*`, `/heater/*`) → should be `5/minute` or stricter

### Validation boundaries
In `audi_connect/vehicle.py`:
- `MIN_CLIMATE_TEMP_C = 16.0`, `MAX_CLIMATE_TEMP_C = 30.0`
- `MIN_HEATER_DURATION_MIN = 10`, `MAX_HEATER_DURATION_MIN = 60`

### Network retry
In `audi_connect/api.py`:
- `MAX_RETRIES = 3`
- `wait_exponential(multiplier=1, min=1, max=10)`
- Retries only on `RequestTimeoutError`, `ConnectionError`, `OSError` — NOT on `ClientResponseError`

In `audi_connect/vehicle.py`:
- `_action_retry` — `stop_after_attempt(3)`, `wait_exponential(multiplier=1, min=2, max=10)`

## Output format

```
## Rate Limit Audit

| Constant                   | File          | Value      | Status |
|----------------------------|---------------|------------|--------|
| DATA_CACHE_TTL             | api.py        | 14400      | ✅     |
| MIN_WATCH_INTERVAL         | api.py        | 900        | ✅     |
| MIN_WATCH_INTERVAL         | main.py       | 900        | ✅     |
| /status rate limit         | api.py        | 30/minute  | ✅     |
| /lock rate limit           | api.py        | 5/minute   | ✅     |
| _action_retry attempts     | vehicle.py    | 3          | ✅     |
| MAX_RETRIES (HTTP)         | audi_connect/api.py | 3    | ✅     |
| ...                        | ...           | ...        | ...    |

### Findings
- [list anything below threshold or missing]

### Net request budget per hour
Estimated worst case: <calculation>
- Background watcher: 60 / WATCH_INTERVAL_MIN × ~3 calls/poll
- User-triggered status: bounded by 30/min × 60 = ?
- Total: <conclusion>

### Verdict
WITHIN BUDGET / RISKY / OVER BUDGET
```

If anything has been lowered below the conservative defaults, treat it as a blocker.
