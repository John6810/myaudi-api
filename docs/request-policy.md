# Upstream request policy

## Evidence checked on 2026-10-08

No official universal quota of “6 requests/hour” was established for the private
myAudi endpoints used here. A polling cycle can make several HTTP calls per
vehicle; the local intervals below are safeguards, not an Audi service guarantee.

- [Home Assistant Audi MMI discussion](https://community.home-assistant.io/t/audi-mmi-support/46543?page=9)
  distinguishes cloud reads from actively refreshing/waking the car. This is
  historical user evidence (January 2020), not a current quota specification.
- [Recent users discuss Audi integration breakage](https://www.reddit.com/r/homeassistant/comments/1wtk950/has_anybody_got_there_audi_car_working_on/).
  Individual reports that another integration works do not establish that a new
  account can authenticate through this client's flow.
- [VW Group Connect maintainer, issue #1364](https://github.com/its-me-prash/vwgroup-connect-ha/issues/1364#issuecomment-5570482595)
  reports the Audi device-grant refusal and app-attestation barrier after testing
  an Audi account. [ioBroker's September 2026 release notes](https://github.com/TA2k/ioBroker.vw-connect#0911-2026-09-23)
  likewise disable classic myAudi login.
- [Audi Belgium's official data-access page](https://www.audi.be/fr/eu-data-act/)
  points owners to its EU Data Act portal. This is a separate data-access route;
  it does not document a working replacement for this API's remote commands.

## Implemented safeguards

- Default vehicle cache TTL: four hours. Background watcher: disabled by default.
- Server data polling: at least 15 minutes between attempts, shared by REST reads,
  forced confirmations and the watcher. This is per process; keep one replica.
- A failed status read raises 503 with `Retry-After`. It does not advance the last
  successful cache timestamp. Subsequent reads during backoff make no new calls.
- The watcher compares data already fetched by the server instead of fetching it
  a second time. Actions invalidate the cache, but cannot override the minimum
  polling interval. Cached telemetry can therefore remain visible during that
  interval; it cannot confirm a newer command.
- Transport retries: at most three attempts for GET/HEAD/OPTIONS transport failures.
  POST requests are never replayed there, including OAuth token exchanges.
- Only lock, climate-stop and heater-stop retry at the action layer, at most three
  attempts on transport errors or 5xx. Client errors, including 401/403/429, and
  task cancellation are never retried.
- Any HTTP 429 pauses subsequent requests made through that AudiAPI instance for
  at least one hour, or longer when `Retry-After` says so (seconds or HTTP date).
  Restarting the process loses in-memory backoff; do not use restarts to retry a
  rejected request.
- Transient authentication failures retain the session and delay retries by at
  least 15 minutes. An explicit `unauthorized_client` device refusal disables
  further login attempts until an operator changes configuration/restarts.
- MBB and IDK token rotations are persisted immediately, even if a later token
  exchange fails. Partial rotation keeps the previous `saved_at`; only a complete
  successful refresh marks the session fresh. File replacement is atomic, with
  owner-only permissions from creation.

## Validation

The pytest suite blocks live network connections and redirects the default token
store to a temporary directory. HTTP scenarios use `aioresponses`; no vehicle
commands or Audi account logins are required. A successful suite validates local
behavior, not provider acceptance of a new EU login.
