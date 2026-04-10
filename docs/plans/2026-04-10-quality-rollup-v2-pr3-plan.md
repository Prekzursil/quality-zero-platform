# Quality Rollup v2 — PR 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Add Semgrep, CodeQL, Chromatic, and Applitools as first-class rollup lanes with normalizers, check scripts, reusable workflows, and profile-based opt-in. Enable Chromatic + Applitools on `momentstudio`.

**Architecture:** 4 new lanes: Semgrep + CodeQL share a SARIF normalizer base (`_sarif.py`); Chromatic + Applitools use JSON API normalizers. Each lane gets a check script, a normalizer, lane registration, and (for visual lanes) a reusable workflow + profile schema.

**Tech Stack:** Python 3.12, SARIF 2.1.0 parsing, GitHub Actions YAML, profile YAML schemas.

**Source of truth:** Design doc §§9.1-9.5, §10 PR 3, Addendum A §A.2.4/§A.2.5/§A.5, Addendum B §B.2.2/§B.3.6/§B.3.17.

---

## Task 1: Shared SARIF normalizer base (`_sarif.py`) with 50MB guard

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/_sarif.py`
- Create: `tests/quality/rollup_v2/test_sarif_common.py`

- [ ] **Step 1: Write failing tests** — parse a minimal SARIF 2.1.0 fixture, assert Finding fields; test 50MB guard raises; test malformed SARIF caught by BaseNormalizer error boundary
- [ ] **Step 2: Implement** `parse_sarif(data: dict, provider: str, repo_root: Path) -> list[Finding]` + `MAX_SARIF_BYTES = 50 * 1024 * 1024` guard + `SarifTooLargeError`
- [ ] **Step 3: Run → pass**
- [ ] **Step 4: Commit** `feat(qrv2-pr3): shared SARIF normalizer base with 50MB guard (§9.1 §9.2 §A.2.5)`

## Task 2: Semgrep normalizer + check script

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/semgrep.py`
- Create: `scripts/quality/check_semgrep_zero.py`
- Create: `tests/quality/rollup_v2/test_normalizer_semgrep.py`
- Create: `tests/quality/rollup_v2/fixtures/normalizers/semgrep_sample.sarif.json`

- [ ] **Step 1: Create SARIF fixture** (minimal Semgrep SARIF output with 2 results)
- [ ] **Step 2: Write failing tests** — parses 2 findings, maps categories via taxonomy, severity from SARIF `level`
- [ ] **Step 3: Implement** `SemgrepNormalizer(BaseNormalizer)` calling `_sarif.parse_sarif()`
- [ ] **Step 4: Implement** `check_semgrep_zero.py` — reads SARIF, asserts zero findings (gate script)
- [ ] **Step 5: Run → pass**
- [ ] **Step 6: Commit** `feat(qrv2-pr3): Semgrep normalizer + check_semgrep_zero.py (§9.1)`

