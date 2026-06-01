---
status: in-progress
phase: design-review-PASSED — writing-plans for TG-2 (token preflight)
task: truthful-gate-subsystem
branch: feat/truthful-gate-subsystem
base: origin/main @ c0a5437
design_doc: docs/plans/2026-06-01-truthful-gate-subsystem-design.md (+ Addenda A,B,C,D)
design_review_gate: PASSED (4 rounds; R1 9 blockers→A, R2 2 HIGH→B, R3 2 HIGH→C, R4 PASS)
execution_method: metaswarm orchestrated (user decision E)
started: 2026-06-01
updated: 2026-06-01
resume_hint: writing-plans-TG-2-then-plan-review-gate-then-orchestrated-execution
---

# Active Plan — Truthful-Gate Subsystem

The "as discussed" plan (recovered from `.remember/today-2026-06-01.md` after
a restart): **truthful-gate subsystem** = dashboard-truth scanner → QZP
profiles + zero-conf onboarding. Design reconstructed, user-confirmed, and
**design-review-gate PASSED after 4 rounds** (all findings code-verified).

## Locked program (design §8/§9 + Addenda A–D)

Three tracks, sequenced so each stands on a non-regressing foundation:

- **Track 1 SUBSYSTEM (TG-1..TG-6):** Truth Source contract (verdict enum
  clean|dirty|unreadable, BOTH count+baseline axes fail-closed → silent-pass
  unrepresentable), token preflight, baseline (deletes `issue_policy.mode:
audit`), reconciliation, thin profiles, `qzp onboard`.
- **Track 2 PLATFORM LITERAL-ZERO (A2):** burn down ~2064 platform findings
  to 0 by escalation_date 2026-09-30; #232 merges at baseline-hold first.
- **Track 3 FLEET-GREEN (C):** drive all 15 repos green via the subsystem.

## PR sequence (gate-approved)

TG-2 (token preflight + alert:scanner-unavailable + cron) → TG-1 (Truth
Source contract + adapter refactor; auth path behavior-CHANGING; fold #232
provider_enforcement) → **TG-3 ATOMIC** (delete issue_policy.mode audit +
truth/verdict.py [count&baseline fail-closed] + truth/baseline.py +
security_path_guard.py + rebase #232 to baseline-hold) → TG-4 (reconcile
SHA-settle + alert:gate-truth-drift + dashboard grey + exit-2) → TG-5
(scanner→context map + golden-identical migration, B1/B2 fallback) → TG-6
(qzp onboard + python -m invocation + deterministic detection + doc lockstep

- inventory exclude:) → TG-7 stretch (kotlin-multiplatform → bilbo-app).

## NEXT ACTION

`writing-plans` for **TG-2** → **plan-review-gate** (3 adversarial:
Feasibility, Completeness, Scope+Alignment, all PASS) → metaswarm
orchestrated execution (IMPLEMENT→VALIDATE→ADVERSARIAL→COMMIT). Then repeat
per TG.

## Carried MEDIUM notes (Addendum D — resolve in named TG)

- [TG-3] reconcile `list_templates` output paths with `SECURITY_RELEVANT_PATHS`
  globs; test against actual emitted paths.
- [TG-3] document why `deadline` axis is intentionally not fail-closed
  (monotonically stricter).
- [TG-3] land baseline DATA files atomically with verdict/loader code.

## USER sign-off pending (3, reversible — proceeding unless vetoed)

1. M1 "platform green" = baseline-hold green (not literal-zero green).
2. #232 merges at baseline-hold; A2 literal-zero = M2 target by 2026-09-30.
3. Onboarding = "one command + deterministic detection", NOT ≤8-line profiles
   for all (4-5 complex profiles stay 30-50 lines; scanner→context map is
   hand-maintained).

## Guardrails (CLAUDE.md)

TDD, 100% line+branch on new modules; `.coverage-thresholds.json` is truth;
lizard -C 15; new scripts → `sonar.coverage.exclusions`; no `--no-verify`;
never touch main; never silent-pass; 8 git stashes are "prevent loss" — keep.
