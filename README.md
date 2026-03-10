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

## Phase 1 Cohort

Phase 1 preserves existing public check names for:

- `Prekzursil/Airline-Reservations-System`
- `Prekzursil/DevExtreme-Filter-Go-Language`
- `Prekzursil/Personal-Finance-Management`
- `Prekzursil/Reframe`
- `Prekzursil/SWFOC-Mod-Menu`
- `Prekzursil/Star-Wars-Galactic-Battlegrounds-Save-Game-Editor`
- `Prekzursil/TanksFlashMobile`
- `Prekzursil/WebCoder`
- `Prekzursil/env-inspector`

`Prekzursil/pbinfo-get-unsolved` stays in the inventory as the dry-run fixture and script portability source.

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

- `codex_setup_command`: the environment bootstrap command for Codex Web
- `verify_command`: the canonical repo verification command

Codex Web is used for backlog sweeps and manual review, not for merge-gate authority.
