# Contributing

Thanks for your interest in myaudi-api!

## Getting Started

```bash
git clone https://github.com/John6810/myaudi-api.git
cd myaudi-api
pip install -r requirements.txt
python -m pytest tests/ -v
```

## Development Workflow

1. Create a branch from `main`
2. Make your changes
3. Run tests: `python -m pytest tests/ -v`
4. Push and open a PR against `main`

## Code Style

- Python 3.12+, PEP 8
- Type hints on public functions
- Async-first (`async`/`await` for all I/O)
- Use `logging` module (INFO for lifecycle, ERROR for failures)
- No unnecessary dependencies

## Tests

All changes should include tests. Run the full suite before submitting:

```bash
python -m pytest tests/ -v
```

- Unit tests: `unittest.mock` / `AsyncMock`
- Integration tests: `aioresponses` for HTTP mocking
- Target: maintain 145+ tests passing

## Reporting Bugs

Use the [bug report template](https://github.com/John6810/myaudi-api/issues/new?template=bug_report.md). Include `-v` logs (redact credentials).

## Feature Requests

Use the [feature request template](https://github.com/John6810/myaudi-api/issues/new?template=feature_request.md). Check [existing issues](https://github.com/John6810/myaudi-api/issues) first.

## Security

Never commit credentials, tokens, or `.env` files. If you discover a security vulnerability, please report it privately rather than opening a public issue.
