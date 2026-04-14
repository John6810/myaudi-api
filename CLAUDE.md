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
- **`auth.py`** - `AudiAuth`: thin coordinator that manages token state, persistence, and delegates to client/actions
  - Manages 3 tokens: `_bearer_token_json` (IDK/CARIAD), `audi_token` (AZS), `vw_token` (MBB)
  - Automatic token refresh + persistence via `TokenStore`
  - Delegates OAuth flow to `oauth.py`, API calls to `client.py`, actions to `actions.py`
- **`client.py`** - `AudiVehicleClient`: read-only API calls (vehicle status, position, trips, charger, climater, preheater)
- **`actions.py`** - `AudiVehicleActions`: remote actions (lock/unlock, climate control, heater, charge mode)
  - S-PIN hash (SHA-512) for secured actions (lock/unlock)
  - Legacy MBB API uses deciKelvin for temperature: `temp_c * 10 + 2731`
- **`api.py`** - Low-level HTTP client (GET/POST/PUT with myAudi headers, 30s timeout, 3x retry with exponential backoff)
- **`connection.py`** - Shared helpers: `create_session()` (SSL via certifi), `connect_and_get_vehicles()`
- **`watcher.py`** - Shared vehicle state watcher logic (used by both CLI `watch` and API background poller):
  - `diff_states()`: compare two brief state dicts and return changed fields
  - `check_vehicles()`: poll vehicles, compute diffs, fire callbacks (`on_change`, `on_initial`, `on_error`)
- **`vehicle.py`** - `AudiVehicle` class:
  - Properties: mileage, range, battery, doors, windows, climate, trips
  - Actions with input validation: `start_climatisation(16-30°C)`, `start_preheater(10-60 min)`
  - Actions have business-level retry (3 attempts, exponential backoff) on network errors — validation errors are NOT retried
  - `get_brief()`: essentials only (locked, position, range)
  - `get_dashboard()`: full status dict
  - `update()`: parallel fetch via `asyncio.gather()` (status + position + trips)
  - `is_moving`: distinguishes "vehicle moving" from "position fetch failed"
- **`models.py`** - API response parsing with enums:
  - `VehicleDataResponse` (old/new API field mapping), `TripDataResponse`, `VehiclesResponse`
  - `LockState`, `DoorState`, `WindowState` enums (replace magic strings)
  - Safe parsing with `.get()` chains
- **`utils.py`** - Helpers: `get_attr` (deep dict access), `parse_int/float/datetime`, `to_byte_array`
- **`exceptions.py`** - Custom exceptions: `AuthenticationError`, `TokenRefreshError`, `SpinRequiredError`, `CountryNotSupportedError`, `RequestTimeoutError`, etc.
- **`token_store.py`** - OAuth token persistence in `~/.audi_connect_tokens.json` (1h TTL, 0o600 permissions on Linux/Mac)

### Entry points
- **`api.py`** (root) - FastAPI REST API server:
  - Rate limiting via slowapi: 30 req/min (read), 5 req/min (actions) — HTTP 429 on exceed
  - 4h data cache (auto-invalidated after actions)
  - Auto token refresh every 45min
  - `?confirm=true` on action endpoints to wait and verify
  - `GET /brief` for quick status
  - Background watcher with webhook support (optional, uses shared `watcher.py`)
- **`main.py`** - CLI with subcommands: `setup`, `status` (`--brief`), `position` (`--open-maps`), `lock`/`unlock` (`--confirm`), `climate-start`/`stop`, `heater-start`/`stop`, `watch`
  - `-v` flag works both before and after subcommand (shared parent parser)
  - User-friendly error messages (no raw tracebacks)
  - Default VIN support (`AUDI_DEFAULT_VIN`)
  - Interactive setup (`python main.py setup`)
- **`ha_sensor.py`** - Home Assistant script (command_line sensor), outputs JSON to stdout

### Tests
- **`tests/`** - 145 tests (pytest + pytest-asyncio) covering:
  - `test_utils.py` - utility functions
  - `test_models.py` - response parsing + enums
  - `test_exceptions.py` - exception hierarchy
  - `test_token_store.py` - token persistence
  - `test_vehicle.py` - is_moving, parallel update, safe parsing, input validation, brief, dashboard null safety
  - `test_actions.py` - S-PIN hash, climate (CARIAD + legacy), preheater, headers
  - `test_auth.py` - token restore, login flow, refresh, OAuth helpers
  - `test_cli.py` - error formatting, VIN resolution
  - `test_watcher.py` - diff_states, check_vehicles callbacks, VIN filter, error handling
  - `test_integration.py` - integration tests with aioresponses: real HTTP stack with mocked network (vehicle list, position, climate, preheater, retry on timeout/connection error)
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
- `AUDI_WATCH_INTERVAL` - Background poll interval in seconds, 0 = disabled (API server only)
- `AUDI_CACHE_TTL` - Data cache TTL in seconds (default: 14400 = 4h)

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
```
GET  /health              Health check + cache info
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
- Network calls are retried 3 times with exponential backoff (1s, 2s, 4s) on timeout or connection errors
- Vehicle actions (lock, climate, heater) have an additional business-level retry (3 attempts, 2-10s backoff) — only on network errors, not validation errors
- API rate limiting: 30/min for reads, 5/min for actions (HTTP 429 with clear message)
- CLI `-v` flag works in both positions: `main.py -v status` and `main.py status -v`
- Trip data returns 403 on Q4 e-tron (legacy MBB endpoint disabled for EV) — handled gracefully
- Never commit the `.env` file (contains credentials)
