# `.claude/` — Project configuration for Claude Code

This directory configures how Claude Code behaves when working on this project. Everything in here (except `settings.local.json`) is committed to git so collaborators get the same experience.

## Structure

```
.claude/
├── settings.json                  # Permissions: what Claude can run without asking
├── settings.local.json.example    # Copy to settings.local.json for your local overrides (gitignored)
├── agents/
│   ├── code-reviewer.md           # Pre-commit reviewer
│   └── test-writer.md             # Writes tests in the project's patterns
└── commands/
    ├── test.md                    # /test — run pytest
    ├── coverage.md                # /coverage — pytest with coverage report
    ├── security-check.md          # /security-check — security audit
    ├── release-prep.md            # /release-prep — pre-merge validation
    ├── audit-ratelimits.md        # /audit-ratelimits — confirm rate limits are conservative
    └── explain-oauth.md           # /explain-oauth — diagnose OAuth login failures
```

## Slash commands

Type `/` in Claude Code to see them. Quick reference:

| Command | When to use |
|---|---|
| `/test` | Anytime — runs `pytest tests/ -v`. Should be 145+ tests passing. |
| `/coverage` | When adding new code — flags low-coverage modules. |
| `/security-check` | Before committing anything that touches auth, secrets, or rate limits. |
| `/release-prep` | Before pushing to `main` — CI ships to GHCR + ArgoCD on every commit. |
| `/audit-ratelimits` | When you change anything related to polling, caching, or HTTP retry. |
| `/explain-oauth` | When login breaks. Walks through the 13-step flow with diagnostics. |

## Subagents

Invoke with the Agent tool, or ask Claude to "use the code-reviewer agent" / "use the test-writer agent".

- **`code-reviewer`** — reads the staged diff and runs through this project's specific checklist (rate limits, idempotency, OAuth, single-replica, secrets in logs).
- **`test-writer`** — picks the right testing tool (`AsyncMock` vs `aioresponses`), follows existing fixture patterns, and runs the test before handing back.

## Local overrides

The shared `settings.json` is intentionally strict:
- ❌ blocks all real Audi API calls (`python main.py lock`, `unlock`, `climate-*`, `heater-*`, `watch`)
- ❌ blocks reads of `.env` and the token cache
- ❌ blocks pushes to `main`, `docker push`, `kubectl delete/apply`, `argocd app sync`
- ✅ allows tests, lint, git read ops, local docker build, edits

If you want Claude to be able to run **read-only** API calls (`status`, `position`) or start `uvicorn` locally, copy `settings.local.json.example` to `settings.local.json`. That file is gitignored so your overrides stay local.

```bash
cp .claude/settings.local.json.example .claude/settings.local.json
```

## Why these restrictions?

- **`python main.py lock/unlock/climate/heater/watch` are denied** — they actually act on the vehicle and consume Audi's ~6 req/h quota. A wayward Claude session could lock your account.
- **`.env` and `~/.audi_connect_tokens.json` are denied** — credentials and OAuth tokens. If Claude needs values from `.env`, paste them yourself.
- **`docker push`, `kubectl apply`, `argocd app sync` are denied** — deploys go through CI (`.github/workflows/build.yml` → GHCR → ArgoCD). Don't bypass it.
- **`git push origin main` is denied** — `main` ships to prod via the GitOps pipeline. PRs only.
- **`oauth.py` is not denied at the file level**, but the `code-reviewer` agent will block any commit that touches it without evidence of manual testing against a real Audi account.

## Adding new agents/commands

Keep them project-specific. Generic stuff (`/format`, `/grep`) doesn't earn a slot — those are already built in.

A new command earns its place when:
- It encodes project-specific knowledge (e.g. "the rate limit budget is ~6 req/h")
- It runs a multi-step workflow you'd otherwise type by hand every time
- It connects to a sensitive area (OAuth, S-PIN, cache invalidation)
