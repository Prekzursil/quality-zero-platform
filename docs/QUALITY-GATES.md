# QZP — Picking Quality Gates and Thresholds

Reference for choosing the right gate severities, thresholds, and
scanner enablement per repo. Every lever lives in
[`profiles/repos/<name>.yml`](../profiles/repos/) — no scattered
configuration across CI files.

> [!TIP]
> Onboarding a new repo? Start with [`docs/ONBOARDING.md`](ONBOARDING.md)
> first. This document complements it by explaining *which* gates to
> pick, not *how* to wire them up.

---

## Mode levers (the big switches)

Every per-repo profile has two top-level mode fields. Pick them
together — they shape the entire gate behaviour.

### `mode.phase`

| Value | What it does | When to use |
|---|---|---|
| `shadow` | Gates run + report. Never block PRs or pushes. | Initial onboarding. Validating wiring. |
| `ratchet` | Gates block on **NEW** findings only (no-new-debt). | Onboarded repos with legacy backlog. Day 2+. |
| `absolute` | Gates block on **ANY** findings. Strict zero. | Cohort that's already at zero on every gate. |

The progression is one-way for normal flow:
**`shadow` → `ratchet` → `absolute`** — but you can always flip back
to a softer phase if a regression lands.

### `issue_policy.mode`

| Value | What it does |
|---|---|
| `audit` | Issues from scanners are **informational** — never blocking. |
| `ratchet` | Block on NEW issues since the leak period; legacy backlog is grandfathered. |
| `zero` | Block on ANY issue. Same semantics as `mode.phase: absolute` for issue-style scanners. |

These two interact. The matrix below shows the effective behaviour:

| `mode.phase` | `issue_policy.mode` | Result |
|---|---|---|
| `shadow` | (any) | Nothing blocks. Pure observation. |
| `ratchet` | `audit` | Nothing blocks (issue-style scanners are informational). |
| `ratchet` | `ratchet` | New-debt-only blocks. |
| `ratchet` | `zero` | Strict zero (ratchet on phase but not on issues — useful for stage-gating). |
| `absolute` | `audit` | Coverage / structural gates block; issue scanners informational. |
| `absolute` | `ratchet` | Strict on structural, ratchet on issues. |
| `absolute` | `zero` | Full strict zero. The endgame. |

**The platform repo itself runs `absolute` + `audit`** because it
has accumulated 838 Codacy / 1116 DeepSource / 110 Sonar findings
from before governance landed — informational issue gates while the
structural gates (coverage, security ratings) stay strict.

---

## Per-scanner severity (`scanners.<name>.severity`)

Each enabled scanner can carry one of these severities, set in the
per-repo profile:

| Severity | Effect |
|---|---|
| `block` | Findings fail the PR/push. |
| `ratchet` | NEW findings fail the PR; existing ones are warnings. |
| `info` | Findings annotate the PR but never fail. |
| `off` | Scanner doesn't run for this repo. |

The full scanner inventory the platform supports today:

| Scanner | What it catches | Default severity (in stack) |
|---|---|---|
| `codeql` | Code security vulnerabilities (CWE-tagged). | `block` |
| `sonarcloud` | Bugs, code smells, security hotspots, coverage. | `block` |
| `codacy_issues` | Style, complexity, common bugs. | `ratchet` |
| `codacy_complexity` | Cyclomatic complexity > 15. | `ratchet` |
| `codacy_clones` | Duplicate-code clones. | `info` |
| `codacy_coverage` | Coverage thresholds (separate from CodeCov). | `block` |
| `deepscan` | JS/TS-specific bug patterns. | `block` |
| `deepsource_visible` | Cross-language visible issues (open beta). | `block` |
| `semgrep` | Custom security/style rules. | `block` |
| `sentry` | Production runtime errors (issue counts). | `block` |
| `socket_pr_alerts` | Supply-chain malware in dep changes. | `block` |
| `socket_project_report` | Probabilistic SBOM project report. | `info` |
| `qlty_check` | Lint, format, security via QLTY meta-tool. | `block` |
| `dependabot` | Vulnerable dep versions. | `block` |
| `applitools` | Visual regression (UI repos only). | `block` |
| `chromatic` | UI snapshot drift (Storybook repos only). | `block` |

### How to relax a scanner

Two patterns for "this scanner is too noisy on my repo":

1. **Lower severity from `block` to `ratchet`** — accept legacy
   debt but block new findings.
2. **Lower severity from `block` to `info`** — pure observation
   while you triage the backlog.
3. **Set severity to `off`** — disable entirely. Reserve for
   scanners that don't apply (e.g. `applitools` on a backend-only
   repo).

Don't *delete* the scanner block from the profile — explicitly set
`severity: off`. That makes the choice auditable in PR review.

### How to tighten a scanner

If a scanner is at `info` or `ratchet` and the repo's backlog is
clear, bump it to `block`. The fleet drift-sync wave will detect the
profile change and (eventually) regenerate the consumer's required
contexts.

---

