# myaudi-api

## Overview
Standalone Python client for the Audi Connect (myAudi) API. Connects to Audi/VW Group services to check vehicle status and execute remote actions. Based on [audiconnect/audi_connect_ha](https://github.com/audiconnect/audi_connect_ha/).

## Tech Stack
- **Python 3** with **asyncio** / **aiohttp** for async HTTP calls
- **beautifulsoup4** for HTML parsing in the authentication flow
- **python-dotenv** for `.env` configuration
- **certifi** for SSL validation with an up-to-date CA bundle
- **tenacity** for retry with exponential backoff on network calls
- **FastAPI** + **uvicorn** for the REST API server
- **pytest** + **pytest-asyncio** for testing
- No database

## Architecture

### Core modules (`audi_connect/`)
- **`oauth.py`** - `AudiOAuth`: the 13-step OAuth2/OIDC login flow (reverse-engineered from Android myAudi app v4.31.0):
  1. Audi market config + OpenID Discovery
  2. PKCE challenge + email/password login via HTML forms
  3. IDK (bearer) token via code exchange
  4. AZS (Audi) token via id_token grant
  5. MBB OAuth client registration + MBB (VW Group) token
  - Also handles token refresh (MBB, IDK, AZS)
  - X-QMAuth header via HMAC-SHA256 with a secret from the APK and a 100s-window timestamp
- **`oauth_state.py`** - `OAuthState`: frozen dataclass holding all 10 OAuth tokens / endpoint URLs after login
  - `from_dict(d)` builds from oauth login result or TokenStore.load
  - `to_dict()` for serialization
  - `with_refresh(refreshed)` returns a new state merging an oauth.refresh_tokens result (immutable update)
- **`auth.py`** - `AudiAuth`: thin coordinator that manages token state, persistence, and delegates to client/actions
  - Holds a single `_state: Optional[OAuthState]` instead of 10 individual attrs
  - Backwards-compat property proxies (`vw_token`, `audi_token`, `mbb_oauth_token`, `xclient_id`) for now — to be removed when no external reader needs them
  - Automatic token refresh + persistence via `TokenStore`
  - `login()` returns the validated vehicle list directly (no more consume-once cache hack)
  - Delegates OAuth flow to `oauth.py`, API calls to `client.py`, actions to `actions.py`
- **`endpoints.py`** - URL building + home-region cache shared by client/actions/oauth
  - `cariad_url(country, path, **kw)`: free function for stateless CARIAD URL building (used during OAuth before any state exists)
  - `AudiEndpoints` class: per-VIN home-region resolution (one upstream call per VIN, cached); `set_vw_token()` after login/refresh; `cariad_url_for_vin()`, `home_region()`, `home_region_setter()`
- **`client.py`** - `AudiVehicleClient`: read-only API calls (vehicle status, position, trips). Receives an `AudiEndpoints` instance instead of building URLs itself.
- **`actions.py`** - `AudiVehicleActions`: remote actions (lock/unlock, climate control, heater)
  - Receives an `AudiEndpoints` instance (no longer reaches into `client._get_*` privates)
  - S-PIN hash (SHA-512) for secured actions (lock/unlock)
  - Legacy MBB API uses deciKelvin for temperature: `temp_c * 10 + 2731`
- **`api.py`** - Low-level HTTP client (GET/POST with myAudi headers, 30s timeout, 3x retry with exponential backoff on transport errors)
- **`connection.py`** - Shared helpers: `create_session()` (SSL via certifi), `connect_and_get_vehicles()`
- **`watcher.py`** - Shared vehicle state watcher logic (used by both CLI `watch` and API background poller):
  - `diff_states()`: compare two brief state dicts and return changed fields
  - `check_vehicles()`: poll vehicles, compute diffs, fire callbacks (`on_change`, `on_initial`, `on_error`)
- **`vehicle.py`** - `AudiVehicle` class:
  - Properties: mileage, range, battery, doors, windows, climate, trips (`_get_field`/`_get_state` are O(1) dict lookups via the indexed `VehicleDataResponse`)
  - Actions with input validation: `start_climatisation(16-30°C)`, `start_preheater(10-60 min)`
  - **Idempotent-only retry policy**: `_idempotent_action_retry` (3 attempts, 2-10s exp backoff) applied ONLY to `lock`, `stop_climatisation`, `stop_preheater` (end-state same on duplicate). `unlock`, `start_climatisation`, `start_preheater` are NOT retried — duplicates can re-trigger notifications, extend the heater timer, or burn S-PIN tokens against the ~6 req/h Audi budget. Validation errors are never retried.
  - `get_brief()`: essentials only (locked, position, range)
  - `get_dashboard()`: full status dict
  - `update()`: parallel fetch via `asyncio.gather()` (status + position + trips)
  - `is_moving`: distinguishes "vehicle moving" from "position fetch failed"
- **`models.py`** - API response parsing with enums:
  - `VehicleDataResponse` (old/new API field mapping), `TripDataResponse`
  - `LockState`, `DoorState`, `WindowState` enums (replace magic strings)
  - O(1) lookup via `get_field(name)` / `get_state(name)` — `data_fields` and `states` are indexed by name in `__init__` after parsing
  - Safe parsing with `.get()` chains
- **`utils.py`** - Helpers: `get_attr` (deep dict access), `parse_int/float/datetime`, `to_byte_array`
- **`exceptions.py`** - Custom exceptions: `AuthenticationError`, `TokenRefreshError`, `SpinRequiredError`, `CountryNotSupportedError`, `RequestTimeoutError`, etc.
- **`logging_utils.py`** - `redact()` helper + `RedactingFilter` (logging filter, installed at startup in `server.py` and `main.py`)
  - Masks bearer tokens, JSON OAuth values (`access_token`, `refresh_token`, `id_token`, `securityToken`, `securityPinHash`, `password`, `spin`, `client_secret`, `code_verifier`), `X-QMAuth` HMAC values, and emails (`xxx***@domain`)
  - Belt-and-suspenders: `aiohttp.*` loggers pinned to WARNING regardless of root level
- **`token_store.py`** - OAuth token persistence in `~/.audi_connect_tokens.json` (1h TTL, 0o600 permissions on Linux/Mac)

### Entry points
- **`server.py`** (root, formerly `api.py`) - FastAPI REST API server:
  - All endpoints except `/health`, `/ready`, `/metrics` require `X-API-Key` header (matches `AUDI_API_KEY` env var via `Depends(require_api_key)`); fails closed with 503 if the key is unset on the server
  - Rate limiting via slowapi: 30 req/min (read), 5 req/min (actions) — HTTP 429 on exceed
  - 4h data cache (auto-invalidated after actions); concurrent `?confirm=true` calls serialized through `_update_lock`
  - Auto token refresh every 45min — incremental refresh (3 upstream calls) is tried first, with fallback to full login (~10 calls) only if refresh fails or no auth context exists yet
  - `?confirm=true` on action endpoints to wait and verify
  - `GET /brief` for quick status
  - `GET /ready` returns 503 until authenticated to Audi Connect; `GET /health` always 200 if process alive
  - `GET /metrics` exposes Prometheus text format (FastAPI HTTP metrics + 4 business metrics)
  - `X-Request-ID` middleware: propagates request id via contextvar to log records as `[rid=...]`
  - Background watcher with webhook support (optional, uses shared `watcher.py`); webhooks optionally signed via HMAC-SHA256 (`X-Audi-Signature: sha256=<hex>`) when `AUDI_WEBHOOK_SECRET` is set
  - Single-replica invariant documented in the file header: in-process cache + slowapi limiter + watcher + token store all assume `replicas: 1`
- **`main.py`** - CLI with subcommands: `setup`, `status` (`--brief`), `position` (`--open-maps`), `lock`/`unlock` (`--confirm`), `climate-start`/`stop`, `heater-start`/`stop`, `watch`
  - `-v` flag works both before and after subcommand (shared parent parser)
  - User-friendly error messages (no raw tracebacks)
  - Default VIN support (`AUDI_DEFAULT_VIN`)
  - Interactive setup (`python main.py setup`)
- **`ha_sensor.py`** - Home Assistant script (command_line sensor), outputs JSON to stdout

### Tests
- **`tests/`** - 183 tests (pytest + pytest-asyncio + aioresponses + httpx for FastAPI TestClient) covering:
  - `test_utils.py` - utility functions
  - `test_models.py` - response parsing + enums + indexed `get_field`/`get_state`
  - `test_exceptions.py` - exception hierarchy
  - `test_token_store.py` - `OAuthState` round-trip persistence
  - `test_vehicle.py` - is_moving, parallel update, safe parsing, input validation, brief, dashboard null safety, **idempotent-only retry policy** (10 tests)
  - `test_actions.py` - S-PIN hash, climate (CARIAD + legacy), preheater, headers
  - `test_auth.py` - token restore, login (returns vehicle list), refresh, OAuth helpers, `OAuthState` integration
  - `test_cli.py` - error formatting, VIN resolution
  - `test_watcher.py` - diff_states, check_vehicles callbacks, VIN filter, error handling
  - `test_integration.py` - integration tests with aioresponses: real HTTP stack with mocked network (vehicle list, position, climate, preheater, retry on timeout/connection error)
  - `test_endpoints.py` - `cariad_url` URL building + `AudiEndpoints` home-region cache
  - `test_logging_utils.py` - `redact` patterns + `RedactingFilter` filter behavior
  - `test_observability.py` - `/metrics`, `/ready`, `X-Request-ID` middleware
  - `test_api_auth.py` - `X-API-Key` dependency on protected endpoints (401 / 503 / 200 / public `/health`)
- Run with: `python -m pytest tests/ -v`

## External APIs

### CARIAD API (new, api_level=1)
- Base URL: `https://emea.bff.cariad.digital` (EU) or `https://na.bff.cariad.digital` (US)
- Endpoints: `/vehicle/v1/vehicles/{vin}/selectivestatus`, `/parkingposition`, `/climatisation/start|stop`, `/auxiliaryheating/start|stop`, `/charging/mode`
- Auth: IDK bearer token

### MBB/VW Group API (legacy, api_level=0)
- Dynamic base URL via homeRegion (`mal-*.prd.*.vwg-connect.com`)
- Endpoints: `/fs-car/bs/climatisation/v1/...`, `/fs-car/bs/rlu/v1/...` (lock/unlock), `/api/bs/tripstatistics/v1/...`
- Auth: MBB (VW) bearer token

### Other endpoints
- Market config: `https://content.app.my.audi.com/service/mobileapp/configurations/...`
- GraphQL vehicles: `https://app-api.live-my.audi.com/vgql/v1/graphql` (EU) / `https://app-api.my.aoa.audi.com/vgql/v1/graphql` (US)
- Identity: `https://identity.vwgroup.io/oidc/v1/authorize`
- MBB OAuth: `https://mbboauth-1d.prd.ece.vwg-connect.com/mbbcoauth`

## Configuration
Environment variables (`.env` file — run `python main.py setup` to create interactively):
- `AUDI_USERNAME` - myAudi account email
- `AUDI_PASSWORD` - Password
- `AUDI_COUNTRY` - Country code (DE, FR, US, etc.) - default: DE
- `AUDI_SPIN` - S-PIN for secured actions (lock/unlock)
- `AUDI_API_LEVEL` - 0 = legacy MBB, 1 = new CARIAD API (default: 1)
- `AUDI_DEFAULT_VIN` - Default VIN, skip `--vin` for single-vehicle users (optional)
- `AUDI_WEBHOOK_URL` - Webhook URL for state change notifications (optional)
- `AUDI_WEBHOOK_SECRET` - HMAC-SHA256 secret to sign outgoing webhooks (`X-Audi-Signature: sha256=<hex>` header). Unsigned if unset (optional)
- `AUDI_WATCH_INTERVAL` - Background poll interval in seconds, 0 = disabled (API server only)
- `AUDI_CACHE_TTL` - Data cache TTL in seconds (default: 14400 = 4h)
- `AUDI_API_KEY` - Required `X-API-Key` header on all REST endpoints except `/health`, `/ready`, `/metrics`. Server fails closed (503) if endpoints are called while this is unset (strongly recommended)

## Useful commands
```bash
# Interactive setup (creates .env)
python main.py setup

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -v

# Quick status (locked, position, range)
python main.py status --brief

# Full vehicle status
python main.py status

# GPS position (opens browser)
python main.py position --open-maps

# Lock with confirmation
python main.py lock --confirm

# Climate control
python main.py climate-start --temp 22
python main.py climate-stop

# Auxiliary heater
python main.py heater-start --duration 30
python main.py heater-stop

# Watch mode (poll + webhooks)
python main.py watch --interval 900
```

## REST API endpoints
All endpoints below except `/health`, `/ready`, `/metrics` require the `X-API-Key` header (matched against `AUDI_API_KEY`). Without the header → 401. With server `AUDI_API_KEY` unset → 503.
```
GET  /health              Liveness probe + cache info (public, 200 even when degraded)
GET  /ready               Readiness probe (public, 503 until authenticated to Audi Connect)
GET  /metrics             Prometheus text format (public, scrape target)
GET  /vehicles            List vehicles
GET  /status              Full vehicle status
GET  /brief               Quick status (locked, position, range)
GET  /position            Vehicle GPS position
POST /{vin}/lock          Lock vehicle (?confirm=true for verification)
POST /{vin}/unlock        Unlock vehicle (?confirm=true for verification)
POST /{vin}/climate/start Start climate (?temp=21&confirm=true)
POST /{vin}/climate/stop  Stop climate
POST /{vin}/heater/start  Start heater (?duration=30&confirm=true)
POST /{vin}/heater/stop   Stop heater
```

## Important notes
- Authentication is complex (13 steps with HTML parsing, PKCE, multiple token exchanges) — reverse-engineered from the Android myAudi app v4.31.0
- The OAuth flow lives in `oauth.py`, the coordinator in `auth.py` (separated for testability)
- Two API levels coexist: legacy (MBB/VW) and new (CARIAD) — the code supports both
- Tokens are cached in `~/.audi_connect_tokens.json` (1h TTL, restricted permissions on Unix)
- Vehicle data fetches run in parallel via `asyncio.gather()` for better performance
- Cache is auto-invalidated after actions (lock, climate, etc.) so next status reflects changes
- Action endpoints support `?confirm=true` to wait 5s and verify the action was applied
- Door/window states use `LockState`, `DoorState`, `WindowState` enums (not magic strings)
- Input validation in core classes: temperature 16-30°C, heater duration 10-60 min
- Network calls are retried 3 times with exponential backoff (1s, 2s, 4s) on timeout or connection errors (transport layer, in `audi_connect/api.py`)
- Vehicle actions: business-level retry is **applied only to idempotent actions** (`lock`, `stop_climatisation`, `stop_preheater`) via `_idempotent_action_retry` (3 attempts, 2-10s backoff). `unlock`, `start_climatisation`, `start_preheater` are NOT retried at the metier layer to avoid double-fire on the ~6 req/h Audi budget. Validation errors (out-of-range temp/duration) are never retried.
- REST API protected by `X-API-Key` header on all endpoints except `/health`, `/ready`, `/metrics`. Wired via FastAPI `Depends(require_api_key)` in `server.py`. Fails closed (503) if `AUDI_API_KEY` is unset on the server.
- Logs sanitized via `RedactingFilter` (in `audi_connect/logging_utils.py`) — masks bearer tokens, OAuth JSON keys (access/refresh/id_token, password, spin, securityToken, securityPinHash, client_secret, code_verifier), `X-QMAuth` HMAC values, and emails (`xxx***@domain`). Installed once at startup in `server.py` and `main.py`.
- `X-Request-ID` middleware on every request (uses client-provided value if present, else generates 12-hex-char uuid). Propagated to log records via contextvars and rendered as `[rid=...]` in log lines for end-to-end Loki/Grafana correlation.
- Prometheus metrics exposed at `/metrics` via `prometheus-fastapi-instrumentator`: standard FastAPI HTTP metrics + 4 custom business metrics: `audi_auth_refresh_total{result}`, `audi_cache_operation_total{operation}`, `audi_action_total{action,result}`, `audi_backend_request_duration_seconds{endpoint}`.
- Webhook payloads optionally signed with HMAC-SHA256 (`X-Audi-Signature: sha256=<hex>` header over the raw request body) when `AUDI_WEBHOOK_SECRET` is set. Backwards compatible: unsigned when unset.
- Single-replica invariant: `server.py` and `audi_connect/token_store.py` carry header banners listing every piece of in-process state that breaks under `replicas > 1` (cache, slowapi limiter, watcher, token store). Do NOT scale beyond 1 without redesigning persistence and rate-limit storage.
- API rate limiting: 30/min for reads, 5/min for actions (HTTP 429 with clear message); `/health`, `/ready`, `/metrics` are unlimited and unauthenticated.
- CLI `-v` flag works in both positions: `main.py -v status` and `main.py status -v`
- CLI `setup` uses `getpass.getpass()` for password and S-PIN to avoid echo + shell history exposure
- Trip data returns 403 on Q4 e-tron (legacy MBB endpoint disabled for EV) — handled gracefully
- Never commit the `.env` file (contains credentials)
