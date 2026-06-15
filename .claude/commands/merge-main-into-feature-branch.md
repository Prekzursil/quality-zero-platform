---
name: merge-main-into-feature-branch
description: Workflow command scaffold for merge-main-into-feature-branch in quality-zero-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /merge-main-into-feature-branch

Use this workflow when working on **merge-main-into-feature-branch** in `quality-zero-platform`.

## Goal

Keeps a feature or fix branch up to date with the latest changes from main, resolving conflicts and integrating upstream changes.

## Common Files

- `scripts/quality/*.py`
- `tests/*.py`
- `.github/workflows/*.yml`
- `profiles/repos/*.yml`
- `templates/repo/.github/workflows/*.yml`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Checkout the feature branch.
- Merge main into the feature branch.
- Resolve any conflicts.
- Update affected scripts and tests as needed.
- Commit the merge.

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.