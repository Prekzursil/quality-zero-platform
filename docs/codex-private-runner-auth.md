# Codex private-runner auth

`quality-zero-platform` assumes GitHub-side mutation happens only on a **trusted private runner**.

The supported model is:

- Codex CLI is already installed on the runner
- the runner keeps a persistent Codex home outside ephemeral job state
- the runner is authenticated with `codex login`
- reusable remediation and backlog workflows run `codex exec`
- `OPENAI_API_KEY` is not required for the common strict-zero contract

## Persistent auth location

The default control-plane contract expects:

```text
~/.codex/auth.json
```

Repo profiles export that path through:

- `codex_environment.auth_file`

## Bootstrap paths

### Preferred: seed the runner once

On the trusted runner host, run:

```bash
codex login
```

This writes the account-auth session to `~/.codex/auth.json`. Because the runner is private and persistent, later workflow runs can reuse and refresh that file in place.

### Fallback bootstrap: temporary GitHub secret

If the runner is freshly provisioned and the persistent auth file does not exist yet, the reusable workflows can consume a temporary repository secret:

```text
CODEX_AUTH_JSON
```

The workflows will only use it to seed `auth.json` when the target auth file is missing. Once the runner has a persistent authenticated profile, the secret can be removed.

## Required runner labels

The shared stack defaults export these labels:

```json
["self-hosted", "codex-trusted"]
```

Label at least one trusted runner with both labels before enabling remediation or backlog sweeps.

## Operational notes

- Treat the runner as private infrastructure; do not use account-auth Codex on public runners.
- Keep `auth.json` outside repository trees and never commit it.
- If the auth file expires or becomes invalid, re-run `codex login` on the runner host or temporarily reseed with `CODEX_AUTH_JSON`.
- `scripts/quality/ensure_codex_auth.py` is the reusable guard used by workflows before running `codex exec`.
