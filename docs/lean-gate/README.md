# Lean 6-gate thin template

This directory is the **additive lean-gate thin template** adopted by the
lean 6-gate charter (decided 2026-06-16; supersedes the 10-SaaS QZP model).
It is the multi-language generalization of the **proven** lean gate already
running in [`Prekzursil/Reframe`](https://github.com/Prekzursil/Reframe)
(`.github/workflows/quality.yml`) — same tool pins, same single-job binary
green/red model.

> **Additive only.** Adding this template does **not** remove the existing
> fleet reusable workflows. The 13 enrolled repos still call the old SaaS
> control-plane workflows; the ~80% deletion of that machinery is **deferred
> to the migration runbook** until the fleet migrates off it. This template
> also does **not** flip any required-status-check / branch-protection /
> ruleset — that is also deferred to the runbook (required ⊆ enabled, or the
> branch goes permanently red).

## What it is

| File | Role |
|------|------|
| `../../.github/workflows/reusable-quality.yml` | ONE `workflow_call` reusable workflow, single job `quality`, auto-detects the caller's languages and runs only the relevant lean lanes. |
| `pre-commit-config.template.yaml` | The gate-1/gate-5 autofix lane (copy to the caller as `.pre-commit-config.yaml`, keep the hooks for languages present). |
| `../../.quality/charter.yml` | The single source of truth for the closed 6-gate set + the banned (deleted) gates. |
| `../../.quality/charter_check.sh` | Pure-bash check that FAILS CI if the active gate set drifts from the charter. |

## The closed 6-gate charter

Model: **Prevent (auto-fix) - Binary green/red - Ratchet-retired (100% everywhere)**.

1. **Lint + format + imports + sec-lint** (AUTOFIX): Ruff [py] - Oxlint+Oxfmt [ts/js] - clippy+rustfmt [rust] - golangci-lint v2 [go] - ErrorProne+Spotless [java] - Roslyn+dotnet-format [c#] - clang-tidy+clang-format [c/c++].
2. **Types**: `tsc --noEmit` [ts] - basedpyright [py].
3. **Tests + coverage**: STRICT **100% line+branch** (standing user policy; ratchet retired). Reasoned, greppable pragma only. **No silent-pass** — a language with source *and* test files but no coverage config/script **FAILS** (the 100% gate cannot pass unmeasured); the only skip is the narrow *no-test-surface-by-design* case (source present with **zero** test files).
4. **SAST**: Opengrep (Semgrep CE acceptable), small pinned in-repo ruleset (`.quality/opengrep`).
5. **Secrets**: gitleaks (pinned + allowlist) + push-protection.
6. **Deps**: osv-scanner + Dependabot (**no** Renovate).

**Deleted as gates** (must NOT reappear): Sonar, Codacy, DeepSource, DeepScan,
qlty, Codecov-as-gate, Snyk, Applitools, Chromatic, Percy, Sentry-as-gate,
Pylint, standalone Bandit, ESLint-classic, Biome, Trivy-for-app-deps.
**CodeQL** only nightly, on PUBLIC repos.

## Adopting it (~5 lines in the caller repo)

```yaml
# .github/workflows/quality.yml in the CALLER repo
name: quality
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
permissions: { contents: read }
jobs:
  quality:
    uses: Prekzursil/quality-zero-platform/.github/workflows/reusable-quality.yml@main
```

Then add the per-language config the gates read:

- `.pre-commit-config.yaml` — copy `pre-commit-config.template.yaml`, keep the
  language hooks you need (gate 1 + gate 5).
- `.quality/opengrep/*.yaml` — the curated SAST ruleset (gate 4); see the
  Reframe `.quality/opengrep/README.md` for the curated subset model.
- `.gitleaks.toml` — secrets allowlist (gate 5).
- `osv-scanner.toml` + Dependabot config — deps (gate 6).
- `.quality/charter.yml` + `.quality/charter_check.sh` — the drift guard.

Language auto-detection means a caller with no Python skips the Python lanes,
a caller with no JS/TS skips `tsc`, and so on — each lane is guarded by an
`if:` keyed off the detected language / present config.

### Robustness on edge-shaped callers

The detection step is hardened so toolchain setup and the deps/types lanes never
hard-fail *before* a gate can run:

- **Dep-less Python repos** — the `pip` cache is enabled only when a resolvable
  Python manifest (`requirements*.txt` / `pyproject.toml` / `setup.py` /
  `setup.cfg` / `Pipfile`) exists. Without one, `setup-python` runs **without**
  the cache instead of erroring on "no file matched".
- **Zero-dependency repos** — `osv-scanner` is invoked only when at least one
  scannable manifest/lockfile exists; otherwise the deps gate **passes**
  (nothing to scan) rather than exiting 128 ("no package sources found").
- **Monorepo / nested TypeScript** — `tsc` runs per `tsconfig.json`: the root
  config if present, otherwise every discovered nested config (e.g. a `ui/`
  subproject). A repo with nested-only configs is checked, not hard-failed.
- **Tests that import a runtime dependency** — gate 3 installs the caller's
  Python **runtime** deps before `pytest`/`coverage`, in priority order:
  `requirements.txt` / `requirements/*.txt` / `requirements*.txt`
  (`pip install -r`, dev/test/lint/doc/ci-only requirements excluded), then
  `pyproject.toml` / `setup.cfg` / `setup.py` (`pip install .`, which pulls the
  project's declared deps incl. Poetry's `[tool.poetry.dependencies]`). With no
  manifest the repo is treated as pure-stdlib and the lane proceeds. This closes
  the `ModuleNotFoundError` red on repos whose tests import a runtime dependency
  (observed: `env-inspector` → `No module named defusedxml`, when the lane had
  installed only `pytest`/`pytest-cov`). The JS/TS test lane already runs
  `npm ci` (or `npm install` if no lockfile) before any vitest/jest coverage so
  `node_modules` exists. The strict `--cov-fail-under=100` / 100% gate is
  unchanged.
- **Caller-local frontend pre-commit hook (`eslint: not found`)** — gate 1's
  ts/js lint+format is **self-contained in this workflow**: a dedicated
  `gate-lint-format-jsts` step runs the workflow's OWN pinned **oxlint 1.69.0**
  + **oxfmt 0.55.0** over the caller's ts/js (the Reframe pattern), independent
  of the caller's `.pre-commit-config.yaml`. A caller repo-local
  `frontend-eslint`-style hook that shells out to a not-yet-installed tool
  (`sh: 1: eslint: not found`, exit 127) can therefore no longer abort the lean
  ts/js gate (observed: `momentstudio`). The `pre-commit run --all-files` step is
  additionally hardened: when a JS/TS manifest is present it runs `npm ci` first
  so legitimate caller-local hooks resolve their tool from `node_modules`, and
  any still-unresolvable ad-hoc frontend hooks are `SKIP`-ed
  (`SKIP=frontend-eslint,eslint,…,prettier,tsc`) — those are the caller's ad-hoc
  lanes, not the lean charter's gate-1; ruff, gitleaks, and the rest of the
  charter hooks still run and still gate. **Chosen approach: (b) self-contained
  oxlint/oxfmt run directly + (a) `npm ci` before pre-commit** — both, so the
  lean gate is correct whether or not the caller's local hooks resolve.

## Charter drift guard

`bash .quality/charter_check.sh` (run by the `charter-check` step in the
reusable workflow) FAILS if:

- any charter gate's `workflow_step` is **missing** from the reusable workflow, or
- any **banned** gate (sonar/codacy/.../renovate) is wired as a `gate-*` step, or
- CodeQL is wired as a gate (it is nightly/public-only).

Keep the pins in `pre-commit-config.template.yaml`, `reusable-quality.yml`, and
`.quality/charter.yml` in sync when bumping a tool version.
