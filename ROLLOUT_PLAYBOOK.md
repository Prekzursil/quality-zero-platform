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
5. Configure Codex Web per repo using the `codex_setup_command` declared in the repo profile.

## Wrapper Contract

Each governed repo should converge on:

- `.github/workflows/quality-zero-platform.yml`
- `.github/workflows/quality-zero-backlog.yml`
- `.github/workflows/quality-zero-remediation.yml`
- `bash scripts/verify`
- a repo-local `AGENTS.md` that points back to strict-zero verification

## Safety Checks

- Do not require a context in rulesets until the provider actually emits it.
- Treat missing vendor statuses as drift in policy, secrets, or provider wiring.
- Keep child check names stable until the migration is explicitly marked complete in `inventory/repos.yml`.
- Never let remediation or backlog workflows write to `main` or `master`.

## Codex Web Manual Pass

For each repo in scope:

1. Connect the GitHub repo in Codex Web.
2. Configure the environment with the repo profile's `codex_setup_command`.
3. Run the repo profile's `verify_command`.
4. Store the verification note in the rollout tracker before enabling backlog sweeps.
