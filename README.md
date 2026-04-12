# myaudi-api

Python client and REST API for the **Audi Connect** (myAudi) service. Check your vehicle status and execute remote actions via CLI or HTTP API.

Based on reverse-engineering of the Android myAudi app v4.31.0 and the [audiconnect/audi_connect_ha](https://github.com/audiconnect/audi_connect_ha/) project.

## Features

- Full vehicle status (mileage, range, fuel level, oil level, AdBlue)
- Quick status mode — locked, position, range in one glance
- GPS position with Google Maps link (auto-open in browser)
- Door, trunk, hood and window states
- Charging data (EV/PHEV): SoC, power, remaining time, plug state
- Remote lock / unlock (requires S-PIN) with confirmation
- Remote climate control (start/stop, 16-30°C)
- Auxiliary heater (start/stop, 10-60 min)
- Trip data (short-term and long-term)
- **REST API** (FastAPI) with Docker deployment and rate limiting
- **Watch mode** — monitor changes and send webhook notifications
- **Action retry** — automatic retry on network failures (lock, climate, heater)
- Home Assistant integration via command_line sensor
- OAuth token caching to avoid re-authenticating on every run
- Interactive setup (`python main.py setup`)

## Quick Start

```bash
git clone https://github.com/John6810/myaudi-api.git
cd myaudi-api
pip install -r requirements.txt
python main.py setup          # Interactive — creates .env file
python main.py status --brief # Quick check
```

## CLI Usage

### Setup

Run the interactive setup to create your `.env` file:

```bash
python main.py setup
```

Or create `.env` manually:

```env
AUDI_USERNAME=your.email@example.com
AUDI_PASSWORD=your_password
AUDI_COUNTRY=DE
AUDI_SPIN=1234
AUDI_API_LEVEL=1
AUDI_DEFAULT_VIN=WAUXXXXXXXXXXXXX
AUDI_WEBHOOK_URL=https://n8n.example.com/webhook/audi
```

### Configuration

| Variable | Description | Default |
|---|---|---|
| `AUDI_USERNAME` | myAudi account email | (required) |
| `AUDI_PASSWORD` | Password | (required) |
| `AUDI_COUNTRY` | Country code (DE, FR, US, etc.) | `DE` |
| `AUDI_SPIN` | S-PIN for lock/unlock | (optional) |
| `AUDI_API_LEVEL` | `0` = legacy MBB, `1` = new CARIAD API | `1` |
| `AUDI_DEFAULT_VIN` | Default VIN — skip `--vin` for single-vehicle users | (optional) |
| `AUDI_WEBHOOK_URL` | Webhook URL for state change notifications | (optional) |
| `AUDI_WATCH_INTERVAL` | Background poll interval in seconds (API server only) | `0` (disabled) |
| `AUDI_CACHE_TTL` | Data cache TTL in seconds | `14400` (4h) |

### Commands

```bash
# Quick status (locked, position, range)
python main.py status --brief

# Full vehicle status (with debug logs)
python main.py status -v

# GPS position (opens in browser)
python main.py position --open-maps

# Lock with confirmation (waits 5s and checks)
python main.py lock --confirm

# Unlock
python main.py unlock --confirm

# Climate control
python main.py climate-start --temp 22
python main.py climate-stop

# Auxiliary heater
python main.py heater-start --duration 30
python main.py heater-stop

# Watch mode — monitor changes, send webhooks
python main.py watch --interval 300

# Target a specific VIN (if multiple vehicles)
python main.py status --vin WAUXXXXXXXXXXXXX
```

## REST API

### Run locally

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

### Run with Docker

```bash
docker build -t audi-connect .
docker run -p 8000:8000 \
  -e AUDI_USERNAME="your.email@example.com" \
  -e AUDI_PASSWORD="your_password" \
  -e AUDI_COUNTRY="DE" \
  -e AUDI_SPIN="1234" \
  -e AUDI_API_LEVEL="1" \
  audi-connect
```

### Kubernetes

```bash
kubectl create secret generic audi-credentials \
  -n myaudi-api \
  --from-literal=AUDI_USERNAME="your.email@example.com" \
  --from-literal=AUDI_PASSWORD="your_password" \
  --from-literal=AUDI_COUNTRY="DE" \
  --from-literal=AUDI_SPIN="1234" \
  --from-literal=AUDI_API_LEVEL="1"
```

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check + cache info |
| `GET` | `/vehicles` | List vehicles |
| `GET` | `/status` | Full vehicle status (optional `?vin=`) |
| `GET` | `/brief` | Quick status: locked, position, range |
| `GET` | `/position` | GPS position (optional `?vin=`) |
| `POST` | `/{vin}/lock` | Lock vehicle (`?confirm=true` to verify) |
| `POST` | `/{vin}/unlock` | Unlock vehicle (`?confirm=true` to verify) |
| `POST` | `/{vin}/climate/start` | Start climate (`?temp=21&confirm=true`) |
| `POST` | `/{vin}/climate/stop` | Stop climate |
| `POST` | `/{vin}/heater/start` | Start heater (`?duration=30&confirm=true`) |
| `POST` | `/{vin}/heater/stop` | Stop heater |

Action endpoints accept `?confirm=true` to wait 5 seconds after the action, re-fetch vehicle data, and return the updated status. Without it, the response is immediate (`"status": "sent"`).

Cache is automatically invalidated after any action so the next `GET /status` reflects the change.

**Rate limiting**: read endpoints allow 30 requests/min, action endpoints allow 5 requests/min. Exceeding returns HTTP 429.

### Example responses

```bash
curl http://localhost:8000/brief
```
```json
{
  "vehicles": [{
    "vehicle": "My Audi",
    "locked": "Locked",
    "position": "50.123, 4.456",
    "maps": "https://www.google.com/maps?q=50.123,4.456",
    "range": "200 km",
    "battery": "51%"
  }]
}
```

```bash
curl http://localhost:8000/status
```
```json
{
  "count": 1,
  "vehicles": [{
    "vin": "WAUXXXXX",
    "mileage": "4,391 km",
    "range": "200 km",
    "battery_soc": "51%",
    "charging": "notReadyForCharging",
    "doors_trunk": "Locked",
    "windows": "Closed",
    "climatisation": "off"
  }]
}
```

```bash
curl -X POST "http://localhost:8000/WAUXXXXX/lock?confirm=true"
```
```json
{
  "status": "confirmed",
  "action": "lock",
  "vin": "WAUXXXXX",
  "vehicle_status": {"doors_trunk": "Locked", "windows": "Closed", "range": "200 km"}
}
```

### Webhooks

Set `AUDI_WEBHOOK_URL` and `AUDI_WATCH_INTERVAL` to enable background monitoring. The API server will poll vehicle data at the configured interval and POST state changes:

```json
{
  "event": "state_change",
  "vin": "WAUXXXXX",
  "title": "My Audi",
  "changes": {
    "locked": {"old": "Locked", "new": "Closed"}
  },
  "state": {"vehicle": "My Audi", "locked": "Closed", "position": "50.123, 4.456", "range": "200 km"},
  "timestamp": "2026-04-12T22:30:00+02:00"
}
```

Works with n8n, Home Assistant webhooks, Discord webhooks, or any HTTP endpoint.

## Home Assistant Integration

The `ha_sensor.py` script outputs vehicle data as JSON to stdout, compatible with Home Assistant's `command_line` sensor:

```yaml
command_line:
  - sensor:
      name: "Audi Connect"
      command: "python3 /config/scripts/myaudi-api/ha_sensor.py"
      value_template: "{{ value_json.range }}"
      json_attributes:
        - vin
        - model
        - mileage
        - range
        - battery_soc
        - charging_state
        - plug_state
        - latitude
        - longitude
        - doors_locked
        - windows_closed
        - climatisation
      scan_interval: 300
```

## Architecture

```
myaudi-api/
  api.py                   # FastAPI REST API (cache, webhooks, confirm)
  main.py                  # CLI (setup, status, watch, actions)
  ha_sensor.py             # Home Assistant script
  Dockerfile
  requirements.txt
  audi_connect/
    oauth.py               # 13-step OAuth2/OIDC login flow
    auth.py                # Token coordinator (delegates to oauth.py)
    client.py              # Read-only API calls (status, position, trips)
    actions.py             # Remote actions (lock, climate, heater)
    api.py                 # Low-level HTTP client (retry, timeout)
    connection.py          # Shared connection helpers
    vehicle.py             # AudiVehicle (properties, validation, brief/dashboard, action retry)
    watcher.py             # Shared watch logic (diff_states, check_vehicles callbacks)
    models.py              # Response parsing + LockState/DoorState/WindowState enums
    utils.py               # Utility functions
    exceptions.py          # Custom exceptions
    token_store.py         # Token persistence (restricted file permissions)
  tests/                   # 145 tests (pytest + pytest-asyncio + aioresponses)
    test_vehicle.py        # is_moving, parallel fetch, validation, brief
    test_actions.py        # S-PIN hash, climate (CARIAD + legacy), heater
    test_auth.py           # Token restore, login, refresh, OAuth helpers
    test_integration.py    # Integration tests (real HTTP stack, mocked network)
    test_watcher.py        # State diff, vehicle polling callbacks
    test_models.py         # Response parsing, enums
    test_cli.py            # Error formatting, VIN resolution
    test_utils.py          # Utility functions
    test_token_store.py    # Token persistence
    test_exceptions.py     # Exception hierarchy
  .github/workflows/
    build.yml              # CI/CD: Docker build + GHCR push + GitOps update
```

## Tests

```bash
python -m pytest tests/ -v
```

145 tests covering: authentication flow, OAuth helpers, token management, vehicle data parsing, action validation, parallel fetching, error formatting, enums, state watcher, and integration tests with mocked HTTP.

## How It Works

Authentication reproduces the OAuth2/OIDC flow of the Android myAudi app in 13 steps:

1. Fetch Audi market configuration
2. OpenID Connect discovery
3. PKCE challenge generation (S256)
4. Email + password submission via HTML forms
5. Exchange authorization code for an IDK bearer token
6. Obtain the AZS (Audi) token
7. Register MBB OAuth client (VW Group)
8. Obtain and refresh the MBB token

Three tokens are managed in parallel:
- **IDK/CARIAD**: for the new vehicle API
- **AZS**: for the Audi GraphQL API (vehicle list)
- **MBB/VW**: for the legacy API (trips, lock/unlock)

Tokens are cached locally (`~/.audi_connect_tokens.json`, 1h TTL, restricted file permissions on Unix) to skip the full login flow on subsequent runs. The API server refreshes tokens automatically every 45 minutes.

## Dependencies

- `aiohttp` - Async HTTP client
- `beautifulsoup4` - HTML parsing (authentication flow)
- `python-dotenv` - `.env` file loading
- `certifi` - CA bundle for SSL validation
- `tenacity` - Retry with exponential backoff
- `fastapi` - REST API framework
- `uvicorn` - ASGI server
- `slowapi` - Rate limiting
- `pytest` + `pytest-asyncio` + `aioresponses` - Testing

## License

MIT License. See [LICENSE](LICENSE) for details.

Usage of the Audi Connect API is subject to Audi AG's terms of service.
