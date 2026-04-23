# Quality-Zero Platform v2 — Design

**Status:** draft, pending review
**Author:** session 2026-04-22
**Supersedes:** the current ad-hoc `profiles/` layout + reusable workflows, not replacing — extending and closing leaks surfaced by event-link PR #127/#128.

---

## 1. North star

**Zero-tolerance governance across all non-fork Prekzursil repos (private included only for `pbinfo-get-unsolved`).**

Every governed repo converges on:
- 0 open findings across all enabled scanners
- 100% coverage on every declared coverage input
- Canonical config (ci.yml, .codacy.yml, codecov.yml, vite.config.ts coverage block, .deepsource.toml, dependabot.yml, .coverage-thresholds.json) owned by the platform and auto-synced via PR
- Drift detected and auto-fixed or PR-proposed

Legacy repos can onboard in **ratchet mode** with a baseline and a hard deadline; no open-ended debt.

---

## 2. Fleet filter

```yaml
# scripts/quality/fleet_inventory.py
include:
  owner: Prekzursil
  visibility: [public]
  fork: false
exceptions:
  include_private:
    - Prekzursil/pbinfo-get-unsolved
exclude:
  archived: false         # archived still included; profile marks read-only
  templates: true         # GitHub template repos excluded
```

The inventory script runs daily and diffs against `profiles/repos/*.yml`. Missing repos → GitHub issue `alert:repo-not-profiled`.

---

## 3. Profile schema (v2) — `profiles/repos/<slug>.yml`

```yaml
slug: Prekzursil/event-link
stack: fullstack-web
version: 2                        # schema version — bumped when schema changes

mode:                             # governance mode
  phase: absolute                 # shadow | ratchet | absolute
  shadow_until: null              # auto-set during bootstrap; null when phased
  ratchet:
    baseline:                     # frozen at ratchet-onboarding time
      coverage_overall: 100.0
      coverage_per_flag:
        backend: 100.0
        ui: 100.0
      findings:
        codacy_issues: 0
        sonar_issues: 0
    target_date: 2026-06-30       # must reach absolute by here
    escalation_date: 2026-09-30   # hard flip to absolute no matter what

coverage:
  min_percent: 100.0
  branch_min_percent: 100.0
  inputs:
    - name: backend
      flag: backend               # ← MANDATORY now — required by reusable-codecov-analytics
      path: backend/coverage.xml
      format: xml
      min_percent: 100.0
    - name: frontend
      flag: ui
      path: ui/coverage/cobertura-coverage.xml
      format: xml
      min_percent: 100.0
    - name: frontend-lcov
      flag: ui
      path: ui/coverage/lcov.info
      format: lcov

scanners:                         # replaces `enabled_scanners`
  codeql:        { enabled: true, severity: block }
  dependabot:    { enabled: true, severity: block }
  sonarcloud:    { enabled: true, severity: block }
  codacy_issues: { enabled: true, severity: block }
  codacy_complexity: { enabled: true, severity: block }
  codacy_clones: { enabled: true, severity: block }
  codacy_coverage: { enabled: true, severity: block }
  deepscan:      { enabled: true, severity: block }
  deepsource_visible: { enabled: true, severity: block }
  semgrep:       { enabled: true, severity: block }
  sentry:        { enabled: true, severity: block }
  socket_pr_alerts: { enabled: true, severity: block }
  socket_project_report: { enabled: true, severity: info }
  qlty_check:    { enabled: true, severity: block }
  applitools:    { enabled: false }  # only visual-regression stacks
  chromatic:     { enabled: false }

overrides:                        # explicit deviations from template
  - file: ui/vite.config.ts
    key: coverage.thresholds.functions
    value: 90
    reason: |
      12 legacy wrapper components can't be unit-tested
      without refactor (tracked in #42).
    expires: 2026-12-31

required_contexts:                # derived from scanners.*.severity == block
  # auto-generated — do not hand-edit
  always: []
  target: []
  pull_request_only: []

vendors:
  sonar:      { project_key: "${sonar_project_key}"  # e.g. <owner>_<repo-slug> }
  codacy:     { project_name: event-link }
  deepsource: { shortcode: event-link }
  codecov:    { slug: Prekzursil/event-link }
  socket:     { org: prekzursil }

codeql:
  languages: [actions, javascript-typescript, python]

dependabot:
  updates:
    - { ecosystem: pip, directory: /backend }
    - { ecosystem: npm, directory: /ui }
```

