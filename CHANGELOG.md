# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-05-10

Hardening release. The project gains a complete security, observability,
and architectural baseline after a focused 14-PR session.

### Added

- X-API-Key authentication on all REST endpoints except /health (#22)
- getpass-based prompt for password and S-PIN in `python main.py setup` (#22)
- Optional HMAC-SHA256 signing of outgoing webhooks via AUDI_WEBHOOK_SECRET, sent in the X-Audi-Signature header (#24)
- RedactingFilter that masks bearer tokens, OAuth JSON keys, X-QMAuth values, and emails from every log record (#24)
- aiohttp loggers pinned to WARNING regardless of root level (#24)
- /metrics endpoint exposing Prometheus text format with 4 custom business metrics (auth refresh, cache events, action counters, upstream Audi latency histogram) (#26)
- /ready endpoint as a strict readiness probe (503 until authenticated to Audi Connect) (#26)
- X-Request-ID middleware: every request gets a 12-char id, propagated to log records via contextvars (#26)
- HEALTHCHECK directive in the Dockerfile and BuildKit pip cache mounts (#26)
- audi_connect/endpoints.py: AudiEndpoints class shared between client and actions for URL building and home-region resolution (#27)
- audi_connect/oauth_state.py: frozen OAuthState dataclass replacing the previous 10 OAuth attributes scattered on AudiAuth (#27)
- prometheus-fastapi-instrumentator dependency (#26)
- Single-replica invariant banner in api.py and token_store.py (#26)
- GitHub Actions test gating: pytest must pass before image build, with PR builds testing without pushing to GHCR (#20, #21)
- Repository security features enabled: Dependabot version updates, Dependabot security updates (auto-PR for CVEs), secret scanning, push protection

### Changed

- Root entry point renamed: api.py → server.py (the FastAPI app), disambiguating from audi_connect/api.py (the low-level HTTP client) (#27)
- VehicleDataResponse.data_fields and .states indexed by name for O(1) lookup; get_dashboard() goes from ~600 string comparisons to ~25 dict lookups per call (#25)
- _confirm_action now routes through client.update_vehicles(force=True) to honor _update_lock — concurrent ?confirm=true calls no longer fan out parallel selectivestatus requests against the ~6 req/h Audi budget (#25)
- tenacity bumped from 8.x to 9.1.4 (#17, no API change touched our code — only the .statistics attribute changed in 9.0)
- Dependencies refreshed: certifi 2026.4.22, python-dotenv 1.2.2, beautifulsoup4 4.14.3, uvicorn 0.46.0 (#15, #16, #18, #19)

### Removed

- Dead code: AudiVehicleActions.set_charge_mode, AudiVehicleClient.get_charger/get_climater/get_preheater, AudiAPI.put/post, models.VehiclesResponse, models.VehicleInfo, and their tests (#25)
- json.loads(object_hook=obj_parser) in the HTTP transport — the datetime format never matched real Audi timestamps, making it a no-op CPU tax with surprising mutation risk (#25)
- Internal wrappers _do_start_climatisation and _do_start_preheater, obsoleted by the retry policy split (#23)
- Consume-once _cached_vehicle_list hack on AudiAuth — login() now returns the validated list directly (#27)

### Fixed

- Retry policy correctness: lock, stop_climatisation and stop_preheater retain idempotent retry; unlock, start_climatisation and start_preheater no longer retry at the metier layer to avoid duplicate fire on the Audi rate budget (#23)
- Encapsulation: actions.py no longer reaches into AudiVehicleClient private methods (_get_cariad_url_for_vin, _get_home_region_*) — both layers share an injected AudiEndpoints (#27)
- Trivy step removed from CI after pinning a non-existent action version blocked image builds (#21)

### Security

- REST API now requires X-API-Key on every endpoint except /health (#22) — this is a **breaking change** for callers; AUDI_API_KEY must be set on the server and propagated to clients
- Interactive setup never echoes the password or S-PIN to the terminal (#22)
- Token caches retained at 0o600 permissions on Unix (existing behavior confirmed and documented)
