---
description: Run the project's test suite (pytest)
allowed-tools: Bash(python -m pytest*)
---

Run the full test suite with verbose output. The baseline is 145 tests passing.

If the user passes arguments via `$ARGUMENTS`, forward them to pytest. Otherwise run the default.

!`python -m pytest tests/ -v $ARGUMENTS`

After the run:
- If all tests pass, summarize: "✅ N tests passed in Xs"
- If any failed, list the failures with their assertion errors and propose a fix
- If the count is below 145, that's a regression — flag it