Key schema changes from v1:
- `mode.phase` with `shadow | ratchet | absolute` replaces the old flat `issue_policy.mode`.
- `coverage.inputs[].flag` is now **required** — drives Codecov per-flag uploads.
- `scanners.*.severity` replaces `enabled_scanners` binary flag.
- `overrides[]` formalizes legitimate deviations with `reason` + `expires`.
- `required_contexts` becomes auto-generated from `scanners` rather than hand-maintained.

---

## 4. Template layout — `profiles/templates/`

Platform becomes a template publisher. Per stack, a templates directory renders consumer files.

```
profiles/templates/
  common/
    ci-fragments/
      setup-python.yml.j2
      setup-node.yml.j2
      codecov-upload-per-flag.yml.j2      # loops over coverage.inputs
    codecov.yml.j2
    .coverage-thresholds.json.j2
    dependabot.yml.j2
  stack/
    fullstack-web/
      ci.yml.j2                           # full frontend + backend + integration jobs
      .codacy.yml.j2                      # includes BOTH lizard + metric engines
      vite.config.ts.coverage-block.j2    # rendered as a marked region
      ui/tsconfig.override.json.j2
    go/
      ci.yml.j2
      .codacy.yml.j2
    cpp-cmake/...
    dotnet-wpf/...
    gradle-java/...
```

Rendering uses Jinja2 with the profile as the context. Per-file marked regions let consumer hand-edit unrelated parts:

```typescript
// ui/vite.config.ts
// BEGIN quality-zero:coverage-block — do not edit by hand
coverage: {
  provider: 'v8',
  reporter: ['text', 'json-summary', 'lcov', 'cobertura'],
  include: ['src/**/*.{ts,tsx}'],
  exclude: ['src/**/*.d.ts', 'src/types/**', 'tests/**'],
  thresholds: { lines: 100, functions: 100, branches: 100, statements: 100 },
},
// END quality-zero:coverage-block
```

Drift check parses the marked regions and compares against re-rendered template.

---

## 5. Reusable workflows — changes

### 5.1 `reusable-codecov-analytics.yml` — fix the flag-split bug

Current: one upload step, all `coverage_input_files` passed with no flag → Codecov aggregates as "Python".

New: profile yields a matrix of inputs; one upload step per flag.

```yaml
- name: Export profile
  id: profile
  run: python platform/scripts/quality/export_profile.py ...  # emits inputs_json

- name: Upload each input to Codecov
  shell: bash
  run: |
    for row in $(echo '${{ steps.profile.outputs.inputs_json }}' | jq -c '.[]'); do
      flag=$(echo "$row" | jq -r '.flag')
      path=$(echo "$row" | jq -r '.path')
      # codecov-action can't loop natively → invoke codecov CLI directly
      ./codecov -f "$path" -F "$flag" --token "${{ secrets.CODECOV_TOKEN }}"
    done

- name: Validate flags reached Codecov
  run: python platform/scripts/quality/validate_codecov_flags.py \
         --profile "$RUNNER_TEMP/profile.json" \
         --sha "${{ github.sha }}"
```

`validate_codecov_flags.py` polls Codecov's `/api/v2/github/<owner>/repos/<name>/commits/<sha>/` and asserts each declared flag received a report. Fails the job if any flag missing → catches the silent-drop that let event-link ship at 58%.

### 5.2 `reusable-quality-zero-gate.yml` — severity-aware rollup

`build_quality_rollup.py` takes the profile as input and produces:

```
gate_status = all(severity.block) → pass
              any(severity.block in fail/action_required) → fail
              any(severity.warn in fail) → pass with warning annotation
              severity.info → recorded, never affects status
```

Break-glass bypass:

```yaml
- name: Check break-glass label
  id: breakglass
  run: python platform/scripts/quality/check_breakglass.py \
         --pr "${{ github.event.number }}" \
         --require-incident-id
  continue-on-error: true

- name: Skip gate if break-glass
  if: steps.breakglass.outputs.active == 'true'
  run: echo "break-glass active, skipping blocking gates"
```

Two labels:
- `quality-zero:break-glass` — requires `Incident: <id>` in PR body, audited to `audit/break-glass.jsonl`, auto-opens remediation issue post-merge
- `quality-zero:skip` — discretionary "merge now," no incident required, audited to `audit/skip.jsonl`, weekly digest flags frequent users

