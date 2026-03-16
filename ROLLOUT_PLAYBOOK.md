# quality-zero-platform Rollout Playbook

## Goals

- keep absolute strict-zero enforcement intact
- preserve phase-1 public check names
- move policy resolution and reusable workflow logic out of per-repo copy templates
- make remediation deterministic and branch-safe

## Rollout Order

1. Dry-run profile and ruleset generation against `Prekzursil/pbinfo-get-unsolved`.
2. Enable caller workflows on the common phase-1 template repos.
3. Migrate the four overlay repos: `SWFOC-Mod-Menu`, `env-inspector`, `Airline-Reservations-System`, and `Reframe`.
4. Generate repo-level ruleset payloads and validate emitted contexts before applying them.
5. Configure Codex with the repo profile's automatic environment contract and unrestricted network.

## Wrapper Contract

Each governed repo should converge on:

- `.github/workflows/quality-zero-platform.yml`
- `.github/workflows/quality-zero-gate.yml`
- `.github/workflows/codecov-analytics.yml`
- `.github/workflows/quality-zero-backlog.yml`
- `.github/workflows/quality-zero-remediation.yml`
- `bash scripts/verify`
- a repo-local `AGENTS.md` that points back to strict-zero verification

The scanner, gate, and Codecov wrappers must run on:

- `pull_request` targeting `main` / `master`
- `push` targeting `main` / `master`
- `workflow_dispatch`

## Safety Checks

- Do not require a context in rulesets until the provider actually emits it.
- Treat missing vendor statuses as drift in policy, secrets, or provider wiring.
- Keep child check names stable until the migration is explicitly marked complete in `inventory/repos.yml`.
- Never let remediation or backlog workflows write to `main` or `master`.

## Codex Web Manual Pass

For each repo in scope:

1. Connect the GitHub repo in Codex Web.
2. Ensure the repo uses the profile's automatic environment contract with unrestricted network and all methods.
3. Run the repo profile's `verify_command`.
4. Store the verification note in the rollout tracker before enabling backlog sweeps.
