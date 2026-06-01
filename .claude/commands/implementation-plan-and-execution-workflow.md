---
name: implementation-plan-and-execution-workflow
description: Workflow command scaffold for implementation-plan-and-execution-workflow in quality-zero-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /implementation-plan-and-execution-workflow

Use this workflow when working on **implementation-plan-and-execution-workflow** in `quality-zero-platform`.

## Goal

Writing an implementation plan for a feature (e.g., a TG milestone), iterating after review, and then implementing the feature with code, tests, and workflow updates.

## Common Files

- `docs/plans/2026-06-01-truthful-gate-tg2-token-preflight-plan.md`
- `scripts/quality/truth/preflight.py`
- `scripts/quality/alerts.py`
- `.github/workflows/scheduled-alerts.yml`
- `pyproject.toml`
- `tests/test_alerts.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Write an initial implementation plan in docs/plans/ (e.g., -tg2-token-preflight-plan.md)
- Revise the plan after review (e.g., fold in must-fixes)
- Implement the feature: add code (scripts/quality/...), update workflows (.github/workflows/...), update configuration (pyproject.toml), and write tests (tests/...)

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.