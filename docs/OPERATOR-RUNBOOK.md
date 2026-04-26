# QZP v2 — Operator Runbook

Concrete `gh workflow run` invocations for advancing the QZP v2 rollout
state. Every command in this runbook is **safe by default** — every
workflow defaults to `dry_run=true` so an accidental dispatch never
opens fleet-wide PRs without an explicit operator opt-out.

This runbook is the missing piece for closing the operator-only
absolute-done bullets identified in
[`.beads/context/execution-state.md`](../.beads/context/execution-state.md).
Every step here is directly cited in that file.

> [!IMPORTANT]
> All commands below assume the operator runs them from a workstation
> with `gh auth login` already configured for the
> `Prekzursil/quality-zero-platform` repo (and write access to the
> consumer repos).

---

## Prerequisites

### `DRIFT_SYNC_PAT` secret

Several workflows in this runbook open PRs against consumer repos.
Without a fine-grained PAT scoped to those repos, they fall back to
`github.token` which can only read public repos and never write.

Provision the PAT once:

1. <https://github.com/settings/personal-access-tokens/new> → choose
   **Only select repositories**.
2. Select every repo in `inventory/repos.yml` *except*
   `Prekzursil/quality-zero-platform` (the platform never bumps itself
   via these waves).
3. Permissions → **Contents: Read and write**, **Pull requests: Read
   and write**, **Workflows: Read and write**.
4. Copy the token, then add it to platform repo settings →
   *Secrets and variables* → *Actions* → **`DRIFT_SYNC_PAT`**.

The same secret powers `drift-sync-wave.yml`,
`bump-workflow-shas-wave.yml`, and the bumps wave.

### `AUDIT_PAT` secret (secrets-sync only)

The secrets-sync workflow appends to `audit/secrets-sync.jsonl` on
the platform repo via the GitHub Contents API. That requires a
fine-grained PAT with `contents:write` on
`Prekzursil/quality-zero-platform`. Add it as
`AUDIT_PAT`. Skip this if not running secrets-sync.

---

## Phase 5 — Wave dispatches

### 1. SHA-bump wave (refresh stale platform pins)

The fleet currently pins **pre-Phase-2 platform SHAs** in every
consumer's `.github/workflows/*.yml`. Until those bumps land, no
consumer can pick up the per-flag Codecov fix or the latest scanner
matrix shape.

```bash
# Step A — dry-run audit (always run first)
gh workflow run bump-workflow-shas-wave.yml \
  --ref main \
  -f target_sha=$(git rev-parse origin/main) \
  -f dry_run=true

# Wait ~2 minutes for the wave to fan out across 14 consumers, then:
gh run list --workflow "Bump Workflow SHAs Wave" --limit 1
gh run download <run-id>   # collect all 14 sha-bump-*.json artifacts

# Each artifact reports {repo, target_sha, files, bumped_total}.
# Confirm the per-repo bump counts look right (typically 4-6 caller
# workflows per repo).
```

```bash
# Step B — opt out of dry-run (with PAT now configured)
gh workflow run bump-workflow-shas-wave.yml \
  --ref main \
  -f target_sha=$(git rev-parse origin/main) \
  -f dry_run=false
```

Step B opens 14 consumer-repo bump PRs. Each PR has the same shape
(every `Prekzursil/quality-zero-platform/.github/workflows/<name>.yml@<old-sha>`
reference replaced with the new SHA). Review + merge each.

### 2. Drift-sync wave

After the SHA bump lands, consumers are running the latest reusable
workflows. Their template-rendered files (codecov.yml, ci-fragments,
dependabot.yml) may still drift from what the latest stack templates
emit; the drift-sync wave opens PRs to bring them into alignment.

```bash
# Dry-run first
gh workflow run drift-sync-wave.yml \
  --ref main \
  -f dry_run=true
# → 15 sync jobs run; each uploads drift-report-<slug>.json
# → expected exit: every job exits 1 with the drift-detected sentinel
#   (the dry-run-detected-drift signal — not a bug)

# Real run
gh workflow run drift-sync-wave.yml \
  --ref main \
  -f dry_run=false
# → opens drift PRs on each consumer repo whose drift report had any
#   missing/drift entries
```

### 3. Bumps wave (Node 20 → 24 canary)

