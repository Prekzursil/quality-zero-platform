# QRv2 Wiring + Automated Remediation — Design Document

**Date:** 2026-04-10
**Branch:** `fix/qrv2-test-fixes` (then dedicated feature branch)
**Depends on:** QRv2 PRs 1-3 (merged) + Fix PR #83

---

## 1. Problem Statement

QRv2 PRs 1-3 built the entire rollup_v2 pipeline (canonical schema, 13 normalizers, 31 patch generators, multi-view renderer, LLM fallback scaffold) — but **none of it runs in production**. The old `build_quality_rollup.py` still produces the useless 3-column table. The Codex remediation loop still uses generic prompts instead of the structured canonical findings with ready-to-apply patches.

Current state:

```
Quality gates → lane artifacts → build_quality_rollup.py (OLD) → useless table → PR comment
                                                                                    ↓
                                            render_codex_prompt.py (GENERIC) → Codex → fix PR
```

Target state:

```
Quality gates → lane artifacts → rollup_v2 pipeline → canonical.json + multi-view markdown
                                       ↓                              ↓
                              deterministic patches              PR comment (new format)
                                  auto-applied                        ↓
                                       ↓                    human reads structured findings
                              remaining findings → Codex/Claude → fix PR with file:line precision
```

## 2. Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Replace old rollup with rollup_v2 in CI | PR comments show multi-view markdown with patches |
| G2 | Auto-apply deterministic patches | Remediation PR includes all `patch_source: "deterministic"` fixes without LLM involvement |
| G3 | Feed structured findings to Codex/Claude | Remediation prompt includes file:line, category, fix_hint, context_snippet for each finding |
| G4 | Upload coverage to all platforms | SonarCloud, Codecov, Codacy, DeepSource, QLTY all receive coverage.xml |
| G5 | End-to-end: detect → report → fix → verify | A single workflow run can detect issues, report them, auto-fix what's deterministic, and LLM-fix the rest |

## 3. Architecture — Wire rollup_v2 into CI

### 3.1 Replace old rollup call in `reusable-scanner-matrix.yml`

The `quality-rollup` job at line 535 currently calls:
```bash
python platform/scripts/quality/build_quality_rollup.py \
  --profile-json "$RUNNER_TEMP/profile.json" \
  --repo "$ROLLUP_REPO_SLUG" \
  --sha "$ROLLUP_SHA" \
  --artifacts-dir artifacts \
  --out-json quality-rollup/summary.json \
  --out-md quality-rollup/summary.md
```

Replace with:
```bash
python -m scripts.quality.rollup_v2 \
  --artifacts-dir artifacts \
  --output-dir quality-rollup \
  --repo "$ROLLUP_REPO_SLUG" \
  --sha "$ROLLUP_SHA"
```

This produces:
- `quality-rollup/canonical.json` — machine-readable findings for remediation
- `quality-rollup/rollup.md` — human-readable multi-view markdown for PR comment

### 3.2 Update `post_pr_quality_comment.py`

Currently reads `quality-rollup/summary.md`. Update to read `quality-rollup/rollup.md` (the new renderer output). The sticky comment marker `<!-- quality-zero-rollup -->` stays the same so existing comments get updated in place.

### 3.3 Feature flag for rollback

