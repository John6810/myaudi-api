# Auth lifecycle

## Session persistence

`AudiAuth` holds an `OAuthState` with the IDK, AZS and MBB tokens and their endpoint
metadata. `TokenStore` persists that state to `~/.audi_connect_tokens.json` using
an owner-only temporary file and atomic replacement. The default maximum file
age is 30 days; access-token lifetimes are shorter. Keep this file across restarts.

A successful MBB or IDK refresh can rotate a refresh token before the next
exchange fails. Each rotation is checkpointed immediately. Checkpoints retain the
previous `saved_at`; only the complete refresh writes a fresh timestamp. Responses
that omit a new refresh token retain the existing one.

## Restore and refresh

1. Restore an eligible cache and refresh if its MBB expiry gate requires it.
2. Validate the session by fetching the vehicle list once.
3. If a fresh-cache validation is rejected for authentication, force one token
   refresh to account for the shorter-lived AZS token, then validate once more.
4. Network errors, 429s and other transient failures preserve the token file and
   propagate. They do not trigger another refresh or full login in the same call.
5. An explicit `invalid_grant` on a refresh-token grant clears the rejected session
   and permits a full login. `invalid_grant` on the AZS exchange does not prove
   that a refresh token is invalid, so it also preserves the session.

The server normally refreshes every 45 minutes, on demand. Concurrent requests
share an authentication lock. After a transient failure it waits at least 15
minutes, or longer if the upstream 429 backoff is still active. The existing
vehicle list stays available for a successful incremental recovery. A full login
replaces the list and invalidates vehicle-data caching.

## EU full login limitation

A new EU login requests a device grant. When Audi rejects it with
`unauthorized_client`, the client raises `DeviceGrantRejectedError`, shows the
reason rather than blaming credentials, and the server stops automatic login
attempts until configuration is changed and the process restarted. It does not
try the blocked EU password flow as a fallback.

The existing PR #63 supplies this diagnostic. It does not unblock fresh EU
sessions. See [OAuth findings](oauth-flow.md#eu-login-status-2026-10-01) and
[request policy and current community sources](request-policy.md). Existing
refresh tokens can expire or be revoked; neither persistence nor passing mocked
tests guarantees continued provider acceptance.

## Observability

`audi_auth_refresh_total` distinguishes login success/failure and incremental
refresh success/failure. `/ready` is unavailable after an auth failure. The data
cache only advances its successful-update timestamp after status reads succeed;
failed reads return 503 with `Retry-After`, rather than masquerading as fresh data.
