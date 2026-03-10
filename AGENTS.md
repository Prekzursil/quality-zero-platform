# AGENTS.md

## Operating Model

`quality-zero-platform` is the strict-zero control plane for personal-account repositories.
This repo owns reusable workflows, policy resolution, ruleset payload generation, remediation prompt contracts, and phase-1 onboarding metadata.

## Canonical Verification Command

Run this command before claiming completion:

```bash
bash scripts/verify
```

## Control-Plane Rules

- Preserve public check names for enrolled phase-1 repos until their ruleset migration is explicitly completed.
- Treat missing third-party contexts as policy drift first; do not "fix code" when the gate definition is wrong.
- Never push directly to a governed repo's default branch from automation. Use `codex/fix/<context>/<shortsha>` or `codex/backlog/<tool>`.
- Keep reusable workflows generic and repo behavior declarative through `inventory/` and `profiles/`.

## Repository Contract

Wrapper repos are expected to provide:

- a canonical `bash scripts/verify` entrypoint
- thin caller workflows that invoke this repo via `workflow_call`
- a minimal repo-local `AGENTS.md` that points contributors back to strict-zero verification

## Scope Guardrails

- Do not commit secrets, tokens, or runtime artifacts.
- Keep policy changes coupled with tests or validation fixtures.
- Prefer additive migrations that keep phase-1 repos green while the control plane takes ownership.
