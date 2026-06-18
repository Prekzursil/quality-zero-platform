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
| `biome.template.json` | The bundled minimal Biome formatter config (the same style as Reframe's `biome.json`). A JS/TS caller may copy it to `biome.json`; if absent, the reusable workflow falls back to this config in-process for the gate-1 format check. |
| `../../.quality/charter.yml` | The single source of truth for the closed 6-gate set + the banned (deleted) gates. |
| `../../.quality/charter_check.sh` | Pure-bash check that FAILS CI if the active gate set drifts from the charter. |

## The closed 6-gate charter

Model: **Prevent (auto-fix) - Binary green/red - Ratchet-retired (100% everywhere)**.

1. **Lint + format + imports + sec-lint** (AUTOFIX): Ruff [py] - Oxlint+Biome [ts/js] (Oxlint = linter, Biome = formatter-only) - clippy+rustfmt [rust] - golangci-lint v2 [go] - ErrorProne+Spotless [java] - Roslyn+dotnet-format [c#] - clang-tidy+clang-format [c/c++].
2. **Types**: `tsc --noEmit` [ts] - basedpyright [py].
3. **Tests + coverage**: STRICT **100% line+branch** (standing user policy; ratchet retired). Reasoned, greppable pragma only. **No silent-pass** — a language with source *and* test files but no coverage config/script **FAILS** (the 100% gate cannot pass unmeasured); the only skip is the narrow *no-test-surface-by-design* case (source present with **zero** test files).
4. **SAST**: Opengrep (Semgrep CE acceptable), small pinned in-repo ruleset (`.quality/opengrep`).
5. **Secrets**: gitleaks (pinned + allowlist) + push-protection.
6. **Deps**: osv-scanner + Dependabot (**no** Renovate).

**Deleted as gates** (must NOT reappear): Sonar, Codacy, DeepSource, DeepScan,
qlty, Codecov-as-gate, Snyk, Applitools, Chromatic, Percy, Sentry-as-gate,
Pylint, standalone Bandit, ESLint-classic, Trivy-for-app-deps.
**CodeQL** only nightly, on PUBLIC repos. (Biome is **not** banned — it is the
ts/js *formatter* in gate 1, mirroring Reframe; round-3 swapped it in for Oxfmt.)

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
- **Non-standard Python manifests (round-3 FIX 3)** — even *with* a manifest,
  `actions/setup-python`'s `cache: pip` default hash glob is only
  `**/requirements.txt` **or** `**/pyproject.toml`. A repo whose sole manifest is
  non-standard (`requirements-dev.txt`, `requirements/*.txt`) matched **neither**,
  so `setup-python` still hard-failed *"No file matched to [\*\*/requirements.txt
  or \*\*/pyproject.toml]"* before any gate ran (observed: `codeblocks`). The
  cached setup now passes `cache-dependency-path` set to the **actual detected
  manifest paths** (incl. `requirements-dev.txt` / `requirements/*.txt`), so the
  cache key always hashes a real file and `setup-python` never hard-fails.
- **Zero-dependency repos (round-3 FIX 4)** — `osv-scanner` is invoked only when
  at least one scannable manifest/lockfile is detected; **and** when it *is* run,
  its own exit `128` ("no package sources found") is mapped to **PASS**. The
  round-1 pre-check alone was insufficient: a repo can match a manifest glob yet
  still have nothing osv recognizes as a scannable source, so the scanner exited
  128 and red the gate (observed: `star-wars`, a zero-dependency repo). "Nothing
  to scan" is not a failure; any **other** non-zero exit (e.g. vulnerabilities
  found) still fails the gate.
- **Monorepo / nested TypeScript** — `tsc` runs per `tsconfig.json`: the root
  config if present, otherwise every discovered nested config (e.g. a `ui/`
  subproject). A repo with nested-only configs is checked, not hard-failed.
- **Monorepo / nested JS-TS coverage (round-3 FIX 5)** — the gate-3 JS/TS
  coverage lane likewise DISCOVERS nested `package.json` projects (mirroring the
  `tsc` lane): root `package.json` if present, otherwise every nested
  `package.json` (excluding vendored trees), running coverage in the project dir
  that owns the `test:coverage` / `vitest|jest --coverage` script. A nested-only
  project (root has no `package.json`, the project lives in e.g.
  `frontend/<app>/`) is now measured instead of hard-failing "no coverage script"
  (observed: `webcoder`). The **silent-pass hole stays closed**: a project with
  test files but no coverage runner still **FAILS**; the only skip is the
  documented *no-test-surface-by-design* case (zero ts/js test files). 100% is
  unchanged.
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
  (linter) + **biome 2.5.0** (formatter-only) over the caller's ts/js (the
  Reframe pattern), independent of the caller's `.pre-commit-config.yaml`. A
  caller repo-local `frontend-eslint`-style hook that shells out to a
  not-yet-installed tool (`sh: 1: eslint: not found`, exit 127) can therefore no
  longer abort the lean ts/js gate (observed: `momentstudio`). The
  `pre-commit run --all-files` step is additionally hardened: when a JS/TS
  manifest is present it runs `npm ci` first so legitimate caller-local hooks
  resolve their tool from `node_modules`, and any still-unresolvable ad-hoc
  frontend hooks are `SKIP`-ed (`SKIP=frontend-eslint,eslint,…,prettier,tsc`) —
  those are the caller's ad-hoc lanes, not the lean charter's gate-1; ruff,
  gitleaks, and the rest of the charter hooks still run and still gate. **Chosen
  approach: (b) self-contained oxlint/biome run directly + (a) `npm ci` before
  pre-commit** — both, so the lean gate is correct whether or not the caller's
  local hooks resolve.
  - **No first-party lintable JS (round-4 FIX).** oxlint runs with
    `--no-error-on-unmatched-pattern` so a caller whose JS is entirely
    ignored/vendored (empty match set) PASSes (exit 0) instead of failing with
    `No files found to lint` (exit 1). This does **not** weaken the gate: when
    lintable JS exists, `--deny-warnings` still runs and still fails on any
    warning. The oxlint ignore set also excludes `**/*.min.js` (alongside
    `node_modules`/`dist`/`build`/`out`/`.venv`/`vendor`) so obvious minified
    build artifacts are never linted — real source is **not** broadly ignored.
  - **Formatter = Biome (round-3 FIX 1).** The CI format check runs
    `biome ci --formatter-enabled=true --linter-enabled=false --assist-enabled=false`
    (check-only, no writes). It honors a caller-shipped `biome.json` / `biome.jsonc`
    if present; otherwise it uses the bundled `docs/lean-gate/biome.template.json`
    config (in-process via `--config-path`) so formatting is deterministic across
    callers — 2-space indent, single quotes, `trailingCommas: all`,
    `semicolons: always`, `lineWidth 100`, `lf`, mirroring Reframe's `biome.json`.
    The local pre-commit hook (`biomejs/pre-commit@v2.5.0` `biome-format`) autofixes
    on commit so local and CI agree. (Earlier rounds used Oxfmt 0.55.0; round-3
    swapped to Biome to match the proven Reframe gate.)

## Charter drift guard

`bash .quality/charter_check.sh` (run by the `charter-check` step in the
reusable workflow) FAILS if:

- any charter gate's `workflow_step` is **missing** from the reusable workflow, or
- any **banned** gate (sonar/codacy/.../renovate) is wired as a `gate-*` step, or
- CodeQL is wired as a gate (it is nightly/public-only).

Keep the pins in `pre-commit-config.template.yaml`, `reusable-quality.yml`, and
`.quality/charter.yml` in sync when bumping a tool version.
