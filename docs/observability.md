# Observability

## Prometheus metrics

`GET /metrics` exposes Prometheus text format. The exporter is `prometheus-fastapi-instrumentator` configured in `server.py` to skip `/metrics`, `/health`, `/ready` from HTTP-side metrics so kubelet and Prometheus scrapes don't drown the signal.

### Standard HTTP metrics (added by `Instrumentator`)

These are emitted automatically for every other route:

- `http_requests_total{handler, method, status}` — Counter.
- `http_request_duration_seconds{handler, method}` — Histogram.
- `http_request_size_bytes{handler, method}` — Summary.
- `http_response_size_bytes{handler, method}` — Summary.
- `http_requests_inprogress{handler, method}` — Gauge.

### Business metrics

Defined in `server.py` (lines 164–186):

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `audi_auth_refresh_total` | Counter | `result` | Login or token-refresh attempts. Values: `success` / `failure` (full login), `refresh_success` / `refresh_failure` (incremental refresh). See [auth-lifecycle.md](auth-lifecycle.md). |
| `audi_cache_operation_total` | Counter | `operation` | Vehicle data cache events. Values: `hit` / `miss` / `invalidate`. |
| `audi_action_total` | Counter | `action`, `result` | Remote actions. `action` ∈ `{lock, unlock, climate_start, climate_stop, heater_start, heater_stop}`. `result` ∈ `{success, failure}`. |
| `audi_backend_request_duration_seconds` | Histogram | `endpoint` | Latency of upstream Audi/CARIAD calls. `endpoint` ∈ `{login, update, lock, unlock, climate_start, climate_stop, heater_start, heater_stop}`. Buckets: 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0 seconds. |

## Health endpoints

| Endpoint | Semantics |
|---|---|
| `GET /health` | Liveness. Returns 200 with `{"status": "ok"\|"degraded", "authenticated": bool, "vehicles": [...], "cache_ttl": int, "cache_age": int\|null, "timestamp": iso8601}`. **Always 200** while the process is alive — Audi being unreachable is `degraded`, not unhealthy. Kubelet must use this for `livenessProbe`. |
| `GET /ready` | Readiness. **503** with `{"detail": "Not authenticated to Audi Connect"}` until the first successful `ensure_auth()` populates the auth context. Kubelet uses this for `readinessProbe` so traffic is held off the pod until OAuth has completed. |

## Request-ID middleware

Installed as `RequestIdMiddleware` in `server.py` (PR #26).

- Reads the inbound `X-Request-ID` header. If missing, generates a 12-hex-char id (`uuid.uuid4().hex[:12]`).
- Echoes the value back on the response `X-Request-ID` header.
- Propagates via a `contextvars.ContextVar` so the value is visible from inside any awaited code.

A separate `RequestIdFilter` (logging filter) reads the contextvar and stamps `record.request_id`. The format string `"%(asctime)s [%(levelname)s] [rid=%(request_id)s] %(name)s: %(message)s"` renders it as `[rid=...]` on every log line. End-to-end correlation in Loki/Grafana: filter by `rid` value.

The filter is also installed in `main.py` for symmetry with the CLI, where the rid stays at the default `"-"` since there's no HTTP middleware to populate it.

## Log redaction

`audi_connect/logging_utils.py` — `RedactingFilter`. Installed on the root logger handlers in both `server.py` and `main.py`. Patterns:

| Pattern | Matches | Redacts to |
|---|---|---|
| `_JSON_KEY_PATTERN` | JSON keys: `access_token`, `refresh_token`, `id_token`, `securityToken`, `securityPinHash`, `hmac`, `password`, `spin`, `client_secret`, `code_verifier` (case-insensitive) | `"<key>": "***"` |
| `_BEARER_PATTERN` | `Bearer <opaque-token>` (case-insensitive) | `Bearer ***` |
| `_QMAUTH_PATTERN` | `v1:<hex>:<hex>{8,}` (the X-QMAuth header value) | `v1:<hex>:***` |
| `_EMAIL_PATTERN` | `<chars1-3><rest>@<domain>` | `<chars1-3>***@<domain>` (keeps the first 1–3 characters and the domain — enough to recognise an account, not enough to disclose the full address) |

VINs are **not** redacted by this filter. They are not secrets, but treat the last 4 chars convention used in `vehicle.update()` log lines (`%s (%s)`, `self.title or self.vin, self.vin[-4:]`) as the project default.

`aiohttp.client`, `aiohttp.internal`, `aiohttp.web`, `aiohttp.access` loggers are pinned to WARNING regardless of the root level (PR #24) — defence in depth so a future debug-mode change upstream cannot accidentally leak request bodies.

## Suggested Grafana dashboards

- **Auth health** — single-stat or graph of:
  - `sum(rate(audi_auth_refresh_total{result="refresh_success"}[1h])) / sum(rate(audi_auth_refresh_total[1h]))` — should sit near 1.0 in steady state.
  - Alert below 0.7 over 6h: refresh tokens are being rejected, the upstream password may have been rotated.
- **Audi rate-limit headroom** — `sum(rate(audi_backend_request_duration_seconds_count[1h]))` should stay well under 6/h. Approaches the limit only on action storms or watcher misconfiguration.
- **Cache efficacy** — `sum(rate(audi_cache_operation_total{operation="hit"}[1h])) / sum(rate(audi_cache_operation_total{operation=~"hit|miss"}[1h]))`. With the 4h cache TTL and a single user, this should run >0.9.
- **Action mix** — stacked graph of `sum by (action, result) (rate(audi_action_total[24h]))` to spot unexpected `failure` spikes.
- **Latency** — `histogram_quantile(0.95, sum by (le, endpoint) (rate(audi_backend_request_duration_seconds_bucket[5m])))` per endpoint. `login` p95 around 5–10s is normal; `update` should be 1–3s.
