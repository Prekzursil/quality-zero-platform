# Fleet baseline — what every governed repository carries

quality-zero-platform (QZP) is the governor of the `Prekzursil` fleet. This
document is the policy of record for the files and settings every fleet
repository carries. Its executable half is
[`scripts/quality/fleet_baseline.py`](../scripts/quality/fleet_baseline.py),
which plans, renders and audits the files below from
[`templates/fleet/`](../templates/fleet/).

The best practices collected here come from the fleet itself. The
community-file shape, the security settings and the 1:1 gate checklist come
from `fractal-darkroom`. The lean gate is `reusable-quality.yml`. Grouped
Dependabot rails and the zero-tolerance dependency gate come from the
2026-08 CVE work (`docs/lean-gate/README.md`).

## Fleet membership

- Every non-archived, non-fork repository owned by `Prekzursil` is a fleet
  repository and carries this baseline.
- Public members are listed in [`inventory/repos.yml`](../inventory/repos.yml)
  with a profile under `profiles/repos/`.
- Private repositories carry the same in-repo baseline. The public inventory
  only lists them once the owner opts them in through `PRIVATE_INCLUDE_SLUGS`
  in `scripts/quality/fleet_inventory.py`, because listing them publishes
  their names.
- Forks the owner has worked on (commits ahead of upstream) carry the baseline too. Their
  `SECURITY.md` and `CONTRIBUTING.md` send upstream code to the upstream project
  (`--upstream owner/name`). The public inventory lists a fork only when it is
  explicitly whitelisted (`FORK_INCLUDE_SLUGS`).

## The baseline, file by file