## Task 3: CodeQL normalizer + check script

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/codeql.py`
- Create: `scripts/quality/check_codeql_zero.py`
- Create: `tests/quality/rollup_v2/test_normalizer_codeql.py`
- Create: `tests/quality/rollup_v2/fixtures/normalizers/codeql_sample.sarif.json`

- [ ] **Step 1: Create SARIF fixture** (CodeQL SARIF with `threadFlows` for taint trace)
- [ ] **Step 2: Write failing tests** — parses findings, handles CodeQL-specific `properties` bag
- [ ] **Step 3: Implement** `CodeQLNormalizer(BaseNormalizer)` calling `_sarif.parse_sarif()` with CodeQL-specific overrides
- [ ] **Step 4: Implement** `check_codeql_zero.py`
- [ ] **Step 5: Run → pass**
- [ ] **Step 6: Commit** `feat(qrv2-pr3): CodeQL normalizer + check_codeql_zero.py (§9.2)`

## Task 4: Register Semgrep + CodeQL in pipeline lane registry

**Files:**
- Modify: `scripts/quality/rollup_v2/patches/__init__.py` (if lane registry is here) or `pipeline.py`

- [ ] **Step 1: Register normalizers** in the pipeline's normalizer dispatch (replace the "not-configured" placeholders from PR 1 Phase 15 with actual normalizer instances)
- [ ] **Step 2: Update `LANE_ARTIFACT_PATHS`** to point at real SARIF artifact paths
- [ ] **Step 3: Test** — run pipeline with Semgrep + CodeQL fixtures → findings appear
- [ ] **Step 4: Commit** `feat(qrv2-pr3): register Semgrep + CodeQL lanes in pipeline (§9.1 §9.2)`

## Task 5: Chromatic normalizer + check script + reusable workflow

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/chromatic.py`
- Create: `scripts/quality/check_chromatic_zero.py`
- Create: `.github/workflows/reusable-chromatic.yml`
- Create: `tests/quality/rollup_v2/test_normalizer_chromatic.py`
- Create: `tests/quality/rollup_v2/fixtures/normalizers/chromatic_sample.json`

- [ ] **Step 1: Create fixture** (Chromatic API response shape: `{builds: [{changeCount, errCount, ...}]}`)
- [ ] **Step 2: Write failing tests** — parses visual diffs as findings, severity from error/change counts
- [ ] **Step 3: Implement** `ChromaticNormalizer(BaseNormalizer)` — Chromatic JSON → canonical findings
- [ ] **Step 4: Implement** `check_chromatic_zero.py` — API check: `accepted == total AND errored == 0`
- [ ] **Step 5: Create** `reusable-chromatic.yml` — calls `chromaui/action` (SHA-pinned per §A.2.4) with profile-resolved inputs. Include `validate_workflow_paths.py` step for `storybook_dir` validation (§B.2.2)
- [ ] **Step 6: Run → pass**
- [ ] **Step 7: Commit** `feat(qrv2-pr3): Chromatic lane — normalizer + check + reusable workflow (§9.3)`

## Task 6: Applitools normalizer + check script + reusable workflow

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/applitools.py`
- Create: `scripts/quality/check_applitools_zero.py`
- Create: `.github/workflows/reusable-applitools.yml`
- Create: `tests/quality/rollup_v2/test_normalizer_applitools.py`
- Create: `tests/quality/rollup_v2/fixtures/normalizers/applitools_sample.json`

- [ ] **Step 1: Create fixture** (Applitools batch result: `{stepsInfo: {total, unresolved, failed, mismatches}}`)
- [ ] **Step 2: Write failing tests** — parses unresolved/failed/mismatches as findings
- [ ] **Step 3: Implement** `ApplitoolsNormalizer(BaseNormalizer)`
- [ ] **Step 4: Implement** `check_applitools_zero.py` — `unresolved == 0 AND failed == 0`
- [ ] **Step 5: Create** `reusable-applitools.yml` — runs Applitools Eyes (SHA-pinned per §A.2.4). Include `eyes_config_path` validation via `validate_workflow_paths.py` (§B.2.2)
- [ ] **Step 6: Run → pass**
- [ ] **Step 7: Commit** `feat(qrv2-pr3): Applitools lane — normalizer + check + reusable workflow (§9.4)`

## Task 7: Register Chromatic + Applitools in pipeline + profile schema

**Files:**
- Modify: `scripts/quality/rollup_v2/pipeline.py` (register normalizers)
- Modify: profile schema or docs

- [ ] **Step 1: Register** Chromatic + Applitools normalizers in the pipeline (replace "not-configured" placeholders)
- [ ] **Step 2: Document profile schema** additions per §9.3/§9.4:
```yaml
visual_regression:
  chromatic:
    enabled: false
    project_token_secret: CHROMATIC_PROJECT_TOKEN
    storybook_build_script: "npm run build-storybook"
    storybook_dir: "storybook-static"
  applitools:
    enabled: false
    api_key_secret: APPLITOOLS_API_KEY
    batch_name_strategy: "branch-sha"
    eyes_config_path: "applitools.config.js"
