# Fleet Autonomy Decision Log (2026-09-04)

SSOT for grill locks G-1…G-27, owner-ops posture, WorkerAdapter, circuit breakers, and G-5 feature rungs. Companion: [`2026-09-04-coverage-orchestration-contract.md`](./2026-09-04-coverage-orchestration-contract.md). Momentstudio owns coverage/e2e/loop lane docs. AST holds a pointer only.

**Phase:** docs/commands only. No fleet-loop code, Continuous-Claude merge automation, enroll scripts, or default-branch automation until **G-20 is flipped to build** with an explicit follow-up.

## Owner-ops posture

- Estate-local work on `Prekzursil/*` is pre-authorized at full intensity.
- Security-adjacent coverage/CI/Playwright/visual work is not a stall reason.
- Hard bans remain: third-party attack, stealer/C2/ransomware, force-push main without ask, commit live secrets, `--no-verify` without ask, irreversible wipe without ask, CSAM, clear crime-vs-others.
- Redact secrets in logs, PRs, receipts, NOTES, digests, DraftPR bodies (presence-only / `[REDACTED]`).
- Automation is draft/propose-only (G-3). Owner-ops does not override human-merge policy.

Stance line: `OWNER-OPS: estate-local Prekzursil; redact secrets in receipts/NOTES/PRs/digests; draft PRs only; hard bans H1–H9.`

## Authoritative decisions (G-1 … G-27)

| ID | Decision |
| --- | --- |
| G-1 | Ambition A+B+C phased; human merge |
| G-2 | QZP+GitHub primary; Cursor Cloud secondary; local Ralph / detached multi-model tertiary |
| G-3 | Human-approved merge only; draft/propose-only |
| G-4 | QZP-enrolled now; enroll rest of workspace over time |
| G-5 | Feature ladder: queue → draft issues → draft PRs; never silent main |
| G-6 | Never-touch A+C: **hard denylist stop** (global + per-repo AGENTS.md); not a soft label |
| G-7 | Compose surfaces; keep `mcp-fleet-curation` local; `codex-fleet-github` one adapter; new health skill id **`repo-fleet-health`** (never `mcp-fleet-*`); `/source` intake |
| G-8 | First build lands in QZP + AST + Cursor Cloud |
| G-9 | Health-first; parallel if file-disjoint; features if health success/blocked |
| G-10 | Clarify or best-effort draft by case (mechanical/health only; not feature-ladder bypass) |
| G-11 | Triggers: failing checks, drift, `agent:ready`, manual |
| G-12 | Unbounded concurrency aspiration + **circuit breakers required** |
| G-13 | Retry until green + exhaust → `blocked`/escalate |
| G-14 | Inner + outer verify; independent verifier; no self-graded done |
| G-15 | Prefer debt-with-rescue when it unblocks checks; else separate |
| G-16 | `/source` = full agent-sort / ECC-style |
| G-17 | `mcp-fleet-curation` = operator/local hygiene only |
| G-18 | Pilot A+B+C **phased/sequential** (never parallel on first build); worker-agnostic |
| G-19 | Fail closed per worker |
| G-20 | This phase: decision log + sculpted coverage/e2e/loop/orchestration **docs**; no fleet-loop code until flipped |
| G-21 | **LOCKED B:** QZP owns fleet+orchestration SSOT; momentstudio owns coverage/e2e/loop lane docs; AST pointer-only |
| G-22 | **LOCKED A:** first golden WU = `about` or `contact` (checkout forbidden as golden) |
| G-23 | **LOCKED A:** de-sloppify is a mandatory separate stage |
| G-24 | **LOCKED A:** missing visual secrets → `outer:blocked`; draft OK only after INNER ∧ LANE_OUTER ∧ REPO_VERIFY; **not** INNER-only draft; silent skip FORBIDDEN |
| G-25 | **LOCKED A:** sequential pipeline + NOTES first; Continuous-Claude-shaped CI poll later still **no merge**; merge-queue FORBIDDEN |
| G-26 | **LOCKED A:** document change-spec now; single-agent OK until G-20 flip |
| G-27 | **LOCKED A:** escalation N=3 |