### 5.3 New: `reusable-drift-sync.yml`

Called by platform's scheduler. For each governed repo:

1. Clone consumer
2. Render templates from stack preset + profile
3. Diff against consumer's current files
4. If diff:
   - Branch: `quality-zero/sync-<template-name>-<platform-sha[:7]>`
   - PR title: `chore(quality-zero): sync <template-name> (platform@<sha[:7]>)`
   - Auto-merge enabled if consumer CI is green AND no uncommitted conflicts
5. Record sync status in `audit/drift-sync.jsonl`

### 5.4 New: `reusable-bootstrap-repo.yml`

`workflow_dispatch` on platform:

```yaml
inputs:
  repo_slug: { required: true }
  stack: { required: true, options: [fullstack-web, go, cpp-cmake, dotnet-wpf, gradle-java] }
  initial_mode: { required: true, default: shadow, options: [shadow, ratchet, absolute] }
  ratchet_target_date: { required: false }
```

Steps:
1. Create `profiles/repos/<slug-sanitized>.yml` from stack defaults.
2. Open PR on target repo titled "feat(quality-zero): onboard".
3. PR runs all gates in **shadow mode** (status-check name suffix `-shadow`, never required).
4. Manual review + merge of onboarding PR.
5. Platform polls CI: **3 consecutive green shadow runs** → auto-PR to flip `mode.phase: shadow` → `absolute` (or `ratchet` if user chose that).

### 5.5 New: `reusable-bumps.yml`

`workflow_dispatch` driven by `profiles/bumps/<date>-<name>.yml`:

```yaml
name: Node 20 → 24
target:
  - file_glob: "**/ci.yml"
    yaml_path: "jobs.*.steps[?.uses contains 'setup-node'].with.node-version"
    value: '24'
affects_stacks: [fullstack-web]
staging_repos:
  - Prekzursil/env-inspector
  - Prekzursil/webcoder
full_rollout_after_staging: true
rollback_on_failure: true
```

Waves:
1. Stage 1: open PRs against `staging_repos`, wait for CI green on all.
2. Stage 2 (auto or manual gate): open PRs against remainder.
3. Rollback: if stage 1 CI fails on ≥ 1 repo, revert the bump recipe + open `alert:fleet-bump-fail` issue.

---

## 6. Known-issues registry — `known-issues/`

Structured YAML, one file per gotcha. Example:

```yaml
# known-issues/py-ineffectual-await-in-suppress.yml
id: QZ-FP-001
title: CodeQL py/ineffectual-statement flags await inside `with suppress(...)`
languages: [python]
triggers:
  - scanner: codeql
    rule: py/ineffectual-statement
conflicts_with:
  - scanner: sonarcloud
    rule: python:S7497     # demands re-raise of CancelledError
detection:
  code_pattern: |
    with suppress(asyncio.CancelledError):
        await .+
recommended_fix: |
  Rewrite to `await asyncio.gather(task, return_exceptions=True)`.
  Satisfies both queries: gather() is clearly effectful (CodeQL happy)
  and returns the exception as a value instead of raising (Sonar happy).
fix_snippet: |
  # before:
  try:
      yield
  finally:
      cleanup_task.cancel()
      with suppress(asyncio.CancelledError):
          await cleanup_task
  # after:
  try:
      yield
  finally:
      cleanup_task.cancel()
      await asyncio.gather(cleanup_task, return_exceptions=True)
example_prs:
  - Prekzursil/event-link#127
feeds_qrv2: true
```

Platform publishes `known-issues/` as a docs site (GitHub Pages). QRv2 loads all entries with `feeds_qrv2: true` into the Codex prompt, so auto-remediation sees known fixes.

Seed the registry from event-link lessons:
- `QZ-FP-001` CodeQL ↔ Sonar await/suppress
- `QZ-FP-002` SonarCloud `typescript:S3735` vs `void promise()`
- `QZ-FP-003` Codacy `metric` engine separate from `lizard`
- `QZ-CV-001` Codecov flag-split bug (required `inputs[].flag`)

---

## 7. Dashboard — `publish-admin-dashboard.yml` additions

Static site published to GitHub Pages (or the existing admin URL). Pages:

1. **Fleet heatmap** (`/index.html`)
   - Table: rows = repos (15 today, will grow), cols = scanner checks + coverage gate.
   - Cell color: green/warn/red/not-applicable.
   - Click → repo's latest PR with failing gate.
