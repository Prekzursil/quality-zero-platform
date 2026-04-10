# Quality Rollup v2 — PR 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Wire up platform self-governance (coverage gate on `scripts/quality/rollup_v2/`), fix SonarCloud coverage upload, deduplicate Codecov, properly configure Codacy with explicit engines, and add ruff/bandit/semgrep per-tool config files.

**Architecture:** Configuration-heavy PR — primarily YAML, TOML, and properties files. One shell script modification (`scripts/verify`). No new Python modules. Coverage scope limited to `scripts/quality/rollup_v2/` per design §A.3.3; full-tree expansion deferred to PR 4.

**Tech Stack:** `pyproject.toml` (coverage + ruff config), `.codacy.yaml`, `.bandit`, `.semgrep.yml`, `sonar-project.properties`, GitHub Actions YAML.

**Source of truth:** Design doc `docs/plans/2026-04-09-quality-rollup-v2-design.md` — §§6-8 + Addendum A (§A.3.1, §A.3.3, §A.7, §A.2.4, §A.2.5) + Addendum B (§B.3.3, §B.3.13, §B.3.18).

---

## Task 1: Create `pyproject.toml` with `[tool.coverage]` + `[tool.ruff]`

**Files:** Create: `pyproject.toml`

- [ ] **Step 1: Create `pyproject.toml`** with the `[tool.coverage.run]` and `[tool.coverage.report]` sections from §6.2, SCOPED to `scripts/quality/rollup_v2` per §A.3.3 (NOT the full-tree `scripts/quality` — that's PR 4). Also add `[tool.ruff]` section per §8.2 and §A.7.

```toml
[tool.coverage.run]
source = [
  "scripts/quality/rollup_v2",
]
branch = true
omit = [
  "tests/*",
  "scripts/provider_ui/*.mjs",
]

[tool.coverage.report]
fail_under = 100.0
precision = 2
show_missing = true
skip_covered = false
exclude_lines = [
  "pragma: no cover",
  "raise NotImplementedError",
  "if __name__ == .__main__.:",
  "if TYPE_CHECKING:",
  "@abstractmethod",
  "^\\s*\\.\\.\\.\\s*$",
]

[tool.ruff]
target-version = "py312"
line-length = 120
src = ["scripts", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "A", "C4", "SIM", "TCH", "S", "PT"]
ignore = ["S101"]  # assert is used in tests

[tool.ruff.lint.isort]
known-first-party = ["scripts"]
```

- [ ] **Step 2: Verify** `python -m coverage run --source=scripts/quality/rollup_v2 -m unittest discover -s tests -p 'test_*.py' && python -m coverage report --fail-under=100`
- [ ] **Step 3: Commit**
```bash
git add pyproject.toml
git commit -m "feat(qrv2-pr2): add pyproject.toml with [tool.coverage] + [tool.ruff] (§6.2 §8.2 §A.3.3 §A.7)"
```

## Task 2: Augment `scripts/verify` with coverage measurement

**Files:** Modify: `scripts/verify`

- [ ] **Step 1: Read current `scripts/verify`** to understand the existing structure
- [ ] **Step 2: Add coverage measurement** after the unittest discover line, per §6.3:
  - `python -m coverage run -m unittest discover -s tests -p 'test_*.py'` (replaces the bare unittest line)
  - `python -m coverage xml -o coverage.xml`
  - `python -m coverage report --fail-under=100 --show-missing`
  - Keep the `node --test` and validator lines after coverage
- [ ] **Step 3: Run `bash scripts/verify`** to confirm it passes (coverage on rollup_v2/ should be 100%)
- [ ] **Step 4: Commit**
```bash
git add scripts/verify
git commit -m "feat(qrv2-pr2): augment scripts/verify with coverage measurement + 100% gate (§6.3)"
```

## Task 3: Create `sonar-project.properties`

**Files:** Create: `sonar-project.properties`

- [ ] **Step 1: Create the file** per §7.1, with amendments from §A.2.5:
  - `sonar.python.version=3.12` (single version, not `3.11,3.12` per A.2.5)
  - Remove `profiles/**` from `sonar.exclusions` (per A.2.5 — profiles should get Sonar coverage)

```properties
sonar.projectKey=Prekzursil_quality-zero-platform
sonar.organization=prekzursil
sonar.sources=scripts
sonar.tests=tests
sonar.python.version=3.12
sonar.python.coverage.reportPaths=coverage.xml
sonar.exclusions=generated/**,docs/admin/**
```

- [ ] **Step 2: Commit**
```bash
git add sonar-project.properties
git commit -m "feat(qrv2-pr2): add sonar-project.properties for SonarCloud coverage upload (§7.1 §A.2.5)"
```

## Task 4: Add SonarCloud scan step + Codecov dedup in workflows

**Files:** Modify: `.github/workflows/reusable-scanner-matrix.yml`

- [ ] **Step 1: Find the SonarSource action's commit SHA** for pinning per §A.2.4
  - Run: `gh api repos/SonarSource/sonarqube-scan-action/commits/v4 --jq .sha` (or similar)
  - Pin to the exact SHA

- [ ] **Step 2: Add the SonarCloud scan step** in the coverage lane of `reusable-scanner-matrix.yml` per §7.2, pinned to SHA

- [ ] **Step 3: Remove the duplicate Codecov upload** from the scanner-matrix coverage lane per §7.3 (keep it only in `reusable-codecov-analytics.yml`)
  - Search for Codecov-related steps in `reusable-scanner-matrix.yml`
  - Remove/comment the duplicate upload step

- [ ] **Step 4: Commit**
```bash
git add .github/workflows/reusable-scanner-matrix.yml
git commit -m "feat(qrv2-pr2): add SonarCloud scan step (SHA-pinned) + Codecov dedup (§7.2 §7.3 §A.2.4)"
```

## Task 5: Rewrite `.codacy.yaml` with engines block

**Files:** Modify: `.codacy.yaml`

- [ ] **Step 1: Read current `.codacy.yaml`** (currently 5 lines of excludes per §1 problem 3)
- [ ] **Step 2: Rewrite** with the engines block from §8.1 + §A.7 (ruff enabled, pylint stays but deprecated):

```yaml
---
engines:
  pylint:
    enabled: true   # deprecated in favor of ruff — removed in PR 4 (§A.7)
  bandit:
    enabled: true
  semgrep:
    enabled: true
  ruff:
    enabled: true
  prospector:
    enabled: true
  pydocstyle:
    enabled: false   # enforced via ruff instead
exclude_paths:
  - "generated/**"
  - "tests/**"
  - "profiles/**"
  - "docs/admin/**"
  - ".github/workflows/control-plane-admin.yml"
  - ".github/workflows/publish-admin-dashboard.yml"
```

- [ ] **Step 3: Commit**
```bash
git add .codacy.yaml
git commit -m "feat(qrv2-pr2): rewrite .codacy.yaml with explicit engines block (§8.1 §A.7)"
```

## Task 6: Create per-tool config files (`.bandit`, `.semgrep.yml`, `.pylintrc` audit)

**Files:**
- Create: `.bandit`
- Create: `.semgrep.yml`
- Modify: `.pylintrc` (add deprecation TODO + tighten)

- [ ] **Step 1: Create `.bandit`** per §8.2:
```ini
[bandit]
skips = B101
targets = scripts
exclude_dirs = tests,generated,.github
```

- [ ] **Step 2: Create `.semgrep.yml`** per §8.2 / §9.1 (basic custom rules + registry opt-in):
```yaml
rules:
  - id: no-hardcoded-secrets
    patterns:
      - pattern: $KEY = "..."
      - metavariable-regex:
          metavariable: $KEY
          regex: ".*(SECRET|TOKEN|PASSWORD|API_KEY|PRIVATE_KEY).*"
    message: "Potential hardcoded secret in $KEY"
    severity: ERROR
    languages: [python]
```

- [ ] **Step 3: Audit `.pylintrc`** — add `# DEPRECATED — kept for one cycle; removed in PR 4 (§A.7)` at the top. Check for any overly permissive rules and tighten if appropriate.

- [ ] **Step 4: Commit**
```bash
git add .bandit .semgrep.yml .pylintrc
git commit -m "feat(qrv2-pr2): add .bandit + .semgrep.yml + audit .pylintrc (§8.2 §A.7)"
```

## Task 7: Flip `.coverage-thresholds.json` blocking flags to true

**Files:** Modify: `.coverage-thresholds.json`

- [ ] **Step 1: Read the current file** — flags should be `false` with the "TEMPORARILY" comment
- [ ] **Step 2: Flip both flags**:
  - `"blockPRCreation": true`
  - `"blockTaskCompletion": true`
  - Update the `$comment` to remove "TEMPORARILY" language
  - Update the `description` to remove the "TEMPORARILY non-blocking" note
- [ ] **Step 3: Commit**
```bash
git add .coverage-thresholds.json
git commit -m "feat(qrv2-pr2): flip coverage-gate flags to blocking (§A.3.1 — PR 2 mandate)"
```

## Task 8: Add CODEOWNERS for scanner configs

**Files:** Create or modify: `CODEOWNERS` (or `.github/CODEOWNERS`)

- [ ] **Step 1: Check if CODEOWNERS exists**
  - `ls CODEOWNERS .github/CODEOWNERS 2>/dev/null`
- [ ] **Step 2: Create/append** the entries from §B.3.18:
```
# Scanner config files — require security-aware review (§B.3.18)
.semgrep.yml     @Prekzursil
.codacy.yaml     @Prekzursil
.bandit          @Prekzursil
.pylintrc        @Prekzursil
pyproject.toml   @Prekzursil
```
- [ ] **Step 3: Commit**
```bash
git add CODEOWNERS .github/CODEOWNERS
git commit -m "feat(qrv2-pr2): add CODEOWNERS for scanner config files (§B.3.18)"
```

## Task 9: File PR 4 tracking issue

**Files:** None (GitHub API call)

- [ ] **Step 1: Create the issue** per §B.3.13:
```bash
gh issue create \
  --title "PR 4: Legacy cleanup + full-tree coverage" \
  --body "## Scope (from QRv2 design §A.3.3, §A.3.4, §A.7, §A.12)

- [ ] Remove \`build_quality_rollup.py\` wrapper (\`TODO(qrv2-pr4)\` comments)
- [ ] Migrate \`post_pr_quality_comment.py\` to use \`rollup_v2\` directly
- [ ] Delete \`.pylintrc\` (ruff is primary since PR 2)
- [ ] Expand \`pyproject.toml [tool.coverage.run] source\` to full tree: \`scripts/quality\`, \`scripts/security_helpers.py\`, \`scripts/provider_ui\`
- [ ] Achieve 100% line+branch coverage on ALL pre-existing modules
- [ ] Update existing tests to import from \`rollup_v2\` instead of wrapper

## Context
Created automatically at PR 2 merge per design §B.3.13." \
  --label "enhancement"
```
- [ ] **Step 2: Note the issue number in a commit message**

## Task 10: Final verification + coverage

- [ ] **Step 1: Run `bash scripts/verify`** — confirm ALL rollup_v2 tests pass + coverage is 100%
- [ ] **Step 2: Run existing tests** to confirm zero regressions: `python -m unittest discover -s tests -p 'test_*.py' 2>&1 | tail -5`
- [ ] **Step 3: Verify git status is clean**

## Self-review checklist

- [ ] `pyproject.toml` has `[tool.coverage]` scoped to `rollup_v2/` only (not full tree)
- [ ] `scripts/verify` runs coverage + 100% gate
- [ ] `sonar-project.properties` has `sonar.python.version=3.12` (single, not dual per §A.2.5)
- [ ] `sonar.exclusions` does NOT include `profiles/**` (per §A.2.5)
- [ ] SonarCloud action pinned to SHA (not floating `@v4` tag per §A.2.4)
- [ ] `.codacy.yaml` has 5 engines explicitly declared
- [ ] `.coverage-thresholds.json` has `blockPRCreation: true` and `blockTaskCompletion: true`
- [ ] `.pylintrc` has deprecation comment
- [ ] CODEOWNERS entries present
- [ ] No Semgrep/CodeQL normalizer changes (that's PR 3)
- [ ] No wrapper removal (that's PR 4)
