# QZP — Onboarding a New Repo

Step-by-step procedure for adding a new repository to the
quality-zero-platform governor. Every step is reversible until the
final `phase: absolute` flip — until then the repo runs in
**`shadow`** mode (gates report but never block) so you can validate
the wiring without breaking the consumer's PR flow.

> [!IMPORTANT]
> The companion document is [`docs/OPERATOR-RUNBOOK.md`](OPERATOR-RUNBOOK.md)
> which covers ongoing operations (waves, dispatches, alerts). This
> document is **specifically about adding a new repo to the fleet**.
> See [`docs/QUALITY-GATES.md`](QUALITY-GATES.md) for choosing
> thresholds.

---

## Prerequisites

You'll need:

1. **Write access** to both this platform repo and the consumer repo.
2. **`gh auth login`** configured for both.
3. **A `DRIFT_SYNC_PAT`** secret on the platform repo (already
   provisioned per the operator runbook). The bootstrap workflow
   uses it to open the wrapper-installation PR on the consumer.
4. **Familiarity with the stack** — pick the closest existing stack
   profile (see *Stack Selection* below).

---

## Step 1 — Pick a stack

Stack profiles live in [`profiles/stacks/*.yml`](../profiles/stacks/)
and define the *baseline* gates + scanners for an entire stack family.
The 10 currently shipped:

| Stack profile | When to use |
|---|---|
| `python-tooling` | Python CLI tools, libraries, scripts (no web framework). |
| `python-only` | Pure-Python apps where the only runtime is Python. |
| `python-web` | Flask / Django / FastAPI services. |
| `python-desktop` | Windows desktop Python apps (PyInstaller, etc.). |
| `node-frontend` | Frontend-only Node/TS (no backend in this repo). |
| `react-vite-vitest` | React + Vite + Vitest stack. |
| `fullstack-web` | Backend + frontend in one repo (e.g. `event-link`). |
| `go` | Go modules, single-binary services. |
| `rust` | Rust crates, single-binary services. |
| `gradle-java` | Gradle + Java, including Spring Boot. |
| `cpp-cmake` | C/C++ with CMake. |
| `dotnet-wpf` | .NET / WPF desktop apps. |

If your repo doesn't fit, *do not* shoehorn — open an issue on the
platform repo and add a new stack profile in a separate PR. Stacks
encode language-specific assumptions (coverage XML format, lint
pinning, scanner enablement) that don't transfer cleanly between
ecosystems.

## Step 2 — Add the repo to inventory

Edit [`inventory/repos.yml`](../inventory/repos.yml) and append:

```yaml
- slug: <Owner>/<RepoName>
  profile: <repo-name-lowercased>      # matches profiles/repos/<name>.yml
  rollout: phase2-wave3                # next-wave default; see existing entries
  default_branch: main
  notes: <one-line context>
```

The `profile` field MUST match the filename of the per-repo profile
you'll create in Step 3.

## Step 3 — Create the per-repo profile

Create [`profiles/repos/<name>.yml`](../profiles/repos/) with the
minimum surface:

```yaml
slug: <Owner>/<RepoName>
version: 2                # current schema version
stack: <stack-from-step-1>
mode:
  phase: shadow           # ALWAYS start in shadow — gates report but never block
issue_policy:
  mode: ratchet           # 'ratchet' = no-new-debt; 'audit' = informational only
                          # 'zero' = strict zero on all scanners
scanners:
  # Override individual scanner severities here. Defaults inherit from the stack.
  # Set to 'block' to fail PRs on findings; 'info' to surface but not block;
  # 'off' to disable entirely. See docs/QUALITY-GATES.md for the matrix.
  codeql: { severity: block }
  dependabot: { severity: block }
  sonarcloud: { severity: block }
  codacy_issues: { severity: ratchet }   # 'ratchet' = block only on NEW issues
  deepscan: { severity: block }
  semgrep: { severity: block }
overrides: []
verify_command: bash scripts/verify     # consumer-side smoke-test entrypoint
required_contexts_mode: replace         # 'replace' = use 'always'+'pull_request_only'
                                        # 'extend'  = add to legacy required-checks list
required_contexts:
  always:                               # required on push and PR
  - shared-scanner-matrix / Coverage 100 Gate
  - shared-codecov-analytics / Codecov Analytics
  - codeql / CodeQL
  pull_request_only:                    # required on PR only
  - SonarCloud Code Analysis
  target:                               # what we'll *eventually* require post-shadow
  - shared-scanner-matrix / Coverage 100 Gate
  - shared-codecov-analytics / Codecov Analytics
  - codeql / CodeQL
  - shared-scanner-matrix / QLTY Zero
  - shared-scanner-matrix / Sonar Zero
  - SonarCloud Code Analysis
coverage:
  command: |
    # Whatever produces a coverage XML on this repo:
    pytest --cov --cov-report=xml:coverage/coverage.xml
  inputs:
  - format: xml
    name: <repo-shorthand>
    path: coverage/coverage.xml
    flag: <flag-name-for-codecov>      # multi-flag repos can list multiple inputs
  require_sources:
  - <subdirectory whose coverage matters>/
vendors:
  sonar:
    project_key: <SonarCloud organization_repo>     # e.g. Prekzursil_event-link
codeql:
  languages:
  - python                              # match the consumer's language list
dependabot:
  updates:
  - ecosystem: pip
    directory: /
```

