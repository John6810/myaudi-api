# API reference

This document lists every public surface: REST endpoints exposed by `server.py`, CLI subcommands in `main.py`, the Home Assistant `command_line` sensor in `ha_sensor.py`, and the webhook payload shape.

## REST API

The FastAPI server is started with `uvicorn server:app --host 0.0.0.0 --port 8000`. Endpoints below derive from the `@app.get(...)` / `@app.post(...)` decorators in [server.py](../server.py).

**Authentication**: every endpoint except `/health`, `/ready`, `/metrics` requires the `X-API-Key` header to match `AUDI_API_KEY`. See the [Authentication](#authentication-for-rest-api) section below.

**Rate limiting**: per-IP via slowapi. `429 Too Many Requests` past the limit. `/health`, `/ready`, `/metrics` are not rate-limited.

### `GET /health`

- Auth: public.
- Rate limit: 60/min.
- Returns: `{"status": "ok" | "degraded", "authenticated": bool, "vehicles": [{"vin", "model", "title"}], "cache_ttl": int, "cache_age": int|null, "timestamp": iso8601}`.
- `status` is `"ok"` when authenticated to Audi, `"degraded"` otherwise. The endpoint always returns 200 — kubelet liveness must not kill the pod just because Audi is down.

```bash
curl http://localhost:8000/health
```

### `GET /ready`

- Auth: public.
- Rate limit: none.
- Returns 200 with `{"status": "ready"}` when authenticated to Audi Connect, else **503** with `{"detail": "Not authenticated to Audi Connect"}`. Use this for kubelet readiness.

### `GET /metrics`

- Auth: public.
- Rate limit: none.
- Returns: Prometheus text format. See [observability.md](observability.md) for the metric catalogue.

### `GET /vehicles`

- Auth: `X-API-Key`.
- Rate limit: 30/min.
- Returns: `{"count": int, "vehicles": [{"vin", "model", "title", "model_year"}]}`.

```bash
curl -H "X-API-Key: $AUDI_API_KEY" http://localhost:8000/vehicles
```

### `GET /status`

- Auth: `X-API-Key`.
- Rate limit: 30/min.
- Query: `?vin=<VIN>` (optional, filters to one vehicle).
- Returns: `{"count": int, "vehicles": [{"vin", "model", "title", **dashboard}]}` — the dashboard dict is `AudiVehicle.get_dashboard()`.

### `GET /brief`

- Auth: `X-API-Key`.
- Rate limit: 30/min.
- Query: `?vin=<VIN>` (optional).
- Returns: `{"vehicles": [brief]}` — `brief` is `AudiVehicle.get_brief()` (locked, position, range, battery/fuel).

### `GET /position`

- Auth: `X-API-Key`.
- Rate limit: 30/min.
- Query: `?vin=<VIN>` (optional).
- Returns: `{"vehicles": [{"vin", "title", "is_moving", "latitude"?, "longitude"?, "google_maps"?}]}`. Coordinates and the maps link are present only when a parking position is known.

### `POST /{vin}/lock`

- Auth: `X-API-Key` + `AUDI_SPIN` env var must be set on the server.
- Rate limit: 5/min.
- Query: `?confirm=true` to wait 5s, force-update, and verify `doors_trunk == "Locked"` in the response.
- Returns: `{"status": "sent" | "confirmed" | "pending" | "sent_unconfirmed", "action": "lock", "vin": ..., "vehicle_status"?: dashboard}`.

### `POST /{vin}/unlock`

- Same shape as `/{vin}/lock` with `action: "unlock"` and the confirm check looking for `doors_trunk == "Closed"`.
- Note: this action is **not retried** at the metier layer (idempotent-only retry policy from PR #23). A failed unlock surfaces immediately as 500.

### `POST /{vin}/climate/start`

- Auth: `X-API-Key`.
- Rate limit: 5/min.
- Query: `?temp=<float>` (default 21.0, range 16–30) — temperature in Celsius. `?confirm=true` to verify after 5s.
- Returns: `{"status", "action": "climate_start", "temperature": float, "vin"}`.
- Not retried (cycle-restart side effect).

### `POST /{vin}/climate/stop`

- Auth: `X-API-Key`. Rate limit: 5/min. `?confirm=true` supported.
- Idempotent: retried up to 3× on transient errors.

### `POST /{vin}/heater/start`

- Auth: `X-API-Key`. Rate limit: 5/min.
- Query: `?duration=<int>` (default 30, range 10–60 min). `?confirm=true` supported.
- Not retried (heater timer would extend on duplicate).

### `POST /{vin}/heater/stop`

- Auth: `X-API-Key`. Rate limit: 5/min. `?confirm=true` supported.
- Idempotent: retried up to 3× on transient errors.

## CLI commands

Entry point: `python main.py <command> [flags]`. Global flags valid both before and after the subcommand: `-u/--username`, `-p/--password`, `-c/--country`, `--spin`, `--api-level`, `--vin`, `-v/--verbose`. Default values are read from environment variables (`AUDI_USERNAME`, `AUDI_PASSWORD`, etc.) — the `.env` file is loaded automatically via `python-dotenv`.

| Command | Args | Description |
|---|---|---|
| `setup` | — | Interactive setup; writes `.env`. Password and S-PIN read via `getpass` (no terminal echo). |
| `status` | `--brief` | Show full vehicle status, or only `locked`/`position`/`range`/`battery` with `--brief`. |
| `position` | `--open-maps` | Print GPS position; with `--open-maps`, open Google Maps in the default browser. |
| `lock` | `--confirm` | Lock the vehicle (requires S-PIN). With `--confirm`, wait 5 s and re-fetch state. |
| `unlock` | `--confirm` | Unlock. Same flag. |
| `climate-start` | `--temp <C>` | Start climate at the given temperature (default 21, range 16–30). |
| `climate-stop` | — | Stop climate. |
| `heater-start` | `--duration <min>` | Start auxiliary heater for N minutes (default 30, range 10–60). |
| `heater-stop` | — | Stop heater. |
| `watch` | `--interval <s>` | Poll vehicle state every N seconds (default 900, **min 900** — Audi rate-limit budget). Logs changes; if `AUDI_WEBHOOK_URL` is set, also POSTs them. |

Examples:

```bash
python main.py status --brief
python main.py position --open-maps
python main.py lock --confirm
python main.py climate-start --temp 22
python main.py watch --interval 1800
```

## HA command_line sensor

`ha_sensor.py` runs the full connect+update cycle once and prints a JSON object to stdout. It is meant to be wired as a Home Assistant `command_line` sensor.

The script reads the same environment variables as the CLI (loaded from `.env` next to the script). It always queries the **first** vehicle returned by `connect_and_get_vehicles()` — no VIN selection.

Fields in the JSON: `vin`, `model`, `mileage`, `range`, `battery_soc`, `charging_state`, `plug_state`, `latitude`, `longitude`, `doors_locked`, `windows_closed`, `climatisation`. On error, prints `{"error": "<message>"}` and exits 1.

Example HA configuration:

```yaml
command_line:
  - sensor:
      name: My Audi
      command: "/path/to/myaudi-api/.venv/bin/python /path/to/myaudi-api/ha_sensor.py"
      scan_interval: 1800   # 30 min — Audi rate-limit-friendly
      value_template: "{{ value_json.range }}"
      unit_of_measurement: "km"
      json_attributes:
        - vin
        - model
        - mileage
        - battery_soc
        - charging_state
        - plug_state
        - latitude
        - longitude
        - doors_locked
        - windows_closed
        - climatisation
```

## Authentication for REST API

Added in PR #22.

- Set `AUDI_API_KEY` server-side. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- Clients send `X-API-Key: <value>` on every protected endpoint.
- If the header is missing or wrong: **401 Unauthorized**, `{"detail": "Invalid or missing X-API-Key"}`.
- If the server has no `AUDI_API_KEY` set: **503 Service Unavailable**, `{"detail": "API key not configured on server"}`. This fail-closed behaviour prevents an accidentally open API on a misconfigured deploy.

Public endpoints (`/health`, `/ready`, `/metrics`) ignore the header.

## Webhook payloads

When `AUDI_WEBHOOK_URL` is set on the API server, the background watcher POSTs JSON to that URL whenever a vehicle's brief state changes. Payload shape (from `server.py`):

```json
{
  "event": "state_change",
  "vin": "WAUXXXXXXXXXXXXX",
  "title": "My Audi",
  "changes": {
    "locked": {"old": "Locked", "new": "Closed"},
    "range": {"old": "180 km", "new": "175 km"}
  },
  "state": {
    "vehicle": "My Audi",
    "locked": "Closed",
    "position": "50.123, 4.456",
    "maps": "https://www.google.com/maps?q=50.123,4.456",
    "range": "175 km",
    "battery": "65%"
  },
  "timestamp": "2026-05-10T19:42:01.123456+02:00"
}
```

If `AUDI_WEBHOOK_SECRET` is set (PR #24), the request also carries:

```
Content-Type: application/json
X-Audi-Signature: sha256=<hex>
```

Where the hex is `HMAC-SHA256(secret, raw_request_body)`. The receiver MUST verify against the **raw** body (n8n: enable "raw body" on the webhook node before validating). The `state` dict above is the same shape as `AudiVehicle.get_brief()`.
