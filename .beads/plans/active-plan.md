---
status: in-progress
phase: design-review-round-2 (round-1 = 5/5 BLOCK -> PASS_WITH_REQUIRED_FIXES; 9 blockers resolved in Addendum A)
task: truthful-gate-subsystem
branch: feat/truthful-gate-subsystem
base: origin/main @ c0a5437
design_doc: docs/plans/2026-06-01-truthful-gate-subsystem-design.md (+ Addendum A)
design_review_round1: 5/5 BLOCK, 9 consolidated blockers (CB-1..CB-9), all addressed in Addendum A
started: 2026-06-01
updated: 2026-06-01
supersedes_active_plan: quality-rollup-v2 (qrv2 PRs already landed; that plan was stale)
related_open_pr: 232 (BLOCKED — truthful gate correctly red on platform legacy debt)
related_merged: 221, 222, 223, 224, 225 (strict-zero push)
resume_hint: confirm-scope-then-run-design-review-gate
---

# Active Plan — Truthful-Gate Subsystem

> **For resume:** The "as discussed" plan from the 2026-06-01 session
> (lost on restart; recovered from `.remember/today-2026-06-01.md`) is the
> **truthful-gate subsystem**: dashboard-truth scanner → QZP profiles +
> zero-conf onboarding. The detailed design was reconstructed into
> `docs/plans/2026-06-01-truthful-gate-subsystem-design.md` and committed.

## Where we are

1. Orientation complete. Ground facts verified (PRs #221/#222 MERGED;
   tokens LIVE incl. DEEPSOURCE_DSN refreshed 2026-05-31; per-provider
   `check_*_zero.py` already exist; #232 OPEN+BLOCKED because the truthful
   gate is correctly red on the platform's 838 Codacy + 1116 DeepSource +
   110 Sonar legacy findings).
2. Design reconstructed + committed.
3. **NEXT:** user confirms scope + 5 OPEN DECISIONS (A–E in the design
   doc §8). Then run the mandatory **design-review-gate** (5 agents).

## Mandated pipeline (this repo's CLAUDE.md)

design-review-gate (5 agents, all approve) → writing-plans (PR-sliced) →
plan-review-gate (3 adversarial, all PASS) → ask execution method →
execute (TDD, 100% cov) → /self-reflect → PR.

## OPEN DECISIONS (block finalization)

- **A** legacy debt: A1 truthful frozen baseline + burn-down (recommended)
  vs A2 drive platform to literal zero now.
- **B** profile shape: B1 thin + stack inheritance + migrate 15
  (recommended) vs B2 generate-only-for-new-repos.
- **C** fleet scope: ship subsystem only vs also drive 15 live repos green
  now (needs DRIFT_SYNC_PAT).
- **D** #232: extend (recommended) vs supersede.
- **E** execution method.

## Do NOT

- Re-brainstorm the target — intent is locked (truthful gates + zero-conf
  onboarding). Forks A–E are the only open questions.
- Skip design-review-gate / plan-review-gate (CLAUDE.md mandates both).
- Use `--no-verify`; touch `main`; drop coverage below 100% on new modules.
- Disturb the 8 git stashes (7 are "prevent loss" from prior sessions).

## Preserved state

- Branch `fix/coverage-uploads-strict-zero-2026-04-29` retains 3 commits
  (2 possibly unmerged: c99cfa5, 39099fa) — triage into TG-1.
- Stash count: 8 (incl. `qzp-generated-rulesets-local-2026-06-01`).
