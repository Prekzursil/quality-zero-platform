# quality-zero-platform

`quality-zero-platform` is the personal-account control plane for strict-zero governance.
It replaces the old queue/template baseline with reusable workflows, shared quality scripts, repo metadata, ruleset payload generation, and Codex remediation contracts.

## What This Repo Owns

- `inventory/repos.yml`: enrolled repositories, rollout wave, and profile bindings
- `profiles/stacks/*.yml`: reusable stack defaults
- `profiles/repos/*.yml`: repo-specific overrides
- `scripts/quality/`: shared strict-zero policy, provider checks, ruleset generation, and remediation prompt rendering
- `.github/workflows/`: reusable workflows called by governed repos
- `templates/repo/`: thin wrapper contract for governed repos
- `generated/rulesets/`: committed ruleset payloads for review and drift detection

## Governed Repositories

The current governed surface covers 13 repositories:

- `Prekzursil/Airline-Reservations-System`
- `Prekzursil/DevExtreme-Filter-Go-Language`
- `Prekzursil/event-link`
- `Prekzursil/momentstudio`
- `Prekzursil/Personal-Finance-Management`
- `Prekzursil/pbinfo-get-unsolved`
- `Prekzursil/quality-zero-platform`
- `Prekzursil/Reframe`
- `Prekzursil/SWFOC-Mod-Menu`
- `Prekzursil/Star-Wars-Galactic-Battlegrounds-Save-Game-Editor`
- `Prekzursil/TanksFlashMobile`
- `Prekzursil/WebCoder`
- `Prekzursil/env-inspector`

Phase-1 compatibility still preserves the established public check names for the original strict-zero cohort while `event-link`, `momentstudio`, and `quality-zero-platform` join the governed inventory.

## Control-Plane Flow

1. Wrapper repos call the reusable scanner and gate workflows from this repo.
2. Shared Python utilities resolve the repo profile, compute required contexts, validate provider inputs, and emit artifacts.
3. Ruleset payloads are generated from the same repo profile data that powers the gate.
4. Failed pull requests enter the Codex remediation loop and write only to `codex/fix/<context>/<shortsha>`.
5. Nightly backlog sweeps isolate one tool lane per run on `codex/backlog/<tool>`.

## Local Development

```bash
python -m pip install -r requirements-dev.txt
bash scripts/verify
```

## Codex Web Lane

Codex Web is intentionally secondary. Each enrolled repo declares:

- `codex_environment.mode: automatic`
- `codex_environment.network_profile: unrestricted`
- `codex_environment.methods: all`
- `verify_command`: the canonical repo verification command

Codex Web is used for backlog sweeps and manual review, not for merge-gate authority. Manual setup and maintenance scripts are intentionally not part of the control-plane contract.