Add a workflow input `rollup_version` (default: `v2`, options: `v1`/`v2`) so the old pipeline can be restored instantly if v2 has issues in production. The old `build_quality_rollup.py` stays as a thin wrapper (per PR 1's A.3.4 design).

## 4. Architecture — SonarCloud CI-based Analysis

SonarCloud's "Automatic Analysis" conflicts with CI-based analysis (error: "You are running CI analysis while Automatic Analysis is enabled"). Both find the **same issues** — the difference is WHERE analysis runs:

| Mode | Runs on | Coverage upload | Integrates with rollup |
|---|---|---|---|
| Automatic Analysis | SonarCloud servers | NO | NO |
| CI-based Analysis | GitHub Actions runner | YES (from our test suite) | YES (via rollup pipeline) |

**Decision:** Disable Automatic Analysis, keep CI-based. Rationale:
- CI-based uploads coverage data (automatic does not)
- CI-based integrates with the rollup pipeline
- All the same issues are found — zero suppression
- SonarCloud explicitly says you can't run both

The SonarCloud scan step in `reusable-scanner-matrix.yml` (already added in PR 2, upgraded to v6) handles this.

## 5. Architecture — Deterministic Patch Auto-Apply

### 5.1 New step in `reusable-remediation-loop.yml`

After downloading lane artifacts and BEFORE invoking Codex:

```yaml
- name: Auto-apply deterministic patches from canonical.json
  working-directory: repo
  run: |
    python platform/scripts/quality/rollup_v2/apply_deterministic_patches.py \
      --canonical-json "$RUNNER_TEMP/canonical.json" \
      --repo-dir . \
      --out-json "$RUNNER_TEMP/applied-patches.json"
```

### 5.2 `apply_deterministic_patches.py` logic

```python
for finding in canonical["findings"]:
    if finding["patch_source"] == "deterministic" and finding["patch"]:
        # Write the unified diff to a temp file
        # Run `git apply --check` first (dry-run)
        # If clean: `git apply` and record success
        # If conflict: skip, record in remaining list
        # Track: applied_count, skipped_count, remaining_findings
```

Output: `applied-patches.json` with:
- `applied`: list of findings that were successfully patched
- `remaining`: list of findings that still need LLM or human attention
- `skipped`: list of findings where the diff didn't apply cleanly

### 5.3 Commit deterministic fixes

After auto-apply:
```yaml
- name: Commit deterministic fixes
  working-directory: repo
  run: |
    git add -A
    git diff --cached --quiet || \
      git commit -m "fix: auto-apply $(cat $RUNNER_TEMP/applied-patches.json | python -c 'import json,sys; print(json.load(sys.stdin)["applied_count"])') deterministic patches from rollup_v2"
```

## 6. Architecture — Structured Codex/Claude Remediation

### 6.1 Update `render_codex_prompt.py` to consume canonical.json

Currently the prompt is generic: "fix the quality issues for this repo". Update to include structured findings from `canonical.json`:

```markdown
## Findings requiring attention

### File: scripts/quality/coverage_parsers.py

#### Finding 1: broad-except (line 42, severity: high)
- **Message**: Catch a more specific exception instead of bare Exception
- **Fix hint**: Narrow the exception type. Use IOError, ValueError, etc.
- **Providers**: Codacy, SonarCloud, DeepSource (3 providers agree)
- **Category**: quality/broad-except
- **Suggested patch** (apply with `git apply`):
```diff
@@ -40,3 +40,3 @@
     try:
         parse_coverage(path)
-    except Exception as e:
+    except (IOError, ValueError) as e:
         log.warning(...)
```

This gives the LLM (Codex or Claude) the EXACT context it needs to fix each issue — file, line, category, fix hint, and a reference patch.

### 6.2 Two-pass remediation

1. **Pass 1 (deterministic)**: Auto-apply all `patch_source: "deterministic"` patches via `git apply` — zero LLM cost, instant.
2. **Pass 2 (LLM)**: Feed ONLY the remaining findings (`patch_source: "llm"` or `"none"`) to the LLM with structured prompts. The LLM gets:
   - The specific file + line
   - The category + fix hint
   - The context snippet (redacted)
   - For `patch_source: "llm"`: a cached LLM-generated patch to verify/apply
   - For `patch_source: "none"`: the finding only, LLM must generate the fix

### 6.3 Claude Code as alternative to Codex

The existing remediation loop uses `run_codex_exec.py` (Codex CLI). Add a parallel path for Claude Code:

```yaml
- name: Run Claude Code remediation
  if: ${{ needs.resolve-profile.outputs.remediation_engine == 'claude' }}
  run: |
    claude --dangerously-skip-permissions \
      --print \
      --output-format json \
      "Fix the following quality findings. For each, apply the suggested patch if provided, or write the minimal fix. Commit each fix separately." \
      < "$RUNNER_TEMP/remaining-findings-prompt.md"
```

Profile-level control: `remediation_engine: "codex" | "claude" | "both"` in the repo profile YAML.

## 7. Architecture — Coverage Upload to All Platforms

Coverage upload already exists in the scanner-matrix for most platforms:
- **Codecov**: `reusable-codecov-analytics.yml` ✅ (already working)
- **Codacy**: `codacy-coverage-reporter-action` at line ~406 ✅ (already in scanner-matrix)
- **DeepSource**: `deepsource report` at line ~418 ✅ (exists, but needs `DEEPSOURCE_DSN` secret provisioned)
- **QLTY**: `qlty coverage publish` at line ~434 ✅ (already working)
- **SonarCloud**: `sonarqube-scan-action` at line 390 ✅ (added in PR 2, needs auto-analysis disabled)

The only missing piece: **`DEEPSOURCE_DSN` is not provisioned as a GitHub secret.** This needs to be added from the DeepSource project settings.

## 8. Architecture — End-to-End Workflow

The full detect → report → fix → verify loop:

```
1. PR opened/pushed
   ↓
2. reusable-scanner-matrix.yml runs
   - 10+ quality lanes produce artifacts
   - Coverage uploaded to SonarCloud, Codecov, Codacy, DeepSource, QLTY
   ↓
3. Quality rollup job runs rollup_v2 pipeline
   - Normalizes all lane artifacts → canonical findings
   - Dedup + merge corroborators
   - Generate deterministic patches
   - Render multi-view markdown
   - Post PR comment with structured findings
   ↓
4. If findings > 0 AND remediation enabled:
   - Auto-apply deterministic patches (git apply)
   - Feed remaining findings to Codex/Claude with structured prompts
   - Create remediation PR with fixes
   - Run verify command on the remediation branch
   ↓
5. Human reviews:
   - Reads the structured rollup comment
   - Reviews the remediation PR
   - Merges or requests changes
```

## 9. Scope — What's in this phase

### In scope:
- Wire rollup_v2 into the quality-rollup job (replace old build_quality_rollup.py call)
- Create `apply_deterministic_patches.py` for auto-apply
- Update `render_codex_prompt.py` to consume canonical.json
- Update `post_pr_quality_comment.py` to read rollup.md
- SonarCloud: disable auto-analysis (keeps ALL issues, routes through CI)
- Document the `DEEPSOURCE_DSN` provisioning requirement
- Feature flag for v1/v2 rollback
- Add Claude Code as an alternative remediation engine

### Out of scope (deferred):
- Full Chromatic/Applitools visual testing (needs `CHROMATIC_PROJECT_TOKEN` + `APPLITOOLS_API_KEY`)
- PR 4 legacy cleanup (wrapper removal, full-tree coverage)
- LLM fallback cache with HMAC signing (scaffold exists, operational wiring is separate)
- Auto-triggering remediation on every failed quality gate (currently triggered manually)

## 10. Success Criteria

- ✅ PR comments on downstream repos show the new multi-view markdown format (not the old 3-column table)
- ✅ `canonical.json` is uploaded as a workflow artifact alongside `rollup.md`
- ✅ Deterministic patches are auto-applied in the remediation loop (measurable: `applied_count > 0` in the remediation PR)
- ✅ The Codex/Claude prompt includes structured findings with file:line, category, fix_hint
- ✅ Coverage appears on SonarCloud dashboard (not just Codecov)
- ✅ Feature flag `rollup_version: v1` restores the old pipeline within 1 commit

## 11. Non-Goals

- NOT making all platform dashboards show zero issues (that's the human/LLM's job after the rollup reports them)
- NOT replacing the existing check_*_zero.py gate scripts (they continue as pass/fail assertions)
- NOT auto-merging remediation PRs (always require human review)
- NOT provisioning secrets from code (DEEPSOURCE_DSN, CHROMATIC_PROJECT_TOKEN, etc. are manual steps)

## 12. Prerequisites (manual steps)

| Action | Owner | Where |
|---|---|---|
| Disable SonarCloud "Automatic Analysis" | You | SonarCloud → Project → Admin → Analysis Method |
| Provision `DEEPSOURCE_DSN` | You | DeepSource → Project → Settings → DSN → copy → `gh secret set DEEPSOURCE_DSN` |
| Provision `CHROMATIC_PROJECT_TOKEN` | You (if using Chromatic) | Chromatic → Project → Settings → Token |
| Provision `APPLITOOLS_API_KEY` | You (if using Applitools) | Applitools → Account → API Key |
