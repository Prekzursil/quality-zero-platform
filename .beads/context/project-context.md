---
updated: 2026-04-09
source: session-handoff
---

# Project Context — Quality Rollup v2 Refactor

Brainstorm complete. Design committed. Awaiting design-review-gate in a fresh session inside this repo's cwd.

## What's done

- Deep repo analysis — 41 Python modules in `scripts/quality/`, 47 test files, 16 workflows, 9 stacks + 15 repo profiles
- Gap diagnosis:
  - `build_quality_rollup.py` produces useless 3-column table (`str(findings[0])` for detail)
  - Semgrep runs but no rollup lane; CodeQL runs but no rollup ingest; Chromatic/Applitools no infra
  - `.codacy.yaml` is 5 lines of excludes only
  - Platform repo has no coverage measurement on itself (violates policy)
  - SonarCloud coverage upload missing (no `sonar-scanner`, no `sonar-project.properties`)
  - Codecov gets duplicate uploads from two workflows
- Brainstorm: Q1-Q8 answered, full architectural design committed
- Feature branch `feat/quality-rollup-v2` created off `main`
- Design doc committed at `93ae3f6`

## What's next (in order)

1. Design-review-gate on `docs/plans/2026-04-09-quality-rollup-v2-design.md` (5 parallel reviewers)
2. `superpowers:writing-plans` for **PR 1 only**
3. Plan-review-gate on the PR 1 plan (3 parallel reviewers)
4. Execute PR 1
5. Repeat 2-4 for PR 2
6. Repeat 2-4 for PR 3
7. Final `/self-reflect` after PR 3 merges

## Must follow rules (this repo's CLAUDE.md)

- 100% line+branch coverage — no suppressions, no `--no-verify`
- Design-review-gate MANDATORY after brainstorming
- Plan-review-gate MANDATORY after writing-plans (3 reviewers all PASS)
- Pre-PR `/self-reflect` to capture learnings
- TDD for all new Python modules
- `.beads/` files are the canonical recovery surface — update on phase transitions

## PR 1 work plan

- Plan file: `docs/plans/2026-04-09-quality-rollup-v2-pr1-plan.md`
- Scope: PR 1 only (rollup rewrite + patch generators)
- Coverage scope: `scripts/quality/rollup_v2/` + `config/taxonomy/` (via YAML loader tests)
