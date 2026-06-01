---
name: code-and-test-update-workflow
description: Workflow command scaffold for code-and-test-update-workflow in quality-zero-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /code-and-test-update-workflow

Use this workflow when working on **code-and-test-update-workflow** in `quality-zero-platform`.

## Goal

Making a targeted fix or feature update that touches implementation code and its corresponding tests, often in response to review or bugfix.

## Common Files

- `scripts/quality/truth/preflight.py`
- `tests/test_truth_preflight.py`
- `.github/workflows/scheduled-alerts.yml`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Update implementation code (e.g., scripts/quality/truth/preflight.py)
- Update or add corresponding tests (e.g., tests/test_truth_preflight.py)
- Optionally update related workflow files if behavior affects CI

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.