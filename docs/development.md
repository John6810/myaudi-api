# Development

## Local setup

For the basics (clone, install, first test run), see [CONTRIBUTING.md](../CONTRIBUTING.md).

This document focuses on the test layout and patterns specific to this repo.

## Test layout

15 test files under [tests/](../tests/), 186 tests at the time of writing (PR #31). One line of intent per file:

| File | Covers |
|---|---|
| `test_actions.py` | `AudiVehicleActions` — S-PIN hash, climate (CARIAD + legacy MBB), heater payloads, headers. |
| `test_api_auth.py` | FastAPI `Depends(require_api_key)` — 401 / 503 / 200 paths and the `/health` public exception. |
| `test_auth.py` | `AudiAuth` — token restore from cache, fresh login, refresh path, OAuth helpers, `OAuthState` integration. |
| `test_cli.py` | `main.py` helpers — error formatting and VIN resolution. |
| `test_endpoints.py` | `cariad_url` builder + `AudiEndpoints` home-region cache (one upstream call per VIN). |
| `test_exceptions.py` | Exception hierarchy. |
| `test_integration.py` | Real HTTP stack with `aioresponses` mocking the network — exercises `client` + `actions` + `api` end-to-end. |
| `test_logging_utils.py` | `redact()` patterns and `RedactingFilter` mutation behaviour. |
| `test_models.py` | `VehicleDataResponse` / `TripDataResponse` parsing, enums, indexed `get_field` / `get_state`. |
| `test_observability.py` | `/metrics` exposition, `/ready` semantics, `X-Request-ID` middleware. |
| `test_server_auth.py` | `AudiClient.ensure_auth` refresh-first flow with fallback to `login`. |
| `test_token_store.py` | `TokenStore.save(OAuthState)` / `load` round-trip, age expiry, file mode. |
| `test_utils.py` | Helpers in `audi_connect/utils.py`. |
| `test_vehicle.py` | `AudiVehicle` properties, parallel update via `asyncio.gather()`, idempotent-only retry policy (10 dedicated tests). |
| `test_watcher.py` | `diff_states`, `check_vehicles` callbacks, VIN filter. |

## Test patterns

Two styles are in use, picked depending on what's being tested:

### `unittest.mock.AsyncMock` — for unit tests

Used when the goal is to exercise behaviour at one specific layer (e.g. retry logic in `vehicle.py`) without involving the HTTP transport. Pattern:

```python
from unittest.mock import AsyncMock, MagicMock

auth = AsyncMock()
auth.set_vehicle_lock = AsyncMock(side_effect=RequestTimeoutError("timeout"))
v = AudiVehicle(auth, {"vin": "WAUTEST"})

with pytest.raises(RequestTimeoutError):
    await v.unlock()

assert auth.set_vehicle_lock.await_count == 1   # not retried (PR #23)
```

Strengths: fast, deterministic, exercises exactly the layer under test.
Weakness: doesn't catch breakage in the HTTP transport itself.

### `aioresponses` — for integration tests

Used in `tests/test_integration.py` to exercise the real `aiohttp` client + `tenacity` retry + `AudiAPI` request method against a mocked network. Pattern:

```python
from aioresponses import aioresponses

with aioresponses() as m:
    m.post(
        "https://emea.bff.cariad.digital/vehicle/v1/vehicles/WAUTEST/climatisation/start",
        status=204,
    )
    await actions.start_climate_control("WAUTEST", temp_c=22.0)
```

Strengths: catches stack-level breakage, validates real header construction and URL building.
Weakness: harder to debug than `AsyncMock`, slower.

### Rule of thumb

- New code in `audi_connect/api.py`, `audi_connect/oauth.py`, or anything HTTP-shaped → integration test.
- New code in `audi_connect/vehicle.py`, `audi_connect/auth.py`, `audi_connect/client.py`, `audi_connect/actions.py` (above the HTTP layer) → `AsyncMock` unit test.
- FastAPI endpoints → `fastapi.testclient.TestClient` (cf `test_api_auth.py`, `test_observability.py`, `test_server_auth.py`).

**Never hit the real network in CI.** No integration test should reach `*.audi.com`, `*.cariad.digital`, or `*.vwg-connect.com`. The CI workflow does not gate on this — it's a code-review responsibility.

## Adding a new endpoint

For a new Audi-side endpoint (e.g. a charging-mode action or a fresh data field), the path is:

1. **URL building** — add the URL in [audi_connect/endpoints.py](../audi_connect/endpoints.py). For CARIAD, that's a method on `AudiEndpoints` returning `cariad_url_for_vin(...)`. For legacy MBB, you'll need a `home_region` lookup too.
2. **Operation method** — add the call in [audi_connect/client.py](../audi_connect/client.py) for reads, [audi_connect/actions.py](../audi_connect/actions.py) for writes. Both receive an injected `AudiEndpoints`.
3. **Retry policy (writes only)** — apply `@_idempotent_action_retry` from `audi_connect/vehicle.py` ONLY if the action is idempotent on the Audi side. `lock`, `stop_climatisation`, `stop_preheater` qualify; `unlock`, `start_climatisation`, `start_preheater` do not (cf PR #23). When in doubt: don't retry.
4. **Domain property** — if the endpoint produces data the user cares about, surface it on `AudiVehicle` (in [audi_connect/vehicle.py](../audi_connect/vehicle.py)) as a `@property` and add it to `get_brief()` or `get_dashboard()` as appropriate.
5. **REST exposure (optional)** — if the endpoint should be reachable over HTTP, add a route in [server.py](../server.py) with `dependencies=[Depends(require_api_key)]` and a slowapi `@limiter.limit(...)`. For new actions, wrap with `_track_action(...)` so the `audi_action_total` counter records it.
6. **Tests** — minimum two: an `AsyncMock` unit test for the new method (assert URL, payload, headers) and an `aioresponses` integration test that exercises the full `aiohttp` path. If the new code is REST-side, add an `httpx.TestClient` test in `test_observability.py` or a sibling.

## Common pitfalls

- **Don't retry non-idempotent writes.** A 500 from Audi on `unlock` may mean the unlock already happened. Retrying could trigger a second unlock notification, burn an S-PIN security token, or lock the account on the rate budget. `unlock`, `start_climatisation`, `start_preheater` deliberately have no metier-level retry — see PR #23.
- **Don't lower the rate-limit defaults without a flag.** `AUDI_WATCH_INTERVAL` is clamped to a 900 s minimum; `AUDI_CACHE_TTL` defaults to 4h. Going below either invites account lockout.
- **Don't log secrets.** The `RedactingFilter` catches the obvious patterns (bearer tokens, OAuth JSON keys, X-QMAuth, emails) but a new pattern needs an explicit entry. New password-or-token-shaped fields → update the filter at the same time.
- **Don't refactor `oauth.py` without a real-account validation step.** Most of the file is dictated by upstream HTML/HTTP shape. Unit tests cover the helpers but cannot certify the end-to-end flow against Audi. A "small refactor" can break authentication for everyone with no in-CI signal.
- **Don't break the single-replica invariant.** `server.py` and `audi_connect/token_store.py` both carry header banners listing the in-process state that breaks at `replicas > 1`. If you change the cache, the rate limiter, the watcher, or the token store, re-read those banners and either preserve the invariant or redesign the persistence layer first.
- **Don't add deps casually.** `requirements.txt` is intentionally minimal. New deps must be justified and prefer a well-maintained package with a stable API.
