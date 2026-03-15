# quality-zero-platform Design

## Objective

Replace `fleet-baseline-lite-v2` with a strict-zero control plane that owns reusable workflows, policy resolution, ruleset payload generation, remediation prompts, and phase-1 onboarding metadata for personal-account repositories.

## Approved Constraints

- strict-zero is the only first-class purpose
- phase 1 covers the nine repos already using `Quality Zero Gate`
- existing public check names stay stable during phase 1
- GitHub Actions on a trusted private runner plus `codex exec` account auth is the enforcement and remediation engine
- Codex Web remains a secondary, repo-connected backlog and review lane

## Control Plane

- `inventory/repos.yml` binds repositories to rollout wave and profile id
- `profiles/stacks/*.yml` model shared stack defaults
- `profiles/repos/*.yml` apply repo overrides
- shared scripts resolve active required contexts, provider inputs, ruleset payloads, and Codex prompts
- reusable workflows call those scripts instead of embedding repo-specific policy

## Automation Model

- aggregate gates poll for declared required contexts and fail closed on drift
- remediation opens or updates `codex/fix/<context>/<shortsha>` PR branches
- backlog sweeps run per tool lane on `codex/backlog/<tool>`
- ruleset payloads are generated from the same resolved profile used by workflows