| File | Template | Rule |
| ---- | -------- | ---- |
| `SECURITY.md` | `SECURITY.md.tmpl` | Created when missing. Replaced only when it is byte-identical to known platform boilerplate. A policy the repository wrote itself is kept. |
| `CONTRIBUTING.md` | `CONTRIBUTING.md.tmpl` | Created when missing from the root, `.github/` and `docs/`. Lists the lean gates for the languages actually present, plus local commands. Every statement describes the repository as it is: a repository-authored `scripts/verify`, pull-request template or Dependabot config is described as the repository's own, not as the managed one, and a checked-in CodeQL workflow is described as advanced setup with the query suite it actually selects (for QZP's `reusable-codeql.yml`, the suite read from the platform's own copy). |
| `CODE_OF_CONDUCT.md` | `CODE_OF_CONDUCT.md.tmpl` | Contributor Covenant 2.1, with the repository's private contact channel. |
| `.github/PULL_REQUEST_TEMPLATE.md` | `PULL_REQUEST_TEMPLATE.md.tmpl` | A gate checklist that maps 1:1 to CI. |
| `AGENTS.md` | `AGENTS.md.tmpl` | The rules for every contributor, human or automated: verify before claiming completion, never weaken a gate, tests ship with the change, never push to the default branch. |
| `.github/ISSUE_TEMPLATE/*.yml` | `ISSUE_TEMPLATE/*.yml.tmpl` | Bug and feature forms. `config.yml` links security reports to private vulnerability reporting on a public repository, and to `SECURITY.md` on a private one (GitHub has no private reporting form for private repositories). Created only when the repository has no issue templates of its own (an `ISSUE_TEMPLATE/` directory or a legacy `ISSUE_TEMPLATE.md`). |
| `.github/dependabot.yml` | rendered | Created when missing. A config carrying the managed header is regenerated whenever the manifests change. A config the repository wrote itself is kept and audited: a manifest tree without an update rail, or an `ignore` of semver-major updates, is reported as a gap. A caller created in the same run counts, so the first workflow arrives with its `github-actions` rail. |
| `.quality/opengrep/*.yaml` | QZP's own `.quality/opengrep/` | Seeded when the repository has no rule file (`*.yml`/`*.yaml`) there. Unmodified seeded rules stay managed: a newly present language gets its rules too. A repository-curated ruleset is kept. `general-security.yaml` always, plus the Python and JS/TS rules when those languages are present. The lean gate skips gate 4 without this directory. |
| `.semgrepignore` | `docs/lean-gate/semgrepignore.template` | Seeded when missing. Without it, opengrep silently drops `test/`, `tests/` and every `*_test.go`. A repository-authored scope that excludes everything or the test trees is reported as a gap. |
| `osv-scanner.toml` | `osv-scanner.toml.tmpl` | Seeded when missing, with no ignores. Gate 6 runs without it too (QZP #324), so the file is where reasoned, dated ignores live. An existing config is audited: an ignore without a written reason, without an `ignoreUntil` date or already expired is a gap. Gate 4 and gate 6 look for these exact, case-sensitive paths. |
| (gate 6 scope) | — | Dependencies gate 6 cannot see are reported as a gap, against the lockfiles osv-scanner v2.3.8 extracts in a source scan: a rail whose directory holds none of its ecosystem's readable files (for example a `pyproject.toml` with no `requirements*.txt` or lockfile, a `package.json` with no lockfile, a Gradle build with no `gradle.lockfile`), and Swift packages, which osv-scanner cannot read at all. `go.mod` and `pom.xml` are read directly. |
| `scripts/verify` | `verify.tmpl` | The canonical local entry point. It runs QZP's preflight at the exact commit the caller pins, in a dedicated clone per pinned commit that every run forces to the pin and cleans, so a local pass means what a CI pass means. A repository's own `scripts/verify` is kept. |
| `.github/workflows/quality.yml` | `quality.yml.tmpl` | The lean-gate caller, pinned to a full 40-hex QZP commit. A thin caller (one `quality` job that only calls `reusable-quality.yml`, read-only contents permissions, nothing else) is platform-owned: it is regenerated from the template, keeping its `with:` inputs and a `workflow_dispatch` trigger, and gaining `deps-severity-floor: any` with `--deps-severity-floor any` (an existing `any` is never dropped). Any other existing caller gets a pin bump. The required context is `<caller job> / <reusable job>`, so the caller job must be `quality` (id and name) and run on `pull_request`, or `quality / quality` never reports. With `--deps-severity-floor any` it must also pass `deps-severity-floor: any`. anything else is a gap, and so is a pin the tool cannot rewrite. A repository with its own in-repo `quality.yml` is reported as a gap to migrate by hand. |
| (CI coverage) | — | C, Java, Kotlin, C#, Rust and Swift are not tested by the lean gate. A repository with such source and no test workflow of its own is reported as a gap. A workflow counts only if it runs that language's build or test tooling. Go or C++ tests without a root `go.mod` (Go) or `scripts/coverage-gate.sh`, and JS/TS tests without a `test:coverage` (or `--coverage` test) script, are gaps too: those coverage lanes fail without them. |

Files are never overwritten unless they are known boilerplate. Discovery
covers tracked and untracked files and is case-insensitive (`security.md`
counts as a `SECURITY.md`), and `--write` refuses to create a file that
already exists on disk. Nothing is committed or pushed by the tool. Every
change reaches a fleet repository through a pull request, never a push to its
default branch.

## Zero tolerance

A finding of **any** severity fails CI. No gate has a "high and above only"
floor.

- **Lean gate** (`reusable-quality.yml`):
  - lint and format;
  - types, with zero errors and zero warnings;
  - tests at **100 % line and branch** coverage;
  - opengrep SAST, gitleaks and osv-scanner.

  Fleet callers opt gate 6 out of the T0 severity floor with
  `deps-severity-floor: any`, so unscored and `UNKNOWN`-severity advisories
  fail too. The tool renders that input only with `--deps-severity-floor any`,
  and only for a pin whose `reusable-quality.yml` declares the input (QZP
  #324 and later). Until then the generated docs state the T0 floor the
  pinned gate actually enforces.
- **CodeQL** (public repositories): GitHub default setup with the
  **extended** query suite (`security-extended`) and the `remote_and_local`
  threat model. Once a repository is green, its ruleset adds code-scanning
  merge protection at thresholds `all` / `all`.
- **Dependabot**:
  - One rail per manifest tree, for every ecosystem in the checkout plus
    `github-actions`.
  - Security updates are grouped per ecosystem, so one pull request can clear
    every open advisory.
  - Minor and patch updates are grouped.
  - **Majors are never ignored**; each arrives as its own pull request. This
    deliberately differs from the legacy profile renderer
    (`render_repo_baseline.py`), which ignores semver-major. Under a
    zero-tolerance dependency gate, a fix that only ships in a new major has
    to be able to arrive.

Exclusions stay explicit and documented in the repository that needs them.
For example, `fractal-darkroom`'s `research/` tree is a byte-identical archive
of an external record. It is excluded from ruff, basedpyright and coverage,
and its own CI job gates it. Zero tolerance applies to code a repository
owns. It never means rewriting an archive to satisfy a linter.

## Repository security settings

Public repositories mirror `fractal-darkroom`:

| Setting | Value | API |
| ------- | ----- | --- |
| Private vulnerability reporting | enabled | `PUT /repos/{repo}/private-vulnerability-reporting` |
| Dependabot alerts | enabled | `PUT /repos/{repo}/vulnerability-alerts` |
| Dependabot security updates | enabled | `PUT /repos/{repo}/automated-security-fixes` |
| Secret scanning + push protection | enabled | `PATCH /repos/{repo}` (`security_and_analysis`) |
| CodeQL default setup | `configured`, `query_suite: extended`, `threat_model: remote_and_local` | `PATCH /repos/{repo}/code-scanning/default-setup` |

Private repositories owned by a personal account cannot use GitHub Advanced
Security, so CodeQL, secret scanning and private vulnerability reporting are
unavailable there. Their security policy routes reports to an issue labelled
`security` in the repository itself, which only people with access can see. They get Dependabot alerts and security updates. The lean
gate's gitleaks and opengrep lanes are their secret-scanning and
code-scanning controls.

## Enforcement order

The rollout must never deadlock a repository: no pull request may be blocked
by a check that pull requests can't turn green.

1. **Governance files pull request:** community files and Dependabot. It
   merges once the repository's existing CI is green.
2. **Lean-gate caller pull request:** create the caller or bump its pin. It
   stays open until every gate is green, and remediation lands on that branch.
3. **Enforcement:** only after the default branch is green with the caller,
   apply the ruleset or branch protection.
   - Require exactly the contexts the repository emits on `pull_request`:
     `quality / quality`, `CodeQL`, and the repository's own fail-closed
     aggregate if it has one.
   - Open a test pull request first and confirm every required context
     reports on it.
   - Renaming a required context (for example moving CodeQL to an advanced
     workflow) must update protection in the same change window.

## Running the tool

```bash
python -m scripts.quality.fleet_baseline \
    --repo-dir ../consumer --slug Prekzursil/<name> \
    --qzp-pin <40-hex quality-zero-platform commit> \
    [--default-branch main] [--private] [--deps-severity-floor t0|any] \n    [--write] [--json-out plan.json]
```

The tool prints one line per baseline file: `create`, `replace`, `bump`,
`keep` or `gap`.

| Exit code | Meaning |
| --------- | ------- |
| 0 | The checkout conforms. |
| 1 | Something is still missing or gapped. With `--write`, only gaps count. |
| 2 | The pin is not a full commit SHA. |