Every field is documented in the
[`docs/QZP-V2-DESIGN.md`](QZP-V2-DESIGN.md) `§3 Schema v2` section.

## Step 4 — Validate the profile locally

```bash
# Run from platform repo root.
python scripts/quality/validate_control_plane.py \
  --inventory inventory/repos.yml \
  --profile profiles/repos/<name>.yml
```

Exits 0 if the profile shape is valid (schema v2 conformant, all
referenced scanners exist, coverage inputs parse, etc.). Exits non-zero
with a pinned error message if not.

## Step 5 — Open the bootstrap PR

```bash
# Run the bootstrap workflow — it opens a PR on the consumer repo
# adding a thin wrapper that calls the platform's reusable workflows.
gh workflow run reusable-bootstrap-repo.yml \
  --ref main \
  -f repo_slug=<Owner>/<RepoName> \
  -f stack=<stack-from-step-1> \
  -f initial_mode=shadow \
  -f target_phase=absolute
```

The wrapper-installation PR adds:

- `.github/workflows/quality-zero-platform.yml` — the consumer-side
  caller workflow (typically 3-5 references to `Prekzursil/quality-zero-platform/.github/workflows/<name>.yml@<sha>`).
- `.github/workflows/codecov-analytics.yml` — coverage upload lane.
- `.github/dependabot.yml` — security updates.
- `codecov.yml` — coverage flag config.

Review the PR. If it looks right, merge it. The repo now runs the
QZP pipeline in **shadow mode** — every gate runs and reports, but
none can block PRs.

## Step 6 — 3 green shadow runs

Wait for at least **3 successful runs** of `shared-quality-zero-gate`
on the consumer's main branch (any combination of pushes and merged
PRs counts). The bootstrap workflow tracks this via a per-repo state
file in `.beads/`. You can also check manually:

```bash
gh run list -R <Owner>/<RepoName> --branch main \
  --workflow "Quality Zero Platform" --limit 5 \
  --json conclusion --jq '.[] | .conclusion'
# Expect: 3+ "success" lines.
```

## Step 7 — Promote to absolute

Once 3 green shadow runs are in, flip the profile from
`phase: shadow` to `phase: absolute`:

```bash
# Edit profiles/repos/<name>.yml — change `phase: shadow` to `phase: absolute`.
# Then commit + push to a `chore/` branch on the platform repo.
git checkout -b chore/promote-<name>-to-absolute
# (edit the file)
git add profiles/repos/<name>.yml
git commit -m "chore(qzp-v2): promote <name> from shadow to absolute"
git push -u origin chore/promote-<name>-to-absolute
gh pr create --fill
```

When that platform-repo PR merges, the consumer's gates start
**blocking** PRs on findings. The promotion is reversible — flip back
to `shadow` if a regression surfaces.

## Step 8 — Add the SHA bump to the next wave

The wrapper installed in Step 5 pinned the platform's reusable
workflows to a specific SHA. Future platform changes don't reach the
consumer until that pin is bumped. The
[`bump-workflow-shas-wave.yml`](../.github/workflows/bump-workflow-shas-wave.yml)
fleet dispatcher does this — see the operator runbook for the
dispatch incantation.

## Step 9 — Optional: change quality-gate thresholds

If the defaults are too strict (or too lax) for this repo, see
[`docs/QUALITY-GATES.md`](QUALITY-GATES.md) for the lever map: how to
tighten/relax SonarCloud, Codacy, coverage, and complexity gates per
scanner per repo.

---

## Rollback

If onboarding goes wrong:

1. **Mid-bootstrap (PR not merged)**: just close the wrapper PR on
   the consumer.
2. **Post-bootstrap, still in shadow**: revert the inventory entry
   on the platform via a `chore/revert-…` PR. The shadow gates
   continue running but the repo drops out of fleet sweeps.
3. **Post-promotion to absolute**: flip `phase: absolute` back to
   `phase: shadow` on the platform via the same revert PR. Gates
   keep running but stop blocking. If you need to fully remove the
   repo, also revert the consumer-side wrapper-installation merge.

---

## See also

- [`docs/OPERATOR-RUNBOOK.md`](OPERATOR-RUNBOOK.md) — ongoing ops
  (waves, dispatches, alerts).
- [`docs/QUALITY-GATES.md`](QUALITY-GATES.md) — picking gates and
  thresholds.
- [`docs/QZP-V2-DESIGN.md`](QZP-V2-DESIGN.md) — full schema and
  architecture reference.
