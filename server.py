#!/usr/bin/env python3
"""
Audi Connect API — Internal REST microservice.

Exposes vehicle status, position, and remote actions
for an Audi Connect account through an internal HTTP API.

Endpoints:
  GET  /health              Health check
  GET  /vehicles            List vehicles
  GET  /status              Full vehicle status
  GET  /position            Vehicle GPS position
  POST /{vin}/lock          Lock vehicle (requires S-PIN)
  POST /{vin}/unlock        Unlock vehicle (requires S-PIN)
  POST /{vin}/climate/start Start climate control
  POST /{vin}/climate/stop  Stop climate control
  POST /{vin}/heater/start  Start auxiliary heater
  POST /{vin}/heater/stop   Stop auxiliary heater

Requires: pip install fastapi uvicorn aiohttp beautifulsoup4 certifi tenacity
"""

# ---------------------------------------------------------------------------
# SINGLE-REPLICA INVARIANT — DO NOT scale this Deployment beyond replicas: 1.
#
# This service holds in-process state that is NOT safe to run concurrently:
#   * AudiClient (module-global) — caches OAuth tokens, vehicle data, and a
#     ~4h response cache. Two replicas would each hit Audi independently and
#     rapidly exceed the ~6 req/hour upstream rate limit, locking the account.
#   * slowapi Limiter — default in-memory backend; per-replica counters break
#     the documented 30/min-read, 5/min-write quotas.
#   * _background_watcher — one polling loop per replica = 2x the budget.
#   * TokenStore — writes ~/.audi_connect_tokens.json inside the container's
#     filesystem; two replicas would race on the same path on shared volumes
#     and diverge on ephemeral ones.
#
# If you ever need HA, you must first: move the cache + rate limiter to Redis,
# move tokens to a shared store (Secret/CRD/Redis), and gate the watcher
# behind leader election. None of that is implemented here. Keep replicas: 1.
# ---------------------------------------------------------------------------