```
- [ ] **Step 3: Commit** `feat(qrv2-pr3): register Chromatic + Applitools lanes + profile schema (§9.3 §9.4)`

## Task 8: Enable on `momentstudio` (§9.5)

**Files:**
- Modify: `profiles/repos/momentstudio.yml`

- [ ] **Step 1: Read current profile**
- [ ] **Step 2: Add** `visual_regression.chromatic.enabled: true` + `visual_regression.applitools.enabled: true`
- [ ] **Step 3: Commit** `feat(qrv2-pr3): enable Chromatic + Applitools on momentstudio (§9.5)`

## Task 9: `verify_action_pins.py` CI check (§B.3.6)

**Files:**
- Create: `scripts/quality/rollup_v2/verify_action_pins.py`
- Create: `tests/quality/rollup_v2/test_verify_action_pins.py`

- [ ] **Step 1: Write failing tests** — scan a fixture workflow YAML, flag floating tags, pass SHA pins
- [ ] **Step 2: Implement** — scans `.github/workflows/*.yml`, fails if any third-party `uses:` has a floating tag (first-party `actions/*` exempted per §A.2.4)
- [ ] **Step 3: Run → pass**
- [ ] **Step 4: Commit** `feat(qrv2-pr3): verify_action_pins.py CI check for SHA-pinning drift (§B.3.6)`

## Task 10: Extended post-remediation blocked-paths list (§B.3.17)

**Files:**
- Modify: `.github/workflows/reusable-remediation-loop.yml` (if the post-remediation diff check exists)

- [ ] **Step 1: Find the post-remediation `git diff --name-only` check**
- [ ] **Step 2: Extend** blocked-paths list to include: `.github/**`, `Dockerfile`, `docker-compose*.yml`, `requirements*.txt`, `pyproject.toml`, `setup.py`, `.npmrc`, `.pip/pip.conf` per §B.3.17. Use `--no-renames` flag.
- [ ] **Step 3: Commit** `feat(qrv2-pr3): extend post-remediation blocked-paths list (§B.3.17)`

## Task 11: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update provider count** (now 12+ providers including Semgrep, CodeQL, Chromatic, Applitools)
- [ ] **Step 2: Update rollup format** description to mention v2 multi-view markdown
- [ ] **Step 3: Commit** `docs(qrv2-pr3): update README with new providers + rollup v2 format`

## Task 12: Coverage + final verification

- [ ] **Step 1: Run coverage** on `rollup_v2/` — must still be 100%
- [ ] **Step 2: Run full test suite** — verify zero regressions
- [ ] **Step 3: Verify all 4 new lanes appear** in the pipeline's normalizer registry

## Pinned Action SHAs (§A.2.4)

| Action | Resolution command |
|---|---|
| `chromaui/action` | `gh api repos/chromaui/action/commits/v1 --jq .sha` |
| `applitools/eyes-*` | Resolve at implementation time based on momentstudio's test framework |
| `SonarSource/sonarqube-scan-action` | Already pinned in PR 2 |

## Self-review checklist

- [ ] SARIF normalizer has 50MB guard (§A.2.5)
- [ ] Semgrep + CodeQL normalizers share `_sarif.py` base (~60-70% code reuse)
- [ ] All new GitHub Actions SHA-pinned (not floating tags)
- [ ] `eyes_config_path` validated via `validate_workflow_paths.py` (§B.2.2)
- [ ] `momentstudio.yml` enables both visual providers
- [ ] `verify_action_pins.py` scans workflows for floating tags
- [ ] Post-remediation blocked-paths extended (§B.3.17)
- [ ] No `.pylintrc` deletion (PR 4)
- [ ] No coverage scope expansion (PR 4)
- [ ] README updated
