# myaudi-api — Technical Documentation

This directory contains in-depth technical reference. For project overview and quickstart, see the [main README](../README.md).

## Contents

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | Layered module structure with a Mermaid flowchart and a per-module responsibility table. |
| [oauth-flow.md](oauth-flow.md) | The 13-step OAuth/OIDC login flow used to authenticate against myAudi, including a sequence diagram, the X-QMAuth HMAC, and known failure modes. |
| [auth-lifecycle.md](auth-lifecycle.md) | Token caching layers, refresh-vs-login policy, and the `ensure_auth()` flowchart wired to `refresh_tokens` (PR #31). |
| [api-reference.md](api-reference.md) | REST endpoints (server.py), CLI subcommands (main.py), Home Assistant `command_line` sensor (ha_sensor.py), and webhook payload format. |
| [observability.md](observability.md) | Prometheus metrics, `/health` and `/ready` semantics, request-id middleware, and log redaction. |
| [deployment.md](deployment.md) | Image build pipeline, Kubernetes/ArgoCD GitOps wiring, sealed-secrets layout, and pending probe/ServiceMonitor work. |
| [development.md](development.md) | Test layout, mocking patterns, and a mini-tutorial for adding a new endpoint. |

## Conventions

- **Diagrams**: Mermaid only. Rendered natively by GitHub (no external tooling needed).
- **Language**: English everywhere — code, commits, docs.
- **Synchronization**: PRs that touch the code MUST update the relevant document. Drift between code and docs is a bug; see PR #28 for what catching up looks like.
- **No duplication**: this directory is for in-depth reference. The main `README.md` covers overview/quickstart; `CLAUDE.md` covers notes for AI assistants.
