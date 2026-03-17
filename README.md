# quality-zero-platform

`quality-zero-platform` is the personal-account control plane for strict-zero governance.
It replaces the old queue/template baseline with reusable workflows, shared quality scripts, repo metadata, ruleset payload generation, provider-admin browser bootstrap helpers, and trusted-runner Codex remediation contracts.

## What This Repo Owns

- `inventory/repos.yml`: enrolled repositories, rollout wave, and profile bindings
- `profiles/stacks/*.yml`: reusable stack defaults
- `profiles/repos/*.yml`: repo-specific overrides
- `scripts/quality/`: shared strict-zero policy, provider checks, ruleset generation, and remediation prompt rendering
- `scripts/provider_ui/`: Playwright-based provider-admin bootstrap scripts that persist browser state outside the repo
- `.github/workflows/`: reusable workflows called by governed repos, including push-parity scanner, gate, and Codecov analytics lanes
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

1. Wrapper repos call the reusable scanner, gate, and Codecov analytics workflows from this repo on both pull requests and protected-branch pushes.
2. Shared Python utilities resolve the repo profile, compute required contexts, validate provider inputs, and emit artifacts.
3. Ruleset payloads are generated from the same repo profile data that powers the gate.
4. Failed pull requests enter the trusted-runner Codex remediation loop and write only to `codex/fix/<context>/<shortsha>`.
5. Nightly backlog sweeps isolate one tool lane per run on `codex/backlog/<tool>`.

## Trusted Runner Codex Lane

GitHub-side mutation runs are intentionally limited to **trusted private runners** that already have Codex CLI installed and authenticated with a ChatGPT/Codex account session.

The common control-plane contract now assumes:

- `github_mutation_lane: codex-private-runner`
- `codex_auth_lane: chatgpt-account`
- `provider_ui_mode: playwright-manual-login`
- `codex_environment.runner_labels: ["self-hosted", "codex-trusted"]`
- `codex_environment.auth_file: ~/.codex/auth.json`

`OPENAI_API_KEY` is **not** part of the common strict-zero contract. The reusable remediation and backlog workflows run `codex exec` on the trusted runner instead of `openai/codex-action`.

Bootstrap the runner once with `codex login`, or provide a temporary `CODEX_AUTH_JSON` secret until the runner has a persistent `auth.json`.

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

## Provider UI Bootstrap

Provider-admin browser work lives under `scripts/provider_ui/` and `docs/provider-browser-bootstrap.md`. The bootstrap uses a dedicated persistent Playwright profile outside the repository tree so manual login can be completed once and later reused for authenticated provider checks.

See also: `docs/codex-private-runner-auth.md`
