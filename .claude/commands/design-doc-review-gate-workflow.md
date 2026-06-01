---
name: design-doc-review-gate-workflow
description: Workflow command scaffold for design-doc-review-gate-workflow in quality-zero-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /design-doc-review-gate-workflow

Use this workflow when working on **design-doc-review-gate-workflow** in `quality-zero-platform`.

## Goal

Iterative design and review of a major subsystem or feature, capturing decisions, blockers, and addenda in design documents, with plan tracking.

## Common Files

- `docs/plans/2026-06-01-truthful-gate-subsystem-design.md`
- `.beads/plans/active-plan.md`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Create or update a detailed design document in docs/plans/
- Update the active plan pointer in .beads/plans/active-plan.md
- Iterate with addenda to the design doc as review rounds progress (Addendum A, B, C, D, etc.)
- Lock in decisions and record closure of blockers
- Mark design-review-gate as passed in the documentation

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.