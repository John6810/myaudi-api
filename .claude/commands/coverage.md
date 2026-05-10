---
description: Run pytest with coverage and report uncovered lines
allowed-tools: Bash(python -m pytest*), Bash(python -m pip install*), Bash(coverage*)
---

Run the test suite with coverage. If `pytest-cov` isn't installed, install it first.

!`python -m pip install pytest-cov 2>/dev/null; python -m pytest tests/ --cov=audi_connect --cov-report=term-missing -v`

After the run, identify modules under `audi_connect/` with coverage below 80% and suggest where to add tests. Pay special attention to:
- New code added in recent commits (check `git log --oneline -10`)
- Branches in `vehicle.py` (validation, brief, dashboard)
- Error paths in `client.py` and `actions.py`

Don't suggest adding tests to `oauth.py` — that flow is fragile and should only be tested manually against a real account.