This is the live test of the bumps recipe pipeline.

```bash
# Dry-run first
gh workflow run reusable-bumps.yml \
  --ref main \
  -f recipe_path=profiles/bumps/2026-04-23-node-24.yml \
  -f dry_run=true
# → plan job emits {staging: [env-inspector, webcoder], rollout: [...]}
# → stage-1 fans out to 2 staging repos in dry-run
# → stage-2 fans out to ~7 rollout repos in dry-run
# → no PRs open; bump-apply.json artifacts uploaded

# Real run
gh workflow run reusable-bumps.yml \
  --ref main \
  -f recipe_path=profiles/bumps/2026-04-23-node-24.yml \
  -f dry_run=false
# → stage-1 opens 2 staging PRs
# → stage-2 opens rollout PRs (per design, stage-2 currently runs
#   immediately after stage-1 dispatch succeeds; the operator merges
#   stage-2 PRs only after staging-CI is green on the consumer side)
# → if stage-1 fails: rollback opens alert:fleet-bump-fail
```

> [!NOTE]
> Stage-2 currently fan-outs immediately after stage-1 (PR-opening
> only — the consumer-side CI on each PR is what gates the actual
> merge). A polling job that explicitly waits for staging-CI green
> before stage-2 opens additional PRs is a future increment.

### 4. SonarCloud — operator config (event-link only)

Event-link's `Coverage 100 Gate` keeps failing on `SonarCloud scan`
with:

```
ERROR: You are running CI analysis while Automatic Analysis is enabled.
       Please consider disabling one or the other.
```

Toggle **Automatic Analysis** OFF on
<https://sonarcloud.io/project/configuration?id=Prekzursil_event-link>.
Then re-run CI on event-link's open PR (#130) — Coverage 100 Gate
will pass and the PR can merge.

---

## Phase 5 — Other operator levers

### `reusable-secrets-sync.yml` (workflow_call)

This workflow propagates one secret to N consumer repos with audit
logging. It is `workflow_call` only — there is no operator dispatch
trigger, by design. Wire it from a consumer's caller workflow when
needed.

### `reusable-bootstrap-repo.yml` (manual onboarding)

Onboards a new consumer repo with a starter profile + 3-green-shadow-runs
gate before flipping `mode.phase` from `shadow` to `absolute|ratchet`.

```bash
gh workflow run reusable-bootstrap-repo.yml \
  --ref main \
  -f repo_slug=Prekzursil/<new-repo> \
  -f stack=python-tooling \
  -f initial_mode=shadow \
  -f target_phase=absolute
```

### `scheduled-alerts.yml` (cron)

Runs every 6 hours; aggregates fleet state and dispatches via
`alert_dispatch`. Manually triggerable for ad-hoc verification:

```bash
gh workflow run scheduled-alerts.yml --ref main -f dry_run=false
```

Default `dry_run=true` is the safe default for the cron schedule
(no surprise `alert:*` issue creation from a cron firing on
incomplete fleet state).

---

## Verification gates before emitting completion

Run these from a clean checkout of platform main:

```bash
# Code-side audit
python scripts/quality/verify_v2_deployment.py --all
echo $?   # MUST be 0

# Pages live + serving
for p in index coverage drift audit; do
  curl -sI "https://prekzursil.github.io/quality-zero-platform/$p.html" \
    | head -1
done   # all four must show HTTP/1.1 200

# No open alert:* issues
gh issue list -R Prekzursil/quality-zero-platform \
  --search "label:alert:*" --json number,title
# expected: []

# All 15 consumer repos green on main
for slug in $(yq '.repos[].slug' inventory/repos.yml -r); do
  state=$(gh run list -R "$slug" --branch main \
    --workflow "Quality Zero Platform" --limit 1 \
    --json conclusion --jq '.[0].conclusion // "(none)"')
  echo "$slug: $state"
done   # every line should print "success"

# Codecov per-flag visibility on event-link (multi-flag canary)
# (manual check — visit https://app.codecov.io/gh/Prekzursil/event-link
#  and confirm SEPARATE rows for backend, ui)
```

When all five sanity checks pass + the four absolute-done bullets in
`.beads/context/execution-state.md`'s "Absolute-done remaining" list
are literally true, the loop's completion promise can emit.