## Coverage thresholds

### SonarCloud `new_coverage` gate

Sonar's quality gate condition `new_coverage ≥ 80` is set on the
**SonarCloud organization** (Prekzursil), not in this repo's config.
To change it:

1. Visit <https://sonarcloud.io/organizations/prekzursil/quality_gates>.
2. Open the active gate (default: `Sonar way`).
3. Edit the `Coverage on New Code` condition. Common values:
   - `80%` — current default. Catches "no test for new code".
   - `60%` — looser. Useful while a repo is still in `ratchet`.
   - `100%` — strict. Use only on cohorts already at zero coverage gaps.

### Codecov coverage check

Each repo's `codecov.yml` (templated by the platform) sets:

```yaml
coverage:
  status:
    project:
      default:
        target: auto         # or "100%" for strict-zero coverage
        threshold: 0%        # how much regression is tolerated
    patch:
      default:
        target: 100%         # patch-level coverage; new lines must all be covered
        threshold: 0%
```

The platform's `coverage.command` and `coverage.inputs` keep the
upload wired correctly. To change the project-level target without
touching templates, set `coverage.target_overrides` in the per-repo
profile — the template renderer reads it.

### Local 100% gate (Python)

Python repos that use `coverage.py` typically pin `fail_under = 100.0`
in `pyproject.toml`. This is **separate** from the SonarCloud gate — it
fails locally during `bash scripts/verify` if any *traced* file dips
under 100%.

To loosen, change `[tool.coverage.report] fail_under` in the
consumer's pyproject (NOT the platform's). The platform doesn't try to
template pyproject.toml.

---

## Complexity threshold (Lizard / QLTY)

The platform runs `lizard -C 15` as a hard cap on cyclomatic
complexity (CCN > 15 fails). To change:

- **Per repo (rare)**: override in the per-repo profile under
  `qlty.complexity_threshold` (default 15).
- **Globally (very rare)**: change the stack profile under
  `qlty.complexity_threshold`. Affects every repo on that stack.

Why CCN 15? Empirically captures "code reviewers will probably want
this refactored" before the function turns into a maze. Bumping to
20 lets through code that's painful to test; dropping to 10 forces
splitting that's not always natural.

---

## Required contexts (branch protection)

The `required_contexts` block in the per-repo profile drives **what
GitHub branch protection actually requires**. Three sub-keys:

```yaml
required_contexts_mode: replace         # 'replace' or 'extend'
required_contexts:
  always:                               # required on push AND PR
    - shared-scanner-matrix / Coverage 100 Gate
  pull_request_only:                    # required on PR only
    - SonarCloud Code Analysis
  target:                               # what we WANT to be required eventually
    - shared-scanner-matrix / Coverage 100 Gate
    - SonarCloud Code Analysis
    - shared-scanner-matrix / Sonar Zero
```

The `target` list is the future state — when the repo is at the
desired phase, the runtime promotes `target` into `always` +
`pull_request_only` automatically.

`required_contexts_mode: replace` (recommended): the platform owns
the entire required-checks list. `extend`: legacy required-checks
are preserved and the platform adds to them. Use `extend` only on
repos with non-platform CI that ALSO must stay required.

To change branch protection:

1. Edit the per-repo profile's `required_contexts` block.
2. Open a platform PR.
3. On merge, the
   [`generated/rulesets/<repo>.json`](../generated/rulesets/) payload
   is regenerated and a separate ruleset-apply PR or admin dispatch
   pushes the new branch protection rule to the consumer.

---

## Quick gate-design recipes

### "I want to onboard a repo with lots of legacy debt"

```yaml
mode:
  phase: shadow             # initial 3 runs
issue_policy:
  mode: audit               # informational only
scanners:
  codeql: { severity: ratchet }
  sonarcloud: { severity: ratchet }
  codacy_issues: { severity: ratchet }
```

Bump to `mode.phase: absolute` + `issue_policy.mode: ratchet` after 3 green shadow runs.

### "I want strict zero on a clean repo"

```yaml
mode:
  phase: absolute
issue_policy:
  mode: zero
scanners:
  codeql: { severity: block }
  sonarcloud: { severity: block }
  codacy_issues: { severity: block }
  semgrep: { severity: block }
  qlty_check: { severity: block }
```

### "I want to disable a flaky scanner temporarily"

```yaml
scanners:
  applitools: { severity: off }     # Sentry's down for a week — disable
```

Then re-enable after the upstream issue resolves. The drift-sync
wave will pick up the change.

### "I want to inspect findings without blocking"

```yaml
scanners:
  semgrep: { severity: info }      # surface findings as PR annotations only
```

---

## See also

- [`docs/ONBOARDING.md`](ONBOARDING.md) — adding a new repo.
- [`docs/OPERATOR-RUNBOOK.md`](OPERATOR-RUNBOOK.md) — wave dispatches
  and alerts.
- [`docs/QZP-V2-DESIGN.md`](QZP-V2-DESIGN.md) — full schema reference.
