# Unfinished / Abandoned / Duplicate Workstreams — Consolidated Index

- **Date:** 27–28.08.2026 (reconciliation executed 28.08.2026)
- **Source audit:** `git-hygiene-audit_2026-08-27.md` (Cowork/output)
- **Branch:** `ideas/unfinished-consolidated` (living branch off `main`; NOT for merge)
- **Recovery guarantees:** every branch below is captured in the full-repo bundle
  `Cowork/backups/qzp-lean-2026-08-28.bundle` AND anchored server-side by a
  `backup/<branchname>` tag pushed to origin. Nothing listed here can be lost by
  branch deletion.

## How to read this index

One entry per **idea**, not per branch — parallel takes on the same idea are folded
into a single entry with all their branches listed. Status vocabulary:

- **RESOLVED-ON-MAIN** — content verified present on main; branch is historical only.
- **SUPERSEDED** — a later merged PR covers the theme; revival not expected.
- **STILL-WANTED?** — owner decision pending; design/docs preserved here.
- **EVOLVED-ELSEWHERE** — the idea continued under a different merged line.

---

## 1. `scripts/verify` GIT_* hook isolation — RESOLVED-ON-MAIN

- **Branches:** `fix/verify-git-hook-isolation` (PR #242 CLOSED) + exact duplicate
  `fix/qzp-gate-plumbing-v2` (PR #243 CLOSED) — one idea, two branches.
- **Unique commits:** 1 each (`cc00fec`, same patch subject on both).
- **What it was:** +3 lines unsetting `GIT_DIR GIT_WORK_TREE …` in `scripts/verify`
  so fixture tests can't mutate the real repo when run inside a pre-push hook.
- **Verdict (2026-08-28):** the identical fix is **already on main** — landed via
  PR #245 (`f7ba5d5`, "fix(verify-v2): unblock Coverage 100 Gate"). The audit's
  `git cherry +` was a patch-id mismatch (different surrounding context), not
  missing content. A fresh cherry-pick onto main came out **empty**. No action needed.
- **Backup tags:** `backup/fix/verify-git-hook-isolation`, `backup/fix/qzp-gate-plumbing-v2`.

## 2. Truthful-gate subsystem — STILL-WANTED? (largest unfinished idea)

- **Branch:** `feat/truthful-gate-subsystem` (PR #236 CLOSED, June 2026).
- **Unique commits:** 18 (11 files, +2488/−165).
- **What it was:** gate-approved design for a "truthful gate" subsystem plus the
  TG-2 token-rotation preflight implementation (`scripts/quality/truth/preflight.py`
  + tests).
- **Design docs preserved on THIS branch:**
  - `docs/plans/2026-06-01-truthful-gate-subsystem-design.md`
  - `docs/plans/2026-06-01-truthful-gate-tg2-token-preflight-plan.md`
- **Note:** branch is 35 commits behind main; code should be re-implemented against
  today's lean gate from the design docs, not merged. Owner call: revive as a fresh
  plan, or close the idea (docs stay here either way).
- **Backup tag:** `backup/feat/truthful-gate-subsystem`.

## 3. Autonomous remediation-engine cluster (June 2026) — STILL-WANTED? (one idea, five branches)

All five PRs closed 08–11.06.2026 in the post-lean-gate pivot; treated as ONE
workstream. If revived, the audit's recommended order is:
check-blocked-paths → security-class-guard → runner-guards → ratchet-coverage
(each rebased/reimplemented as a fresh PR against today's lean gate).

| Branch | PR | Unique commits | Scope | What it was |
|---|---|---|---|---|
| `fix/remediation-runner-guards` | #246 | 5 | 8 files, +1404/−54 | remediation lane rebuilt with hard safety guards (charter §5), incl. ReDoS fix |
| `feat/check-blocked-paths` | #241 | 8 | 3 files, +443 | SSOT blocked-paths guard for the autofix engine |
| `test/ratchet-gate-100-coverage` | #240 | 7 | 5 files, +2318 | 100% line+branch coverage for ratchet gate + coverage-source wiring |
| `fix/security-class-guard-canonical` | #239 | 2 | 2 files, +250/−61 | security-class guard recognises canonical rollup_v2 findings |
| `fix/qzp-autofix-wave1` | #244 | 5 | 55 files, +129/−136 | mechanical SAFE ruff autofix wave — **cheaper to regenerate than revive** |

- **Backup tags:** `backup/fix/remediation-runner-guards`, `backup/feat/check-blocked-paths`,
  `backup/test/ratchet-gate-100-coverage`, `backup/fix/security-class-guard-canonical`,
  `backup/fix/qzp-autofix-wave1`.

## 4. Strict-zero whole-tree gate hardening — SUPERSEDED (likely)

- **Branch:** `claude/trusting-goodall-Uva23` (PR #232 CLOSED).
- **Unique commits:** 8 (50 files, +2575/−1438).
- **What it was:** strict-zero whole-tree enforcement on push AND PR gate hardening.
- **Superseded-by evidence:** the lean-gate rebuild (#252–#263) replaced this gate
  architecture. Owner confirmation pending on whether the whole-tree-on-push
  *semantics* are still wanted as a separate idea.
- **Backup tag:** `backup/claude/trusting-goodall-Uva23`.

## 5. PR #290 — complexity + duplication gate modules — GREEN-BUT-UNWIRED (kept open)

- **Branch:** `feat/qzp-complexity-duplication-gates` (PR #290 OPEN since 12.08).
- **Scope:** 11 files, +1954/−0 — T1 new-code-only decision modules, all checks pass,
  but **not wired into any lane** (inert if merged).
- **Reconciliation decision (2026-08-28, per no-half-finished-to-main rule):**
  NOT merged. The PR stays open as the carrier of the work; the idea is recorded
  here. To land it: write the wiring plan (which lane invokes the modules, what
  thresholds, ratchet-vs-block mode), wire + test in the same PR, then merge.
- **Backup tag:** `backup/feat/qzp-complexity-duplication-gates`.

## 6. Superseded March/April 2026 lines — SUPERSEDED (8 branches, de-duplicated to 6 ideas)

Kept only under the never-delete-unbacked rule; all content is bundle+tag anchored.
None of these should be revived as-is — each theme already landed via a merged PR.

| Idea (deduplicated) | Branch(es) | PR | Unique commits | Superseded by |
|---|---|---|---|---|
| April zero-mode self-governance | `fix/qzp-platform-self-governance-zero-mode` | #164 CLOSED | 47 | #113 (MERGED) covers the theme |
| Env-zero gate hardening | `codex/env-zero-gate-hardening` | #55 CLOSED | 1 | lean-gate rebuild #252–#263 |
| DeepSource visible-PR enforcement | `codex/fix/deepsource-visible-pr-enforcement` | #63 CLOSED | 19 | #61 (MERGED) |
| Main-zero followup | `codex/main-zero-followup` | #72 CLOSED | 10 | #70/#71 (MERGED) |
| Sync event-link verify contract | `codex/sync-event-link-verify-contract` | #53 CLOSED | 5 | #54 `…-main` variant (MERGED) |
| Strict-zero airline/vendor CSM (two parallel takes, one idea) | `codex/pr-scoped-vendor-zero-csm` (no PR) + `codex/strict-zero-airline-csm` (no PR) | — | 6 + 4 | #74 `codex/strict-zero-airline-csm-v2` (MERGED) |
| optibot bundler bump | `optibot/bundler/1780095284702` | #235 CLOSED | 3 | trivial (1 file, +1/−1); content dead |

- **Backup tags:** `backup/<branchname>` for every branch in the table.

## 7. "Cursor CLI bridges" docs — IDEA-ONLY (no commits ever existed)

- **Branch:** local `docs/cursor-cli-bridges` — pointer identical to main, zero
  unique commits. The idea (document the Cursor CLI bridge patterns) exists only
  as this branch name and is now recorded here; the placeholder branch is deleted.
- **Backup tag:** `backup/docs/cursor-cli-bridges` (points at the main tip it sat on).

---

## Not in scope of this index

- `archive/pre-hygiene-20260617`, `archive/qzp-full-saas-control-plane` — deliberate
  archives, ancestry-merged, kept as-is.
- All merged-PR remote branches (§2.3-A of the audit) — content on main; remote
  pruning deferred to a future owner-approved pass.