2. **Coverage trend** (`/coverage.html`)
   - Per repo, line chart: last 30 days, overall + per-flag.
   - Highlights regressions > 0.5% day-over-day with red markers.
   - Shows `ratchet.baseline` + `target_date` overlay for ratchet-mode repos.
3. **Drift list** (`/drift.html`)
   - Open drift-sync PRs across fleet, grouped by template.
   - Age of oldest un-merged sync. > 3 days → opens `alert:drift-stuck` issue.
4. **Audit feed** (`/audit.html`)
   - Live view of `audit/break-glass.jsonl` + `audit/skip.jsonl`.
   - Filter by user, label, time window.
   - Unresolved break-glass > 7 days → `alert:bypass-stale` issue.

Data source: consolidated `quality-rollup/summary.json` artifacts from every governed repo's last successful scan, pulled via `gh api` in `build_admin_dashboard.py`.

---

## 8. Alerting — GitHub issues in platform repo

Events and labels:

| Event | Label | Opens when | Closes when |
|---|---|---|---|
| Coverage regression | `alert:regression` | main-branch cov drops > 0.5% | cov recovers or PR merges fix |
| Ratchet deadline missed | `alert:deadline-missed` | `target_date` < today AND repo not at absolute | `mode.phase == absolute` |
| Ratchet escalation hit | `alert:escalation` | `escalation_date` < today AND repo not at absolute | `mode.phase == absolute` |
| Bypass stale | `alert:bypass-stale` | break-glass unresolved > 7 days | tracking issue closed |
| Drift stuck | `alert:drift-stuck` | sync PR open > 3 days | PR merged or closed |
| Fleet bump failed | `alert:fleet-bump-fail` | staging wave CI failed | rollback PR merged |
| Repo not profiled | `alert:repo-not-profiled` | inventory finds new repo without profile | profile added or repo excluded |
| Flag not reported | `alert:flag-missing` | Codecov validator detects declared flag got no report | flag reports again |

`CODEOWNERS` auto-assigns. Weekly digest issue summarizes all open alerts.

---

## 9. Migration plan (5 phases)

Each phase is 1+ PRs to `quality-zero-platform` + N follow-up drift PRs on consumer repos.

### Phase status at a glance (updated 2026-04-23)

| Phase | Code | PRs |
|---|---|---|
| 1 — schema v2 + fleet inventory | merged | pre-Phase-5 series |
| 2 — Codecov flag fix + validator | merged | pre-Phase-5 series |
| 3 — templates + drift-sync | merged (code) | pre-Phase-5 series |
| 4 — severity map + break-glass + known-issues | merged | #102-#106 |
| 5 — bootstrap + bumps + dashboard + alerts | merged | #107-#115 |

**Phase 5 code inventory (all merged):**

- #107 `verify_v2_deployment.py` — static audit of every Phase 1-5 deliverable
- #108 `alerts.py` — dedupable per-event issue opener for all 8 alert types
- #109 `reusable-bootstrap-repo.yml` + `bootstrap_repo.py` — shadow→absolute promotion primitives
- #110 `bumps.py` + `profiles/bumps/2026-04-23-node-24.yml` — recipe schema + canary
- #111 `admin_dashboard_pages.py` — coverage / drift / audit pages + private-repo redaction
- #112 `alert_triggers.py` — 7 pure detector functions (one per remaining alert type)
- #113 Self-governance profile: platform at `mode.phase: absolute`, full severity map
- #114 `alert_dispatch.py` — detector → gh-issue glue
- #115 `reusable-bumps.yml` + `bump_rollout.py` — rollout planner + workflow

**Operational work remaining for `QZP_V2_FULLY_SHIPPED_AND_VERIFIED`:**

