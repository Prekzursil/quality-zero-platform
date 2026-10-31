# BEST-CONFIG — the fleet standard

**Status:** charter document (2026-10-31)
**Derived from:** evidence in this repo + the live fleet audit
([`qzp-fleet-audit.md` snapshot, 2026-10-31](#evidence-base)) + the live
configuration of the strongest members. Not taste: every element below cites
where it already runs green, or the gap it closes.

This document is **the** target configuration for every governed repository.
It supersedes nothing that is green; it is the synthesis of
[`FLEET-BASELINE.md`](FLEET-BASELINE.md) (files + settings),
[`.quality/charter.yml`](../.quality/charter.yml) (the closed 6-gate set),
[`.quality/tool-pins.yml`](../.quality/tool-pins.yml) (exact versions), and
the per-repo profile shapes of the members already at zero.

**Standing invariants (never traded away):**

- `deps-severity-floor` stays **`any`** (zero tolerance). The `t0` floor is a
  legacy default, not the standard.
- Coverage stays **STRICT 100% line AND branch** on the declared scope.
- CodeQL query suites stay **`extended`** (`security-and-quality` class), threat
  model `remote_and_local`.
- Only ever ENABLE a stricter check or fix configuration. Never disable a
  security feature to make a branch green.
- Never force-merge a red PR: fix it, or backlog it with the exact gap named.
- Alerts (dependabot / code-scanning / secret-scanning) are resolved by FIXING
  (dependency-bump PRs, code fixes) — never auto-dismissed or closed.
- Forks/mirrors of upstream projects are **never modified** (our CI on them
  would fight upstream syncs); archived repos are report-only.

---

## 1. The core: lean 6-gate, pinned by SHA

Every governed repo calls
[`reusable-quality.yml`](../.github/workflows/reusable-quality.yml) from its
own `.github/workflows/quality.yml`, **pinned to a FULL 40-hex commit SHA**
(a branch or movable tag lets one platform push change every caller's gate at
once). Language auto-detection skips lanes with no matching source.

| # | Gate | Tooling (exact pin, from [`.quality/tool-pins.yml`](../.quality/tool-pins.yml)) |
|---|------|----------------------------------------------------------------------------------|
| 1 | Lint + format | **ruff 0.15.17** (`ruff check` + `ruff format --check`) [py] · **oxlint 1.69.0** (linter) + **biome 2.5.15** (formatter-only) [ts/js]. Self-contained in CI — the workflow's own pinned CLIs, never the caller's pre-commit. |
| 2 | Types | **basedpyright 1.39.8** at `typeCheckingMode: standard` [py] (honors a caller config when shipped) · `tsc --noEmit` per tsconfig [ts]. |
| 3 | Tests + coverage | **pytest 9.1.1 / pytest-cov 7.1.0 / coverage 7.16.2** [py] · per-project `test:coverage` (vitest/jest) [ts/js] · caller `coverage-gate.sh` [go/c++]. **STRICT 100% line + branch** on the declared scope. |
| 4 | SAST | **opengrep v1.32.0** — ALWAYS runs; the caller's curated `.quality/opengrep` ruleset when present, else the platform default embedded in the workflow. (Semgrep CE acceptable.) |
| 5 | Secrets | **gitleaks 8.30.1** — ALWAYS runs (allowlist presence must never be the trigger); `.gitleaks.toml` / `.gitleaksignore` are optional reviewed allowlists only. |
| 6 | Deps | **osv-scanner v2.3.8** + Dependabot (no Renovate) — **`deps-severity-floor: any`**: every finding blocks, unscored and `UNKNOWN`-severity advisories included. `osv-scanner.toml` carries only reviewed, dated, reasoned ignores. |

Every pinned binary is verified against its SHA-256 release-asset digest
(`asset_sha256` in the pins file) before it runs — nothing installs on a
mismatch.

Gate-3 anti-silent-pass rules (load-bearing, from
[`docs/lean-gate/README.md`](lean-gate/README.md)):

- A language with source **and** test files but no coverage config/script
  FAILS — the 100% gate cannot pass unmeasured.
- The only skip is *no-test-surface-by-design* (source present, zero test
  files). Fixture/test-data trees are inputs, never projects.
- Each coverage lane clears its project's `coverage/` first; a committed or
  cached report can never stand in for the run.
- Exclusions from the measured scope are **declared** (e.g. in
  `CONTRIBUTING.md`) with a reasoned, greppable pragma only. Generated or
  archived trees are excluded by name, never rewritten to satisfy a linter.

Local/CI parity: `scripts/verify` in the consumer runs the platform preflight
at the exact pinned commit, in a dedicated clone — a local pass means what a
CI pass means.

## 2. CodeQL — belt and braces (public repos)

CodeQL is **not** one of the 6 lean gates (the charter-check fails if it is
wired as a `gate-*` step); it is the security baseline layer that runs
alongside them, twice over:

1. **Advanced wrapper, pinned to SHA.** A `.github/workflows/codeql.yml` that
   calls
   `Prekzursil/quality-zero-platform/.github/workflows/reusable-codeql.yml@<full-40-hex-sha>`
   on `push` + `pull_request` + a weekly off-peak cron. The wrapper resolves
   the repo's profile, fail-closes unless the analysis succeeds, and checks
   the platform out at the workflow's own commit (`job.workflow_sha`), so a
   caller can never pick or drift from the scripts it runs. This is the shape
   running in **env-inspector** (pinned at `b4fbe38470fd2831f86f194df5b638a271bae8b8`).
   A repo that needs richer control runs its own in-repo advanced wrapper
   (**llm-anthology**: per-language matrix `python` + `javascript-typescript`,
   `queries: security-and-quality`, weekly off-peak cron) — in that form,
   SHA-pin the `github/codeql-action` steps as well, like the platform's own
   `codeql.yml` does for every action it uses.
2. **GitHub default setup, extended.** Default setup configured with the
   **extended** query suite and the `remote_and_local` threat model
   ([`FLEET-BASELINE.md` § Repository security settings](FLEET-BASELINE.md)).

3. **Merge protection.** Once the repo is green, its ruleset carries
   code-scanning merge protection for tool `CodeQL` at thresholds
   `alerts_threshold: all` / `security_alerts_threshold: all` (from
   [`profiles/stacks/quality-zero-phase1-common.yml`](../profiles/stacks/quality-zero-phase1-common.yml)):
   a PR that introduces a CodeQL alert of ANY severity is blocked, not only
   high/critical.

Public repos only: private repositories owned by a personal account cannot
use GHAS — CodeQL, secret scanning and private vulnerability reporting are
unavailable there. Their security policy routes reports to a labelled issue;
the lean gate's gitleaks + opengrep lanes are their secret-scanning and
code-scanning controls (see [`FLEET-BASELINE.md`](FLEET-BASELINE.md)).

## 3. Secret scanning — three layers

1. **GitHub secret scanning + push protection: enabled** (`PATCH
   /repos/{repo}` `security_and_analysis`) on every public repo — blocks
   known-provider secret patterns at push time.
2. **gitleaks (gate 5) for non-provider / generic patterns**: always-on in CI
   over the whole tree, with `--redact`. Push protection only knows
   provider-backed patterns; gitleaks' generic rules catch
   non-provider-shaped secrets (internal tokens, bespoke keys) that push
   protection cannot. Findings are fixed or triaged into a **reviewed**
   `.gitleaksignore` (the platform's own carries "findings triaged as FAKE
   test data (reviewed)" with a review date) — never blanket-muted.
3. **Leak response**: a leaked credential is revoked first, then cleaned;
   history rewriting is not a substitute for rotation.

Alongside: private vulnerability reporting **enabled**, Dependabot alerts
**enabled**, Dependabot security updates **enabled**.

## 4. Dependabot — weekly, grouped, majors never ignored

One rail per manifest tree, for **every ecosystem in the checkout plus
`github-actions`** (a `github-actions` rail ships with the first workflow —
a caller created in the same run counts). Weekly cadence. Then:

- **Security updates grouped per ecosystem** — one PR can clear every open
  advisory.
- **Minor + patch updates grouped.**
- **Majors are never ignored**; each arrives as its own PR. Under a
  zero-tolerance dependency gate, a fix that only ships in a new major has to
  be able to arrive. (This deliberately supersedes the legacy renderer
  `profiles/templates/common/dependabot.yml.j2`, which ignores semver-major —
  that template is retained only for not-yet-migrated SaaS-era consumers.)
- A manifest tree without an update rail, or an `ignore` of semver-major
  updates, is reported as a **gap** by the baseline audit.

## 5. Community files — the full set

Every governed repo carries (template-rendered by
[`scripts/quality/fleet_baseline.py`](../scripts/quality/fleet_baseline.py),
created-when-missing, self-written policies kept):

| File | Role |
|------|------|
| `LICENSE` | The account's most common community gap in the 2026-10-31 audit — a product repo without a license is unfinished, not minimal. |
| `SECURITY.md` | Private vulnerability-reporting channel; routes upstream bugs upstream on forks. |
| `CONTRIBUTING.md` | Lists the lean gates for the languages actually present + local commands. Declares measured coverage scope and documented omissions. |
| `CODEOWNERS` | Review routing; `alert:*` auto-assignment. |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1. |
| `.github/PULL_REQUEST_TEMPLATE.md` | A gate checklist mapping 1:1 to CI. |
| `.github/ISSUE_TEMPLATE/*.yml` | Bug + feature forms; `config.yml` links security reports to the private channel. |
| `AGENTS.md` | Contributor rules for humans and automation: verify before claiming done, never weaken a gate, tests ship with the change, never push to the default branch. |
| `scripts/verify` | The canonical local entry point (preflight at the pinned commit). |
| `.github/workflows/quality.yml` | The lean-gate caller, SHA-pinned, `deps-severity-floor: any`. |

Files are never overwritten unless byte-identical platform boilerplate; every
change reaches a fleet repo through a PR, never a push to the default branch.

## 6. Coverage reporting — codecov 100/0 when a token exists

Codecov is **analytics, never a gate** (codecov-as-gate is on the charter's
banned list). Where a `CODECOV_TOKEN` exists, the repo ships a `codecov.yml`
rendered from [`profiles/templates/common/codecov.yml.j2`](../profiles/templates/common/codecov.yml.j2):

```yaml
coverage:
  status:
    project:
      default: { target: 100%, threshold: 0% }
    patch:
      default: { target: 100%, threshold: 0% }
```

Per-flag uploads for multi-input repos (one upload per declared
`coverage.inputs[].flag`; the flag-split bug that let a repo report 58% while
configured for 100% is fixed by the per-flag lane + validator). No token → no
codecov lane, and the 100% line+branch measure is still enforced in-repo by
gate 3. Coverage is reported honestly as `N/total = X%, resumable` — never
inflated.

## 7. Rulesets — required contexts are the repo's OWN checks

Branch protection via the generated ruleset
(`generated/rulesets/<repo>.json`, applied by `reusable-ruleset-sync.yml`):

- **Required = exactly what the repo emits on `pull_request`.** Measured, not
  assumed — `scripts/quality/validate_required_contexts.py` compares each
  profile's `required_contexts` against the check-runs actually reported on
  main/PR-head and fails on any phantom context that would strand a branch
  permanently red.
- `required_contexts_mode: replace` (the platform owns the whole list) unless
  non-platform CI must also stay required.
- Nothing that cannot report on a `pull_request` may be required
  (push-only/Pages/dynamic jobs stay unrequired).
- A repo with its own fail-closed aggregate (e.g. fractal-darkroom's
  `ci-green`, `if: always()` + every `needs.*.result == success`) requires
  the aggregate instead of brittle matrix-derived names.
- Renaming a required context (e.g. moving CodeQL to an advanced wrapper)
  updates protection **in the same change window**.

The strong shapes, as measured on 2026-10-24 and re-confirmed green in the
2026-10-31 audit:

| Repo | Required contexts (always) |
|------|----------------------------|
| env-inspector | `quality / quality`, `CodeQL` |
| fractal-darkroom | `ci-green`, `quality / quality`, `CodeQL` |
| tss / tsis-agent-evaluation | own `verify (<os>, <py>)` matrix (4 cells), `quality / quality`, `CodeQL / codeql` |
| llm-anthology | own `tests` matrix (7 cells), packaged-artifact smoke jobs, `analyze (python)` + `analyze (javascript-typescript)`, cockpit jobs |

- **Enforcement order (never deadlocks a repo):** (1) governance-files PR
  merges on existing-green CI; (2) the lean-gate caller PR stays open until
  every gate is green, with remediation landing on that branch; (3) only once
  the default branch is green with the caller is the ruleset applied — open a
  test PR first and confirm every required context reports on it.
- Ruleset bypass: repository admins may merge past a red check **only through
  a pull request** (audited); nobody pushes past the ruleset directly.
- Coverage context choice: a repo whose own CI enforces 100% (pytest
  `--cov-branch --cov-fail-under=100`, vitest thresholds) declares
  `coverage.policy: repo_enforced` with an `evidence_note` — the repo's own
  contexts go red on a coverage regression, which the ruleset already
  requires. QZP collects no artifact it cannot gate honestly.

## 8. Evidence repos — deny-by-default `.gitignore`

Repos that exist to hold research evidence, generated reports, or archived
records (fractal-darkroom-research is the live example) invert the ignore:
**everything is ignored by default, and only curated sources are negated
in**:

```gitignore
# deny-by-default: nothing enters git unless explicitly allowed
/*
!/src/
!/paper/
!/experiments/*.py
!/experiments/data/MANIFEST.md
!/README.md
```

Conventions it enforces (observed live in
`fractal-darkroom-research/.gitignore` and in the platform's own
`.gitignore` rationale):

- Build outputs (LaTeX intermediates, compiled PDFs) are rebuilt by CI and
  uploaded as artifacts — never committed.
- Large data is **pointed to, never committed**: indexed with size + SHA-256
  in a committed `MANIFEST.md`; binaries (`*.npy`, `*.npz`, corpora) stay
  local.
- Generated lane reports (coverage dumps, zero-lane reports, rollups) never
  enter git — readers take them from CI artifacts; if a curated file must
  live in an ignored tree, add an explicit `!`-negation with a comment.
- Zero tolerance applies to code a repository **owns**; a byte-identical
  archive of an external record (e.g. fractal-darkroom's `research/` tree) is
  excluded by name from ruff/basedpyright/coverage and gated by its own CI
  job — it is never rewritten to satisfy a linter.

## 9. Per-stack variations

Stacks decide exactly two things (everything above is invariant): which
commands produce coverage + run tests, and which stack templates render.
From [`profiles/stacks/`](../profiles/stacks/):

| Stack | Coverage artifact | Notes |
|-------|-------------------|-------|
| `python-tooling` / `python-web` | `quality-artifacts/coverage/coverage.xml` (pytest-cov, `--cov-branch`, `fail_under = 100`) | The platform's own stack. |
| `python-desktop` | `coverage-xml` | GUI repos; `require_sources` pins the measured scope. |
| `node-frontend` | `quality-artifacts/coverage/lcov.info` | oxlint+biome+tsc; per-`package.json` project discovery for coverage (nested projects measured, workspace members never silently skipped). |
| `fullstack-web` | mixed `summary.json` | Composes backend + ui inputs, one flag each. |
| `go` | caller `scripts/coverage-gate.sh` | Go setup 1.22+; golangci-lint via local pre-commit. |
| `cpp-cmake` | lcov `coverage.info` | clang-tidy/clang-format local; compiled-language autofixers are pre-commit-only until a self-contained CI lane exists (round-6 state). |
| `dotnet-wpf` / `gradle-java` | stack runner | .NET 8 / temurin 17; Roslyn+dotnet-format, ErrorProne+Spotless local. |
| dual-language (llm-anthology shape) | per-language, enforced in-repo | Python matrix (3.9–3.13 across OSes) with pytest 100% line+branch AND js matrix (node 20/22) with vitest thresholds; CodeQL per-language matrix. |

Common setup pins (from the stack common profile): Python 3.12, Node 22 LTS
(GitHub Actions deprecates Node 20 actions; the forced default flips to Node
24 on 2026-06-02).

## 10. Documented exclusion classes

Membership is every non-archived, non-fork repo owned by the account.
Exclusions are explicit, verified (never guessed), and recorded with reasons:

| Class | Count (2026-10-31 audit) | Treatment |
|-------|--------------------------|-----------|
| **Forks** | 53 of 96 repos (21 confirmed `isFork=true` from the named task list; 30 more found by sweep) | Out of scope: a fork's CI and alert surface tracks its third-party upstream; large alert counts on forks (ToolUniverse 292 dependabot, ZHMModSDK 245 codeql) are upstream debt. Forks the owner has worked on carry the baseline's SECURITY/CONTRIBUTING **pointing upstream**. Two forks are deliberately enrolled as fixtures — `event-link` (fullstack overlay member), `pbinfo-get-unsolved` (dry-run fixture / portability source) — per `inventory/repos.yml` notes. |
| **Mirrors** | 0 found account-wide (`isMirror` sweep) | Same rule as forks: never modified — CI would fight the mirror source. Listed as excluded if any appear. |
| **Docs-only / content repos** | e.g. `security-kb` (no primary language detected), `.github` (org community-health meta, no code) | Not backlog candidates for the 6-gate (no code to gate); carry community files only; recorded, not silently dropped. |
| **Archived** | 0 today | Report-only when they appear; profile marks read-only. |
| **Evidence/archival trees** | e.g. fractal-darkroom `research/` | Excluded by name from lint/types/coverage; gated by their own CI job; never rewritten. |

## 11. What is deliberately NOT in the standard (banned as gates)

From [`.quality/charter.yml`](../.quality/charter.yml) — the charter-check
FAILS CI if any of these is wired as a gate step: sonar, codacy, deepsource,
deepscan, qlty, **codecov-as-gate**, snyk, applitools, chromatic, percy,
**sentry-as-gate**, pylint, standalone bandit, eslint-classic,
trivy-for-app-deps, **renovate**. Biome is not banned — it is the gate-1
ts/js formatter. The 10-SaaS model this replaces is archived on
`branch+tag archive/qzp-full-saas-control-plane`; its docs
([`QUALITY-GATES.md`](QUALITY-GATES.md),
[`STRICT-ZERO-CHECKLIST.md`](STRICT-ZERO-CHECKLIST.md),
[`QZP-V2-DESIGN.md`](QZP-V2-DESIGN.md)) remain as history.

## Evidence base

Everything above was read or run on 2026-10-31:

- `git clone --depth 1 https://github.com/Prekzursil/quality-zero-platform.git`
  → `docs/QUALITY-GATES.md`, `docs/STRICT-ZERO-CHECKLIST.md`,
  `docs/QZP-V2-DESIGN.md`, `docs/FLEET-BASELINE.md`,
  `docs/lean-gate/README.md`, `.quality/charter.yml`,
  `.quality/tool-pins.yml`, `.github/workflows/{reusable-quality,reusable-codeql,codeql,verify}.yml`,
  `profiles/stacks/*.yml`, `profiles/templates/common/{dependabot,codecov}.yml.j2`,
  `profiles/repos/{llm-anthology,env-inspector,fractal-darkroom,codeblocks-pretty-prints-stable,tss,tsis-agent-evaluation,quality-zero-platform}.yml`,
  `inventory/repos.yml`, `.gitignore`, `.gitleaksignore`.
- Fleet audit table `qzp-fleet-audit.md` (96 repos, per-repo
  latest-main/alerts/community columns, exclusion lists).
- Live `gh api` reads: `Prekzursil/llm-anthology` `.github/workflows/codeql.yml`
  (advanced wrapper, per-language matrix, `security-and-quality`),
  `Prekzursil/env-inspector` `.github/workflows/{codeql,quality}.yml` +
  workflow listing (SHA-pinned reusable-codeql caller),
  `Prekzursil/fractal-darkroom-research` `.gitignore` (evidence-repo
  conventions), `Prekzursil/tss` workflow listing.

## See also

- [`FLEET-BASELINE.md`](FLEET-BASELINE.md) — the executable baseline
  (`scripts/quality/fleet_baseline.py`) and enforcement order.
- [`docs/lean-gate/README.md`](lean-gate/README.md) — gate mechanics,
  edge-shaped-caller robustness, preflight, phantom-context validator.
- [`ONBOARDING.md`](ONBOARDING.md) — wiring a repo onto this standard.
