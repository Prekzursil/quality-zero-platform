---
status: in-progress
phase: phase-7-pr1-complete-preparing-pr
task: quality-rollup-v2
branch: feat/quality-rollup-v2
design_doc: docs/plans/2026-04-09-quality-rollup-v2-design.md
design_commit: 93ae3f6
design_addendum_a_commit: 1fe3b7e
design_addendum_b_commit: 9b148e9
metaswarm_scaffold_commit: ce8d522
started: 2026-04-09
updated: 2026-04-09
design_review_gate_status: PASSED (3 rounds, 5/5 APPROVED_WITH_CONCERNS, 13 blockers resolved)
plan_review_gate_status: PASSED (3 rounds, 3/3 PASS, 7 blockers resolved across Rounds 1-2)
pr1_plan: docs/plans/2026-04-09-quality-rollup-v2-pr1-plan.md
pr1_plan_commits: a3f367f, 8dd7509, 4524e9d
execution_method: subagent-driven-development
pr1_execution_status: COMPLETE (18/18 phases, 386 rollup_v2 tests, 100% coverage, 0 legacy regressions)
resume_hint: create-pr1-then-writing-plans-for-pr2
---

# Active Plan — Quality Rollup v2 Refactor

> **For /start-task resume:** This plan is mid-flight. Brainstorming phase (Q1-Q8) is complete and the design doc is committed. The next action is running the **5-agent design-review-gate** on the committed design doc, THEN `writing-plans` for **PR 1 only** (not all 3 at once).

## Summary

Refactor `quality-zero-platform` quality rollup and gate reporting system to:

1. Replace the useless 3-column markdown table (currently `str(findings[0])`) with a canonical SARIF-inspired finding schema + multi-view rendered markdown
2. Build a D+E two-tier patch system (deterministic generators + LLM fallback)
3. Enforce 100% line+branch coverage on the platform repo itself (currently unmeasured)
4. Fix SonarCloud coverage upload (currently broken)
5. Harden `.codacy.yaml` from 5-line excludes-only to a real engines-declarative config + per-tool config files
6. Add Semgrep, CodeQL, Chromatic, Applitools as first-class rollup lanes
7. Enable Chromatic + Applitools on `momentstudio` (the one visual repo)

## Locked-in decisions (brainstorm Q1-Q8)

| Q | Decision | One-liner |
|---|---|---|
| Q1 | C | Dual output: canonical JSON + rendered markdown |
| Q2 | D | Multi-view markdown (default: by file; collapsible: by provider, by severity, autofixable) |
| Q3 | E | Hybrid dedup taxonomy: full for security+quality, file+line for style |
| Q4 | D+E | Deterministic patches (~30 categories) + LLM fallback scaffold |
| Q5a | D | Strict 100% line+branch with declared excludes in `pyproject.toml [tool.coverage]` |
| Q5b | C | Reuse `run_coverage_gate.py` + `assert_coverage_100.py` in `scripts/verify` |
| Q5c | A | Fix SonarCloud via `sonar-project.properties` + `SonarSource/sonarqube-scan-action` in scanner matrix |
| Q6 | B | Minimal `.codacy.yaml` + per-tool config files (.pylintrc audit, .bandit, [tool.ruff], .semgrep.yml) |
| Q7 | C | Chromatic + Applitools infra, enable on `momentstudio` only in this PR series |
| Q8 | C | 3 PRs: foundation → self-governance → new provider lanes |

## 3-PR scope slicing

### PR 1 — "Rollup rewrite + patch generators" (current target)
- Canonical finding schema (`qzp-finding/1`)
- Per-provider normalizers for existing 9 lanes
- Dedup + hybrid taxonomy (~40 categories)
- Multi-view rendered markdown
- D+E patch generators (~30 deterministic + LLM scaffold)
- Platform dogfood: rollup invoked on platform's own PRs
- TDD, 100% coverage gate on new modules

### PR 2 — "Self-governance + SonarCloud + Codacy config"
- `pyproject.toml [tool.coverage]`
- `scripts/verify` augmented with coverage measurement + assertion
- `sonar-project.properties` + `SonarSource/sonarqube-scan-action` step
- Codecov dedup (remove from scanner-matrix, keep in codecov-analytics)
- `.codacy.yaml` rewrite (engines block)
- `.pylintrc` audit, `.bandit`, `pyproject.toml [tool.ruff]`, `.semgrep.yml`

### PR 3 — "New provider lanes"
- `check_semgrep_zero.py` + Semgrep rollup lane
- `check_codeql_zero.py` + CodeQL rollup lane (reuse Semgrep SARIF normalizer)
- `reusable-chromatic.yml` + `check_chromatic_zero.py` + rollup lane
- `reusable-applitools.yml` + `check_applitools_zero.py` + rollup lane
- Profile schema: `visual_regression.chromatic` / `visual_regression.applitools`
- `profiles/repos/momentstudio.yml` enables both
- README update (15 repos, new provider list, new rollup format)

## Resume instructions

When running `/start-task` in a fresh session inside this repo:

1. **Context-recovery check detects this file** → ask user "active plan exists from previous session, resume or start fresh?"
2. On resume → run `bd prime --work-type recovery` (if beads CLI is wired up) or just read this file + the design doc
3. Next action: **run design-review-gate** on `docs/plans/2026-04-09-quality-rollup-v2-design.md`
4. After all 5 reviewers approve → invoke `superpowers:writing-plans` for **PR 1 only**
5. After PR 1 plan is drafted → run **plan-review-gate** (3 adversarial reviewers — Feasibility, Completeness, Scope+Alignment). All must PASS.
6. Execute PR 1 via user's chosen method (orchestrated execution or subagent-driven development)
7. After PR 1 merged → repeat steps 4-6 for PR 2 and PR 3

## Do NOT

- Re-brainstorm — decisions Q1-Q8 are locked; changes require explicit user override
- Plan all 3 PRs at once — plan PR 1, execute, then PR 2, then PR 3
- Skip the design-review-gate — this repo's CLAUDE.md mandates it
- Use `--no-verify` on any commit
- Touch `main` directly — stay on `feat/quality-rollup-v2` (this branch) or PR-specific sub-branches off it
- Drop coverage below 100% on any new module (TDD, strict gate)

## Open items for writing-plans phase

- Exact rule-ID → canonical category mapping for Codacy/Sonar/DeepSource/Semgrep (research, ~2h total)
- `ruff` vs `pylint` primacy (leaning ruff, pylint secondary)
- `.bandit` as file vs `pyproject.toml [tool.bandit]` (reliability varies by version)
- Provider priority for `pick_primary_by_provider_priority()` — proposal: SonarCloud > Codacy > DeepSource > Semgrep > CodeQL > QLTY > DeepScan
- Whether PR 1 pre-reserves lane keys for Semgrep/CodeQL/Chromatic/Applitools so PR 3 is purely additive

## Linked files

- Design doc: `docs/plans/2026-04-09-quality-rollup-v2-design.md` (committed at `93ae3f6`)
- Project memory: `~/.claude/projects/C--Users-Prekzursil-Documents-GitHub-quality-zero-platform/memory/qrv2_resume.md`
- Parent inventory: `inventory/repos.yml` (15 repos; `momentstudio` is the visual one)
- Existing rollup code to rewrite: `scripts/quality/build_quality_rollup.py`
- Existing coverage scripts to reuse: `scripts/quality/run_coverage_gate.py`, `scripts/quality/assert_coverage_100.py`
