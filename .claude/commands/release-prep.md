---
description: Prep work before pushing to main (tests + lint + commit message check + changelog suggestion)
allowed-tools: Read, Grep, Glob, Bash(python -m pytest*), Bash(git log*), Bash(git diff*), Bash(git status), Bash(ruff*)
---

Run pre-release validation. The CI (`.github/workflows/build.yml`) builds and pushes to GHCR + updates ArgoCD on every commit to `main`, so what lands on `main` ships immediately. Be thorough.

## Steps

### 1. Working tree clean?
`git status` — flag any uncommitted changes.

### 2. Tests pass
`python -m pytest tests/ -v` — must be 145 minimum, all green.

### 3. Lint (if ruff is configured)
Try `ruff check audi_connect/ api.py main.py ha_sensor.py` — if ruff isn't installed, skip silently.

### 4. Recent commits review
`git log origin/main..HEAD --oneline` — list everything that's about to ship.

For each commit, check:
- Does the message follow conventional commits style? (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`)
- Is it a single logical change, or does it bundle unrelated stuff?

### 5. Diff review against main
`git diff origin/main..HEAD --stat` — what files changed? Pay attention to:
- `audi_connect/oauth.py` — was it touched? If yes, was it manually tested against a real Audi account? **Do not ship without confirmation.**
- `api.py` (root) and `Dockerfile` — these affect the deployed image
- `requirements.txt` — new deps need justification
- `.github/workflows/build.yml` — pipeline changes affect the deploy

### 6. CLAUDE.md / README freshness
If the diff includes user-facing changes (new env var, new endpoint, new CLI command), CLAUDE.md and README.md should reflect them. Flag if stale.

### 7. Suggested changelog entry
Based on the commits, propose a short user-facing changelog line like:
```
## v1.x.x
- feat: webhook HMAC signature
- fix: climate-stop now respects api_level=0
- chore: bump aiohttp to 3.10
```

## Output

```
## Release Prep — <branch_name>

### Status
- Working tree: clean / dirty
- Tests: ✅ N/N passing
- Lint: ✅ / ⚠️ N issues / skipped

### Commits ready to ship
- abc1234 feat: ...
- def5678 fix: ...

### Concerns
- ...

### Suggested changelog
...

### Verdict
SHIP IT / HOLD ON
```

Don't push — that's the user's call. Just report.
