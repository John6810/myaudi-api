# Security Policy

## Supported versions

Only the latest release on `main` is supported. Older tags are not patched.

| Version | Supported |
|---------|-----------|
| `main`  | ✅ |
| older   | ❌ |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Instead, report via GitHub's private vulnerability reporting:

1. Go to the [Security tab of this repository](https://github.com/John6810/myaudi-api/security)
2. Click "Report a vulnerability"
3. Fill in the form

Alternatively, contact the maintainer directly via the email listed on the GitHub profile.

## What I commit to

This is a personal homelab project maintained on best-effort. I will:

- Acknowledge receipt within 7 days
- Investigate and triage in good faith
- Coordinate disclosure with the reporter
- Credit the reporter in the release notes (unless they prefer anonymity)

I will not:

- Pay bug bounties
- Provide commercial-grade SLAs

## Out of scope

- Vulnerabilities in the Audi Connect / CARIAD / VW Group APIs themselves (report to Audi)
- Issues that require physical access to the vehicle
- Issues that depend on credentials being already compromised (e.g., a leaked AUDI_PASSWORD or AUDI_API_KEY) — those are operator hygiene, not project vulnerabilities
- Side-channel issues that require attacker control over the K8s cluster or the host running the CLI

## Security features in place

Documented for transparency, not as a guarantee:

- X-API-Key authentication on all REST endpoints except /health
- HMAC-SHA256 signing available for outgoing webhooks
- Log redaction filter masks bearer tokens, OAuth secrets, and emails
- getpass for sensitive CLI prompts (no terminal echo, no shell history)
- Token cache file restricted to 0o600 on Unix
- Conservative Audi API rate limits to avoid account lockout
- Dependabot version updates and security updates enabled
- Secret scanning + push protection enabled