### Ambition A / B / C (G-1)

| Ambition | Scope | DoD | Kill switch |
| --- | --- | --- | --- |
| **A** Health/PR rescue | QZP audit → classify → draft repair PRs | Re-audit `success\|partial\|blocked`; human merge | Stop if never-touch hit or auth missing for all workers |
| **B** Feature factory | G-5 ladder only | Draft issue/PR for ticket scope; verify green or blocked | Stop on product ambiguity requiring G-10 clarify |
| **C** Product-owner behavior | Prioritize backlog via packets; still human merge | Packets filed with evidence; no silent main | Never grants merge authority |

**Post–G-20 build sequence (never A+B+C in parallel on first build):** (1) momentstudio single-WU coverage pilot → (2) health loop → (3) feature ladder.

### G-5 feature rungs

1. Queued ticket  
2. Draft issue (ideation)  
3. Draft feature PR  

Forbid queue→main and queue→ready-for-merge PR. G-10 best-effort draft is scoped to non-feature (mechanical/health) lanes only.

### G-6 hard denylist

Payment provider config, live secrets, auth credential invention, checkout/payment **product** behavior changes, migrations, release/git-crypt. Default = do not enqueue. `risk:high` never unlocks G-6. Checkout/payment are never golden WUs (G-22A).

## WorkerAdapter (document now; implement post–G-20)

```yaml
WorkerAdapter:
  id: codex | cursor_cloud | claude
  auth_probe: -> available | unavailable  # fail-closed; no silent cross-sub
  dispatch: (change_spec, branch_policy) -> receipt
  branch_prefix_map:
    codex: "codex/fix/"
    cursor_cloud: "cursor/"
    claude: "claude/fix/"
  verify_hooks: [INNER, LANE_OUTER, REPO_VERIFY]
  invariants: [draft_only, no_merge, redact_secrets, never_touch_hard_stop]
```

Missing/stale worker auth → that worker `unavailable`; requeue or block; never silent-fallback without receipt.

## Circuit breakers (required before any post–G-20 scale)

| Breaker | Cap |
| --- | --- |
| Max concurrent coding agents / repo | 4 |
| Max concurrent council/review agents | 10 (platform async cap) |
| Max runs / coverage batch | 20 |
| Max cost / batch | **HARD:** `budget.total` must be set before coding fan-out; unset → refuse. Halt when `spent() >= budget.total` OR `remaining() < floor` (document floor e.g. 50_000) |
| Identical-fail escalate | N=3 (G-27) |
| Max draft-PR revisions / WU | 3 |
| Chromatic/Applitools | fail-closed if secret missing → `outer:blocked` |
| Docs-phase fan-out | 0 coding agents; council review waves ≤10 async |

## Coverage lane verify layers (pointer)

Canonical detail lives in momentstudio docs. Summary:

- **INNER:** `npm run test:coverage` (Karma) + `diff-coverage.mjs` on PR-added executable `frontend/src` lines with `GITHUB_BASE_REF` set (skip-log = fail). Not global istanbul 100%.
- **LANE_OUTER:** Case A secrets present → green; Case B `visual_pair_required` and secrets missing → mandatory `outer:blocked` (still run non-visual specs).
- **REPO_VERIFY:** `make verify` always for done.
- **Done:** INNER ∧ LANE_OUTER ∧ REPO_VERIFY ∧ independent Review. Stages: `Ground → Select → Impl → DeSlop → ValidateInner → LaneOuter → RepoVerify → Review → DraftPR`.

## Out of scope until G-20 flip

Remediation loop code, `~/.claude/workflows/*.mjs`, Continuous-Claude merge, Ralphinho merge queue, fleet agent-sort install, enroll scripts, auto-merge allowlist, service-command rewrite.
