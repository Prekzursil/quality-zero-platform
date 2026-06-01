---
status: in-progress
phase: TG-2 SHIPPED (PR #236 open) — next is TG-1
task: truthful-gate-subsystem
branch: feat/truthful-gate-subsystem
base: origin/main @ c0a5437
pr: 236 (feat/truthful-gate-subsystem -> main; design + TG-2 token preflight)
design_doc: docs/plans/2026-06-01-truthful-gate-subsystem-design.md (+ Addenda A,B,C,D)
design_review_gate: PASSED (4 rounds; R1 9 blockers→A, R2 2 HIGH→B, R3 2 HIGH→C, R4 PASS)
tg2_plan: docs/plans/2026-06-01-truthful-gate-tg2-token-preflight-plan.md (rev 2)
tg2_plan_review_gate: PASSED (1 round, 5 must-fixes folded into rev 2)
tg2_build: dc47182 (impl) + 2ae55a7 (fix: deepscan->EXEMPT); 1531 tests, 100% line+branch on truth/preflight.py, verify exit 0
execution_method: metaswarm orchestrated (user decision E)
started: 2026-06-01
updated: 2026-06-01
resume_hint: monitor-PR-236-CI-then-merge-then-writing-plans-TG-1
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

1. **Monitor PR #236 CI** (touches `scheduled-alerts.yml` → human review, no
   auto-merge). On green, merge → `origin/main` advances.
2. **TG-1** (Truth Source contract + adapter refactor; auth path
   behavior-CHANGING per A.CB-5; fold #232 `provider_enforcement.py`):
   `writing-plans` → 1-round plan-review-gate → orchestrated build → PR.
3. Repeat the per-TG cadence (one TG green/merged before the next):
   TG-3 (atomic) → TG-4 → TG-5 → TG-6 → TG-7.

**Cadence rule (advisor):** one round of plan-review per TG, fix genuine
build-breakers once, then build. Measure progress in merged PRs, not gates.
The per-unit adversarial review in the orchestrated build is the real
defect-catcher — do not iterate-to-clean on documents.

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
