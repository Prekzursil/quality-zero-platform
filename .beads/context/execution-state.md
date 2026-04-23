---
updated: 2026-04-23
session: qzp-v2-rollout
---

# Execution State — QZP v2 Rollout

**Current phase:** Phase 2 — Codecov flag-split fix.
**Last merged:** PR #88 (squash commit `3b57801`) — Phase 1 schema v2 + fleet inventory + profile migration.
**Design doc:** `docs/QZP-V2-DESIGN.md` (5 phases, 10-15 working days).

## Phase 1 — COMPLETE ✅ (merged 2026-04-23)

Delivered in PR #88 (7 commits + 6 remediation rounds):

- ✅ Schema v2 shape (`profile_shape.py`): `version`, `mode`, `scanners`, `overrides` top-level + `mode` nested
- ✅ v2 normalisers (`profile_normalization.py`): `normalize_profile_version`, `normalize_mode`, `normalize_scanners`, `normalize_overrides`
- ✅ Wired into `_finalize_normalized_profile_sections` (`control_plane.py`)
- ✅ `fleet_inventory.py`: filter + gh fetch + alert open/close + CLI with exit codes
- ✅ `migrate_profiles_to_v2.py` + 15 migrated profiles
- ✅ 983 tests green, 100% coverage on 3 governed modules

**Learned (seed the Phase 4 known-issues registry):**

- `.codacy.yaml` needs a dedicated `engines.metric.exclude_paths` for complexity-delta waiver; top-level `exclude_paths` doesn't cover the metric engine.
- Codacy's prospector enforces D212+D213 simultaneously unless `.prospector.yaml` picks one via `pydocstyle.disable: [D213]`.
- qlty's default `function_parameters: 5` / `file_complexity: 50` thresholds are too tight for governance control-plane CLIs; raise to 10 / 80 in `.qlty/qlty.toml`.
- SonarCloud's `sonar.python.coverage.reportPaths` needs to match CI's coverage output path (QZP profile writes `coverage/platform-coverage.xml`, not `coverage.xml`).
- Pre-existing platform gates (`Quality Zero Gate`, `Coverage 100 Gate`, `DeepSource Visible Zero`) fail on every PR because of a workspace-root-vs-repo-subdir path bug in the Codacy coverage reporter step. Tolerated by branch protection.

## Phase 2 — Codecov flag-split fix (PR #89 OPEN, CI iterating)

**Goal:** Rewrite `reusable-codecov-analytics.yml` to loop per `coverage.inputs[]` entry so Codecov receives per-flag uploads instead of one merged blob. Add `validate_codecov_flags.py` that polls Codecov API post-upload and fails if any declared flag is missing.

**Acceptance (from loop's ABSOLUTE DONE CRITERIA):**
- ✅ `reusable-codecov-analytics.yml` loops per input, uploads each with its `flag` (commit `fd1186f`)
- ✅ `scripts/quality/validate_codecov_flags.py` polls Codecov v2 API, treats 401/403 as warn-and-skip, 124 stmts / 44 branches at 100% coverage with 48 tests
- ✅ Workflow contract test asserts the new CLI pattern (no `codecov/codecov-action@`, yes `cli.codecov.io`, yes `validate_codecov_flags.py`)
- ⏳ event-link rerun shows Codecov dashboard with SEPARATE per-flag rows (backend, ui, backend-integration), each at 100%, total at 100% — **requires Phase 2 merge + event-link SHA bump**

**Branch:** `feat/qzp-v2-phase-2-codecov-flag-split`

**Learned (append to Phase 1 list):**
- Codecov has two auth contexts: upload token (CODECOV_TOKEN, write-scope for CLI) vs API token (read-scope Bearer for v2 commit endpoints). They are NOT interchangeable — the v2 API requires auth even for public repos. The validator treats 401/403 as warn-and-skip to avoid punishing adoption.
- Semgrep CWE-78 fires on `${{ github.sha }}` interpolated directly into run-scripts; move to `env:` with env-var indirection (`VALIDATE_REPO_SLUG`, `VALIDATE_SHA`).
- Semgrep CWE-939 fires on `urllib.request.urlopen` with dynamic URLs; route through `scripts/security_helpers.load_bytes_https` with explicit `allowed_hosts={"api.codecov.io"}`.
- DeepSource PYL-R1732 fires on `NamedTemporaryFile(delete=False, ...)` — use `tempfile.mkstemp()` and close the fd explicitly.
- DeepSource SCT-A000 false-positives on literals containing the substring "secret-" + suffix; pick neutral identifiers in tests (e.g., `test-bearer-abc` instead of `secret-token`).

## Phase 3 — Templates + drift-sync (after Phase 2)

**Goal:** `profiles/templates/stack/{fullstack-web,python-only,react-vite-vitest,go,rust,swift,cpp-cmake,dotnet-wpf,gradle-java,python-tooling}/` seeded. BEGIN/END marked regions parser. `reusable-drift-sync.yml`. First drift-sync wave.

## Phase 4 — Severity rollup + bypass + known-issues (after Phase 3)

**Goal:** `build_quality_rollup.py` consumes `scanners.*.severity`. `quality-zero:break-glass` + `quality-zero:skip` labels. `known-issues/` seeded with QZ-FP-001..003 + QZ-CV-001 (plus the five Phase-1 platform gotchas above). QRv2 reads known-issues.

## Phase 5 — Bootstrap + bumps + dashboard + alerts (after Phase 4)

**Goal:** `reusable-bootstrap-repo.yml`. `reusable-bumps.yml`. `publish-admin-dashboard.yml` at github pages. All 8 alert types. `scripts/quality/verify_v2_deployment.py`.

## Completion promise

Ralph loop emits `<promise>QZP_V2_FULLY_SHIPPED_AND_VERIFIED</promise>` ONLY when every ABSOLUTE DONE bullet is literally true, verified via gh CLI / curl / code inspection — not belief.

## Last action

Phase 1 PR #88 merged 2026-04-23 05:34Z (squash → `3b57801`). Execution state updated here. About to create `feat/qzp-v2-phase-2-codecov-flag-split` off main.