- [ ] First drift-sync wave across 15 governed repos
- [ ] event-link per-flag Codecov verification (PR #129 still pending)
- [ ] All 15 governed repos on main with green quality rollup
- [ ] Dashboard actually deployed to GitHub Pages
- [ ] Scheduled alert dispatcher + secrets-sync workflow wiring
- [ ] Zero open `alert:*` issues on the platform repo

`scripts/quality/verify_v2_deployment.py --all` reports 39/39 required + optional artifacts present with zero warnings as of 2026-04-23.

### Phase 1 — schema v2 + fleet inventory (1-2 days)
- Add profile schema v2 loader (backwards-compat: `version: 1` profiles still read).
- `scripts/quality/fleet_inventory.py` + `alert:repo-not-profiled` opener.
- No consumer-visible change yet.

### Phase 2 — Codecov flag fix + validator (1 day)
- Rewrite `reusable-codecov-analytics.yml` to loop-per-flag.
- Add `validate_codecov_flags.py`.
- Seed every v1 profile's inputs with `flag:` (migration script infers from path).
- **Unblocks:** event-link Codecov correctness + any other repo with multi-flag coverage.

### Phase 3 — templates + drift-sync (3-5 days)
- Author `profiles/templates/stack/fullstack-web/` from event-link's current-known-good state.
- Author `profiles/templates/stack/go/` from whichever Go repo is most advanced.
- `reusable-drift-sync.yml` + marked-region parser.
- First sync wave: open PR on each governed repo with the rendered templates (expect conflicts; manual resolution on first run).

### Phase 4 — severity map + break-glass + known-issues (2-3 days)
- Rewrite `build_quality_rollup.py` to consume `scanners.*.severity`.
- Implement `quality-zero:break-glass` + `quality-zero:skip` label handlers.
- Seed `known-issues/QZ-FP-001..003` + `QZ-CV-001` from event-link lessons.
- Wire QRv2 to load known-issues entries into its Codex prompt.

### Phase 5 — bootstrap, bumps, dashboard, alerts (3-4 days)
- `reusable-bootstrap-repo.yml` + shadow-mode lifecycle.
- `reusable-bumps.yml` + staging/full-rollout.
- Dashboard pages (heatmap, coverage trend, drift list, audit feed).
- Alert issue openers — **per-event only, no digest** (revised from the initial design to match §8's per-event contract).

Total: ~10-15 working days for a single owner. Most of the value lands after Phase 3.

---

## 10. Round 5 decisions (all locked)

### 10.1 Scanners vs stacks — simplification
Drop the `enabled_scanners` / `scanners.*.enabled` concept entirely. **Every scanner always runs** against every governed repo; repos with no matching code auto-pass (no Go code → `DeepSource: Go` passes trivially). The `scanners:` map in the profile keeps only `severity:` (block/warn/info) — no `enabled:` field.

Stacks stay, but their purpose shrinks to exactly two things:
1. Which commands the platform runs to produce coverage + run tests (`pytest` vs `go test` vs `cargo test` vs `xcodebuild`).
2. Which templates get rendered in the consumer repo (Python CI vs Go CI vs Rust CI; `.codacy.yml` ignore paths specific to the stack).

### 10.2 Stacks to add
- `python-only` — pytest + coverage, no frontend. Splits out of `fullstack-web`.
- `react-vite-vitest` — vitest + v8 coverage + eslint + prettier. Splits out of `fullstack-web`.
- `fullstack-web` becomes a **composition**: a repo profile can declare `stacks: [python-fastapi, react-vite-vitest]` and templates get merged.
- `rust` — cargo + llvm-cov/tarpaulin + clippy + rustfmt.
- `swift` — xcodebuild + swiftformat + swiftlint + xcov.
- Existing `cpp-cmake`, `go`, `dotnet-wpf`, `gradle-java` unchanged.

### 10.3 QRv2 scope
**Everything, including security** — QRv2 attempts all finding classes. But with one safeguard: **security-class fixes (Dependabot, Snyk, Socket security alerts, CodeQL/Semgrep with CWE tag) always open a PR and never auto-merge**, regardless of whether non-security fixes are configured for auto-merge. Human approval required on the security patch before it reaches main.

```yaml
# QRv2 config
auto_merge_classes:
  - style             # prettier, black, eslint-fix
  - complexity        # Codacy complexity
  - unused_imports
  - duplication
  - code_smell        # Sonar non-bug
require_review_classes:
  - security          # anything with CWE tag
  - dependency_bump   # Dependabot + Snyk
  - unknown           # not in known-issues registry
```

### 10.4 Secrets distribution
**Repo-level secrets + sync workflow** with these safeguards:
- Manager GitHub PAT is **fine-grained**, scoped only to `repo.secrets:write` on the fleet's owner (Prekzursil).
- PAT rotated quarterly; rotation runs as a reusable workflow that generates a new PAT, updates all stored copies, and invalidates the old one.
- Every secret-write appends to `audit/secrets-sync.jsonl` with `{repo, secret_name_hash, timestamp, actor}`. Value is never logged.
- `check_quality_secrets.py` runs in Quality Rollup and fails if any `scanners.*.severity == block` scanner lacks its secret on the repo. Opens `alert:secret-missing`.
- Leak detection: if the manager PAT appears in any repo's code (Gitleaks/TruffleHog in the preflight step), the PAT is auto-revoked and an `alert:manager-pat-leak` issue opens.

### 10.5 Platform governs itself
**Full self-governance** — `profiles/repos/quality-zero-platform.yml` uses `mode.phase: absolute` with every scanner at `severity: block`, same as any other repo. Stack: `python-tooling` (new stack preset — mostly `scripts/quality/*.py` + pytest). Drift-sync workflow opens PRs against itself.

### 10.6 Alerting cadence
**Per-event issues only — no digest at all.** Whenever an alert event fires, the platform opens (or de-dupes against an existing) issue with the appropriate `alert:*` label. No scheduled roll-up, no weekly summary, no batch email. The dashboard's audit feed is the only aggregated view.

### 10.7 Dashboard hosting
**GitHub Pages on quality-zero-platform (public)** at `https://prekzursil.github.io/quality-zero-platform/`. Private-repo metrics (currently just `pbinfo-get-unsolved`) are **redacted** from the public view — row shows the slug but metrics are blanked. A separate markdown artifact (`dashboards/private-latest.md`) committed to the platform repo holds the full private data, viewable only by repo collaborators.

---

## 11. Profile schema — final shape (v2.1)

Applying Round 5 simplifications:

```yaml
slug: Prekzursil/event-link
stacks: [python-fastapi, react-vite-vitest]   # composition allowed
version: 2

mode:
  phase: absolute                 # shadow | ratchet | absolute
  shadow_until: null
  ratchet:
    baseline: { coverage_overall: 100.0, coverage_per_flag: { backend: 100, ui: 100 }, findings: { codacy_issues: 0 } }
    target_date: 2026-06-30
    escalation_date: 2026-09-30

coverage:
  min_percent: 100.0
  branch_min_percent: 100.0
  inputs:
    - { name: backend,       flag: backend, path: backend/coverage.xml, format: xml, min_percent: 100 }
    - { name: frontend,      flag: ui,      path: ui/coverage/cobertura-coverage.xml, format: xml, min_percent: 100 }
    - { name: frontend-lcov, flag: ui,      path: ui/coverage/lcov.info, format: lcov }

scanners:                        # severity only — no enabled flag
  codeql:                 { severity: block }
  dependabot:             { severity: block }
  sonarcloud:             { severity: block }
  codacy_issues:          { severity: block }
  codacy_complexity:      { severity: block }
  codacy_clones:          { severity: block }
  deepscan:               { severity: block }
  deepsource_visible:     { severity: block }
  semgrep:                { severity: block }
  sentry:                 { severity: block }
  socket_pr_alerts:       { severity: block }
  socket_project_report:  { severity: info  }
  qlty_check:             { severity: block }

overrides: []
vendors: { sonar: { project_key: "${sonar_project_key}"  # e.g. <owner>_<repo-slug> }, ... }
dependabot: { updates: [ ... ] }
```

Auto-generated from this profile:
- `required_contexts` (from scanners with severity=block)
- Consumer's `ci.yml`, `.codacy.yml`, `codecov.yml`, `vite.config.ts` coverage block
- Branch-protection rules (via `reusable-ruleset-sync.yml`)

---

## 12. Ready to implement

All decisions locked. Phase 1 can start whenever you give the go. Suggested order to minimize risk:

| Phase | Duration | Deliverable | Unblocks |
|---|---|---|---|
| 1 | 1-2 days | Schema v2 loader + fleet inventory | All downstream phases |
| 2 | 1 day | **Codecov flag-split fix + validator** | Real 100% reporting on all multi-flag repos |
| 3 | 3-5 days | Templates + drift-sync workflow | Canonical config across fleet |
| 4 | 2-3 days | Severity rollup + break-glass/skip labels + known-issues registry | Humane gate ergonomics + QRv2 autonomy |
| 5 | 3-4 days | Bootstrap + bumps + dashboard + per-event alerts | Fleet-wide ops + visibility |

Phase 2 is highest-leverage for the short term — it fixes the silent-drop bug that let event-link report 58% even with correct configs.