import asyncio
import contextvars
import hashlib
import hmac
import json
import logging
import os
import secrets
import ssl
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import certifi
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from audi_connect.api import AudiAPI
from audi_connect.auth import AudiAuth
from audi_connect.vehicle import AudiVehicle
from audi_connect.watcher import check_vehicles
from audi_connect.exceptions import (
    AudiConnectError,
    SpinRequiredError,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
AUDI_USERNAME = os.getenv("AUDI_USERNAME", "")
AUDI_PASSWORD = os.getenv("AUDI_PASSWORD", "")
AUDI_COUNTRY = os.getenv("AUDI_COUNTRY", "DE")
AUDI_SPIN = os.getenv("AUDI_SPIN")
AUDI_API_LEVEL = int(os.getenv("AUDI_API_LEVEL", "1"))

# API key required on all endpoints except /health.
# When unset, protected endpoints fail-closed with 503 to avoid an open API.
AUDI_API_KEY = os.getenv("AUDI_API_KEY", "")

TZ = ZoneInfo(os.getenv("TZ", "Europe/Paris"))

# Re-authenticate every 45 minutes (tokens expire after ~1h)
TOKEN_REFRESH_INTERVAL = 45 * 60

# Cache vehicle data to avoid hammering Audi's API (default: 4 hours)
DATA_CACHE_TTL = int(os.getenv("AUDI_CACHE_TTL", "14400"))

# Webhook URL for state change notifications (optional)
WEBHOOK_URL = os.getenv("AUDI_WEBHOOK_URL")

# Optional HMAC-SHA256 signing secret for outgoing webhooks. When set, every
# webhook POST carries an X-Audi-Signature: sha256=<hex> header computed over
# the raw request body. The receiver must verify on the raw body.
WEBHOOK_SECRET = os.getenv("AUDI_WEBHOOK_SECRET", "")

# Watch interval for background polling (default: 0 = disabled)
# Audi's API has aggressive rate limits (~6 req/hour). Enforcing a 15 min minimum
# to avoid account lockout. Set to 0 to disable background polling entirely.
MIN_WATCH_INTERVAL = 15 * 60
_raw_watch_interval = int(os.getenv("AUDI_WATCH_INTERVAL", "0"))
if _raw_watch_interval > 0 and _raw_watch_interval < MIN_WATCH_INTERVAL:
    WATCH_INTERVAL = MIN_WATCH_INTERVAL
else:
    WATCH_INTERVAL = _raw_watch_interval

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [rid=%(request_id)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Request-ID propagation: contextvar set by middleware, read by filter at log-emit
# time. Defined and installed BEFORE any log call so the format string's
# %(request_id)s always resolves (otherwise the watch-interval warning below
# would crash with KeyError before the middleware ever runs).
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Stamp record.request_id from the contextvar so the format string can render it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


for _h in logging.getLogger().handlers:
    _h.addFilter(RequestIdFilter())

from audi_connect.logging_utils import RedactingFilter
for _h in logging.getLogger().handlers:
    _h.addFilter(RedactingFilter())

# Belt-and-suspenders: pin aiohttp loggers to WARNING regardless of root level,
# in case aiohttp adds verbose client logging in future versions or someone
# enables DEBUG on the root logger.
for _name in ("aiohttp.client", "aiohttp.internal", "aiohttp.web", "aiohttp.access"):
    logging.getLogger(_name).setLevel(logging.WARNING)

log = logging.getLogger("audi-api")

if _raw_watch_interval > 0 and _raw_watch_interval < MIN_WATCH_INTERVAL:
    log.warning(
        "AUDI_WATCH_INTERVAL=%ds is below the %ds minimum (Audi rate limits ~6 req/hour). "
        "Clamped to %ds to avoid account lockout.",
        _raw_watch_interval, MIN_WATCH_INTERVAL, WATCH_INTERVAL,
    )


# ---------------------------------------------------------------------------
# Prometheus metrics (business-level — HTTP metrics added by Instrumentator)
# ---------------------------------------------------------------------------
audi_auth_refresh_total = Counter(
    "audi_auth_refresh_total",
    "Audi Connect login or token-refresh attempts.",
    ["result"],
)
audi_cache_operation_total = Counter(
    "audi_cache_operation_total",
    "Vehicle data cache events.",
    ["operation"],
)
audi_action_total = Counter(
    "audi_action_total",
    "Remote vehicle actions issued to Audi Connect.",
    ["action", "result"],
)
audi_backend_request_duration_seconds = Histogram(
    "audi_backend_request_duration_seconds",
    "Latency of upstream Audi/CARIAD calls as seen by this service.",
    ["endpoint"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0),
)


# ---------------------------------------------------------------------------
# Audi Connect Client (cached auth, fully async)
# ---------------------------------------------------------------------------
class AudiClient:
    """Async API client for Audi Connect with cached authentication."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._auth: Optional[AudiAuth] = None
        self.vehicles: list[AudiVehicle] = []
        self.authenticated = False
        self._auth_time = 0.0
        self._auth_lock = asyncio.Lock()
        self._last_update = 0.0
        self._update_lock = asyncio.Lock()

    def _needs_refresh(self) -> bool:
        if not self.authenticated:
            return True
        return (time.time() - self._auth_time) > TOKEN_REFRESH_INTERVAL

    async def ensure_auth(self) -> bool:
        """Login if needed (expired or missing). Thread-safe via lock."""
        if not self._needs_refresh():
            return True
        async with self._auth_lock:
            if not self._needs_refresh():
                return True
            return await self.login()

    async def login(self) -> bool:
        """Full authentication flow."""
        log.info("Connecting to Audi Connect (%s)...", AUDI_USERNAME)
        t0 = time.time()
        try:
            if self._session is None:
                ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                self._session = aiohttp.ClientSession(connector=connector)

            api = AudiAPI(self._session)
            self._auth = AudiAuth(api, country=AUDI_COUNTRY, spin=AUDI_SPIN, api_level=AUDI_API_LEVEL)
            vehicle_list = await self._auth.login(AUDI_USERNAME, AUDI_PASSWORD)
            self.vehicles = [AudiVehicle(self._auth, v) for v in vehicle_list]

            self.authenticated = True
            self._auth_time = time.time()
            log.info("Authenticated — %d vehicle(s)", len(self.vehicles))
            audi_auth_refresh_total.labels(result="success").inc()
            audi_backend_request_duration_seconds.labels(endpoint="login").observe(time.time() - t0)
            return True

        except Exception as e:
            log.error("Authentication failed: %s", e)
            self.authenticated = False
            audi_auth_refresh_total.labels(result="failure").inc()
            return False

    async def update_vehicles(self, force: bool = False) -> None:
        """Update all vehicles data, respecting cache TTL."""
        now = time.time()
        if not force and (now - self._last_update) < DATA_CACHE_TTL:
            audi_cache_operation_total.labels(operation="hit").inc()
            return
        async with self._update_lock:
            # Double-check after acquiring lock
            if not force and (time.time() - self._last_update) < DATA_CACHE_TTL:
                audi_cache_operation_total.labels(operation="hit").inc()
                return
            audi_cache_operation_total.labels(operation="miss").inc()
            t0 = time.time()
            log.info("Updating vehicle data%s...", " (forced)" if force else " (cache expired)")
            for vehicle in self.vehicles:
                try:
                    await vehicle.update()
                except Exception as e:
                    log.error("Failed to update %s: %s", vehicle.vin, e)
            self._last_update = time.time()
            audi_backend_request_duration_seconds.labels(endpoint="update").observe(time.time() - t0)
            log.info("Vehicle data cached for %ds", DATA_CACHE_TTL)

    def invalidate_cache(self) -> None:
        """Force next update_vehicles() call to refresh data."""
        audi_cache_operation_total.labels(operation="invalidate").inc()
        self._last_update = 0.0

    def get_vehicle(self, vin: str) -> Optional[AudiVehicle]:
        """Find a vehicle by VIN (case-insensitive)."""
        for v in self.vehicles:
            if v.vin.upper() == vin.upper():
                return v
        return None

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
client = AudiClient()


_watcher_task: Optional[asyncio.Task] = None


async def _send_webhook(payload: dict) -> None:
    """POST a JSON payload to the configured webhook URL.

    Signs the request with HMAC-SHA256 over the raw body when AUDI_WEBHOOK_SECRET
    is set; otherwise sends an unsigned request (backwards compatible default).
    """
    if not WEBHOOK_URL:
        return
    body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if WEBHOOK_SECRET:
        sig = hmac.new(WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Audi-Signature"] = f"sha256={sig}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                WEBHOOK_URL, data=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 300:
                    log.warning("Webhook returned HTTP %d", resp.status)
    except Exception as e:
        log.warning("Webhook failed: %s", e)


async def _background_watcher() -> None:
    """Background task that polls vehicle data and sends webhooks on state changes."""
    prev_states: dict[str, dict] = {}
    log.info("Background watcher started (interval: %ds, webhook: %s)", WATCH_INTERVAL, WEBHOOK_URL or "none")

    async def _on_change(vehicle, changes, state):
        log.info("State change for %s: %s", vehicle.vin, changes)
        await _send_webhook({
            "event": "state_change",
            "vin": vehicle.vin,
            "title": vehicle.title,
            "changes": changes,
            "state": state,
            "timestamp": datetime.now(TZ).isoformat(),
        })

    while True:
        await asyncio.sleep(WATCH_INTERVAL)
        try:
            if not await client.ensure_auth():
                continue
            await client.update_vehicles(force=True)
            await check_vehicles(client.vehicles, prev_states, on_change=_on_change)
        except Exception as e:
            log.error("Watcher error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Login on startup, start watcher if configured, close on shutdown."""
    global _watcher_task
    if not AUDI_USERNAME or not AUDI_PASSWORD:
        log.error("AUDI_USERNAME and AUDI_PASSWORD env vars are required")
    else:
        await client.login()
    if WATCH_INTERVAL > 0:
        _watcher_task = asyncio.create_task(_background_watcher())
    yield
    if _watcher_task:
        _watcher_task.cancel()
    await client.close()


limiter = Limiter(key_func=get_remote_address)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach an X-Request-ID to every request and propagate it via contextvar.

    Uses a client-provided X-Request-ID when present, otherwise generates a
    12-hex-char uuid. The same value is echoed back in the response header.
    """

    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_ctx.reset(token)


app = FastAPI(
    title="Audi Connect API",
    description="Internal API for Audi Connect vehicle management",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)
app.state.limiter = limiter

Instrumentator(
    should_group_status_codes=True,
    excluded_handlers=["/metrics", "/health", "/ready"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


async def require_api_key(x_api_key: str = Header(default="", alias="X-API-Key")):
    """Authn guard: every protected endpoint requires X-API-Key matching AUDI_API_KEY.

    Fails closed (503) when AUDI_API_KEY is unset on the server, so a misconfigured
    deploy can't accidentally serve an open API.
    """
    if not AUDI_API_KEY:
        raise HTTPException(status_code=503, detail="API key not configured on server")
    if not secrets.compare_digest(x_api_key, AUDI_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


async def _require_auth():
    if not await client.ensure_auth():
        raise HTTPException(status_code=503, detail="Cannot connect to Audi Connect")


def _get_vehicle_or_404(vin: str) -> AudiVehicle:
    vehicle = client.get_vehicle(vin)
    if not vehicle:
        available = [v.vin for v in client.vehicles]
        raise HTTPException(status_code=404, detail=f"VIN '{vin}' not found. Available: {', '.join(available)}")
    return vehicle


# --- Health ---
@app.get("/health")
@limiter.limit("60/minute")
async def health(request: Request):
    cache_age = int(time.time() - client._last_update) if client._last_update else None
    return {
        "status": "ok" if client.authenticated else "degraded",
        "authenticated": client.authenticated,
        "vehicles": [{"vin": v.vin, "model": v.model, "title": v.title} for v in client.vehicles],
        "cache_ttl": DATA_CACHE_TTL,
        "cache_age": cache_age,
        "timestamp": datetime.now(TZ).isoformat(),
    }


# --- Readiness ---
@app.get("/ready")
async def ready(request: Request):
    """Readiness probe — strict. 503 if not authenticated to Audi Connect."""
    if not client.authenticated:
        raise HTTPException(status_code=503, detail="Not authenticated to Audi Connect")
    return {"status": "ready"}


# --- Vehicles ---
@app.get("/vehicles", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def list_vehicles(request: Request):
    await _require_auth()
    return {
        "count": len(client.vehicles),
        "vehicles": [
            {"vin": v.vin, "model": v.model, "title": v.title, "model_year": v.model_year}
            for v in client.vehicles
        ],
    }


# --- Status ---
@app.get("/status", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def get_status(request: Request, vin: Optional[str] = Query(None, description="Filter by VIN")):
    await _require_auth()
    await client.update_vehicles()

    vehicles = client.vehicles
    if vin:
        vehicles = [_get_vehicle_or_404(vin)]

    return {
        "count": len(vehicles),
        "vehicles": [
            {"vin": v.vin, "model": v.model, "title": v.title, **v.get_dashboard()}
            for v in vehicles
        ],
    }


# --- Brief status ---
@app.get("/brief", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def get_brief(request: Request, vin: Optional[str] = Query(None, description="Filter by VIN")):
    """Quick status: locked, position, range — the essentials."""
    await _require_auth()
    await client.update_vehicles()

    vehicles = client.vehicles
    if vin:
        vehicles = [_get_vehicle_or_404(vin)]

    return {
        "vehicles": [v.get_brief() for v in vehicles],
    }


# --- Position ---
@app.get("/position", dependencies=[Depends(require_api_key)])
@limiter.limit("30/minute")
async def get_position(request: Request, vin: Optional[str] = Query(None, description="Filter by VIN")):
    await _require_auth()
    await client.update_vehicles()

    vehicles = client.vehicles
    if vin:
        vehicles = [_get_vehicle_or_404(vin)]

    results = []
    for vehicle in vehicles:
        pos = vehicle.position
        data = {"vin": vehicle.vin, "title": vehicle.title, "is_moving": vehicle.is_moving}
        if pos and pos.get("latitude"):
            data["latitude"] = pos["latitude"]
            data["longitude"] = pos["longitude"]
            data["google_maps"] = f"https://www.google.com/maps?q={pos['latitude']},{pos['longitude']}"
        results.append(data)

    return {"vehicles": results}


# --- Lock / Unlock ---
@app.post("/{vin}/lock", dependencies=[Depends(require_api_key)])
@limiter.limit("5/minute")
async def lock_vehicle(request: Request, vin: str, confirm: bool = Query(False, description="Wait and confirm action status")):
    await _require_auth()
    vehicle = _get_vehicle_or_404(vin)
    try:
        await _track_action("lock", vehicle, vehicle.lock())
        result = {"status": "sent", "action": "lock", "vin": vehicle.vin}
        if confirm:
            result.update(await _confirm_action(vehicle, "doors_trunk", "Locked"))
        return result
    except SpinRequiredError:
        raise HTTPException(status_code=400, detail="S-PIN not configured")
    except AudiConnectError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/{vin}/unlock", dependencies=[Depends(require_api_key)])
@limiter.limit("5/minute")
async def unlock_vehicle(request: Request, vin: str, confirm: bool = Query(False, description="Wait and confirm action status")):
    await _require_auth()
    vehicle = _get_vehicle_or_404(vin)
    try:
        await _track_action("unlock", vehicle, vehicle.unlock())
        result = {"status": "sent", "action": "unlock", "vin": vehicle.vin}
        if confirm:
            result.update(await _confirm_action(vehicle, "doors_trunk", "Closed"))
        return result
    except SpinRequiredError:
        raise HTTPException(status_code=400, detail="S-PIN not configured")
    except AudiConnectError as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Climate ---
@app.post("/{vin}/climate/start", dependencies=[Depends(require_api_key)])
@limiter.limit("5/minute")
async def start_climate(request: Request, vin: str, temp: float = Query(21.0, ge=16, le=30, description="Temperature in C"), confirm: bool = Query(False)):
    await _require_auth()
    vehicle = _get_vehicle_or_404(vin)
    try:
        await _track_action("climate_start", vehicle, vehicle.start_climatisation(temp_c=temp))
        result = {"status": "sent", "action": "climate_start", "temperature": temp, "vin": vehicle.vin}
        if confirm:
            result.update(await _confirm_action(vehicle, "climatisation", None))
        return result
    except AudiConnectError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/{vin}/climate/stop", dependencies=[Depends(require_api_key)])
@limiter.limit("5/minute")
async def stop_climate(request: Request, vin: str, confirm: bool = Query(False)):
    await _require_auth()
    vehicle = _get_vehicle_or_404(vin)
    try:
        await _track_action("climate_stop", vehicle, vehicle.stop_climatisation())
        result = {"status": "sent", "action": "climate_stop", "vin": vehicle.vin}
        if confirm:
            result.update(await _confirm_action(vehicle, "climatisation", None))
        return result
    except AudiConnectError as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Heater ---
@app.post("/{vin}/heater/start", dependencies=[Depends(require_api_key)])
@limiter.limit("5/minute")
async def start_heater(request: Request, vin: str, duration: int = Query(30, ge=10, le=60, description="Duration in minutes"), confirm: bool = Query(False)):
    await _require_auth()
    vehicle = _get_vehicle_or_404(vin)
    try:
        await _track_action("heater_start", vehicle, vehicle.start_preheater(duration=duration))
        result = {"status": "sent", "action": "heater_start", "duration": duration, "vin": vehicle.vin}
        if confirm:
            result.update(await _confirm_action(vehicle, None, None))
        return result
    except AudiConnectError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/{vin}/heater/stop", dependencies=[Depends(require_api_key)])
@limiter.limit("5/minute")
async def stop_heater(request: Request, vin: str, confirm: bool = Query(False)):
    await _require_auth()
    vehicle = _get_vehicle_or_404(vin)
    try:
        await _track_action("heater_stop", vehicle, vehicle.stop_preheater())
        result = {"status": "sent", "action": "heater_stop", "vin": vehicle.vin}
        if confirm:
            result.update(await _confirm_action(vehicle, None, None))
        return result
    except AudiConnectError as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _confirm_action(vehicle: AudiVehicle, check_field: Optional[str], expected_value: Optional[str]) -> dict:
    """Wait a few seconds, re-fetch vehicle data via the cache-coordinated path,
    and check if the action was applied. Goes through client.update_vehicles()
    so concurrent confirms are serialized by _update_lock and don't fan out
    parallel selectivestatus calls against the ~6 req/h Audi budget."""
    client.invalidate_cache()
    await asyncio.sleep(5)
    try:
        await client.update_vehicles(force=True)
        dashboard = vehicle.get_dashboard()
        confirmed = True
        if check_field and expected_value:
            confirmed = dashboard.get(check_field) == expected_value
        return {"status": "confirmed" if confirmed else "pending", "vehicle_status": dashboard}
    except Exception as e:
        log.warning("Could not confirm action: %s", e)
        return {"status": "sent_unconfirmed", "detail": str(e)}


async def _track_action(action: str, vehicle: AudiVehicle, coro):
    """Wrap an action coroutine to emit Prometheus metrics."""
    t0 = time.time()
    try:
        await coro
    except Exception:
        audi_action_total.labels(action=action, result="failure").inc()
        raise
    finally:
        audi_backend_request_duration_seconds.labels(endpoint=action).observe(time.time() - t0)
    audi_action_total.labels(action=action, result="success").inc()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
