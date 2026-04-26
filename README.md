# quality-zero-platform

`quality-zero-platform` (QZP) is the personal-account control plane for hybrid ratchet plus strict-zero governance.
It replaces the old queue/template baseline with reusable workflows, shared quality scripts, repo metadata, ruleset payload generation, provider-admin browser bootstrap helpers, GitHub-native admin/dashboard surfaces, aggregated PR rollups, and trusted-runner Codex remediation contracts.

> [!IMPORTANT]
> **Where to start:**
> - **Adding a new repo to the fleet?** → [`docs/ONBOARDING.md`](docs/ONBOARDING.md)
> - **Choosing gates / thresholds?** → [`docs/QUALITY-GATES.md`](docs/QUALITY-GATES.md)
> - **Running fleet-wide operations?** → [`docs/OPERATOR-RUNBOOK.md`](docs/OPERATOR-RUNBOOK.md)
> - **Full architecture reference?** → [`docs/QZP-V2-DESIGN.md`](docs/QZP-V2-DESIGN.md)

## What This Repo Owns

- `inventory/repos.yml`: enrolled repositories, rollout wave, and profile bindings
- `profiles/stacks/*.yml`: reusable stack defaults
- `profiles/repos/*.yml`: repo-specific overrides
- `scripts/quality/`: shared strict-zero policy, provider checks (13 providers: Codacy, CodeQL, Chromatic, Applitools, SonarCloud, DeepSource, DeepScan, QLTY, Sentry, Semgrep, Dependabot, Coverage, Secrets), ruleset generation, and remediation prompt rendering
- `scripts/provider_ui/`: Playwright-based provider-admin bootstrap scripts that persist browser state outside the repo
- `.github/workflows/`: reusable workflows called by governed repos, including push-parity scanner, gate, rollup, admin, Pages, and Codecov analytics lanes
- `templates/repo/`: thin wrapper contract for governed repos
- `generated/rulesets/`: committed ruleset payloads for review and drift detection

## Governed Repositories

The current governed surface (per [`inventory/repos.yml`](inventory/repos.yml)) covers 15 repositories. Run `yq '.repos[].slug' inventory/repos.yml` for the current list; this README intentionally does not duplicate it to avoid drift.

Phase-1 compatibility preserves the established public check names for the original strict-zero cohort while later cohorts (`event-link`, `momentstudio`, `quality-zero-platform`) join the governed inventory through the bootstrap flow described in [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

## Control-Plane Flow

1. Wrapper repos call the reusable scanner, gate, and Codecov analytics workflows from this repo on both pull requests and protected-branch pushes.
2. Shared Python utilities resolve the repo profile, compute required contexts, validate provider inputs, and emit artifacts.
3. Ruleset payloads are generated from the same repo profile data that powers the gate.
4. Failed pull requests enter the trusted-runner Codex remediation loop and write only to `codex/fix/<context>/<shortsha>`.
5. Nightly backlog sweeps isolate one tool lane per run on `codex/backlog/<tool>`.
6. Pull requests receive one aggregated quality rollup v2 comment (multi-view markdown with by-file, by-severity, and provider-summary views) instead of forcing reviewers to inspect every individual scanner artifact.

## Hybrid Ratchet

The control plane distinguishes onboarding and convergence:

- PRs can use `issue_policy.mode: ratchet` so Sonar and Codacy enforce no-new-debt semantics while a repo still carries legacy backlog.
- Protected-branch convergence stays strict once the repo is fully onboarded.
- Coverage remains whole-project 100% by default, while QLTY still enforces patch coverage and total non-regression.

See [`docs/QUALITY-GATES.md`](docs/QUALITY-GATES.md) for the full mode-and-severity matrix.

## QZP v2 (Phase 1–5) Capabilities

Beyond the original strict-zero gate flow, v2 adds:

- **Schema v2 profiles** with explicit per-scanner severity, hybrid mode (`shadow` / `ratchet` / `absolute`), and stack inheritance — see [`profiles/stacks/`](profiles/stacks/) and [`profiles/repos/`](profiles/repos/).
- **Codecov per-flag uploads** for multi-component repos (`event-link` runs separate `backend` and `ui` flags).
- **Drift-sync templates** that regenerate consumer CI files from stack templates and open PRs when they drift; fleet-wide dispatcher in [`drift-sync-wave.yml`](.github/workflows/drift-sync-wave.yml).
- **Severity rollup + break-glass labels** — PR rollup honors per-scanner severity; admins can apply `bypass:break-glass` or `skip:<scanner>` labels with audit logging.
- **Known-issues registry** so Codex remediation prompts can include “don't refile this” context.
- **Bootstrap workflow** — onboard a new repo with a starter profile + 3-green-shadow-runs gate before flipping `mode.phase` from `shadow` to `absolute|ratchet`. See [`reusable-bootstrap-repo.yml`](.github/workflows/reusable-bootstrap-repo.yml).
- **Bumps recipe pipeline** — fleet-wide bumps (Node 20→24, Python minor versions, etc.) flow through staged dispatch with rollback, see [`reusable-bumps.yml`](.github/workflows/reusable-bumps.yml).
- **Admin dashboard** at `https://prekzursil.github.io/quality-zero-platform/` — repo heatmap, coverage trend, drift status, bypass audit feed.
- **Alerts subsystem** — every operationally relevant condition (drift, secret-missing, fleet-bump-fail, etc.) dispatches a labelled GitHub issue via [`alerts.py`](scripts/quality/alerts.py); cron sweep in [`scheduled-alerts.yml`](.github/workflows/scheduled-alerts.yml).

## Admin Dashboard

The repo now includes a GitHub-native admin surface:

- `publish-admin-dashboard.yml` builds and deploys a read-only dashboard to GitHub Pages.
- `control-plane-admin.yml` accepts typed `workflow_dispatch` inputs and opens PRs for inventory or profile changes instead of mutating `main` directly.
- `scripts/quality/build_admin_dashboard.py` renders the dashboard payload and static HTML.
- `scripts/quality/control_plane_admin.py` applies the requested YAML edits before rulesets are regenerated and reviewed in the PR.

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
