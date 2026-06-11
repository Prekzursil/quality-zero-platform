# Strict-Zero Operator Checklist

This document captures the per-repo and per-platform configuration that
must be in place for the strict-zero contract to actually red-block on
findings, complexity / duplication drift, and missing coverage. Once a
repo is fully checked, every silent-pass mechanism documented in
[QUALITY-GATES.md](QUALITY-GATES.md) is closed.

## What strict-zero means here

The contract is: **any finding, any threshold breach, or any missing
coverage upload red-blocks both the PR and the default branch — no
exceptions, no ``continue-on-error``, no ``audit`` mode**. Every fleet
repo is held to the same bar.

## Per-repo secrets (Settings → Secrets and variables → Actions)

Each fleet repo needs every secret below set, OR the corresponding lane
will red-block until the secret arrives. That's by design — missing
secrets are not silently tolerated.

| Secret | Used by | What happens when missing |
|--------|---------|---------------------------|
| ``CODECOV_TOKEN`` | Codecov upload (always-on) | Codecov upload step fails |
| ``CODACY_API_TOKEN`` | Codacy Zero gate (issue / threshold queries) | Codacy gate fails with ``CODACY_API_TOKEN is missing`` |
| ``CODACY_PROJECT_TOKEN`` | Codacy coverage publish | ``Either a project or account API token must be provided`` + lane fails |
| ``DEEPSOURCE_DSN`` | DeepSource coverage publish | Lane fails with explicit ``DEEPSOURCE_DSN is missing`` preflight error |
| ``SONAR_TOKEN`` | Sonar Zero gate + coverage publish | Sonar lane fails |
| ``SEMGREP_APP_TOKEN`` (optional) | Semgrep ``ci`` mode | Falls back to ``semgrep scan --config auto`` (less rule coverage but still produces SARIF) |
| ``DEEPSCAN_API_TOKEN`` (optional) | DeepScan ``open_issues`` policy mode | Required when ``DEEPSCAN_POLICY_MODE=open_issues`` |

### Where to find each token

- **Codacy**: [app.codacy.com](https://app.codacy.com) → repo → Settings → Coverage → "Project API Token" (project-scoped). Account API token is under Account → API tokens.
- **DeepSource**: [app.deepsource.com](https://app.deepsource.com) → repo → Settings → Reporting → DSN.
- **Sonar**: [sonarcloud.io](https://sonarcloud.io) → My Account → Security → Generate token (project-scoped).
- **Codecov**: [app.codecov.io](https://app.codecov.io) → repo → Settings → Tokens → Repository Upload Token.
- **Semgrep**: [semgrep.dev](https://semgrep.dev) → Settings → Tokens (App Token).

## Per-repo variables (Settings → Secrets and variables → Actions → Variables)

| Variable | Used by | When required |
|----------|---------|---------------|
| ``DEEPSCAN_POLICY_MODE`` | DeepScan Zero gate | Optional — defaults to ``github_check_context``. Set to ``open_issues`` when DeepScan GitHub App isn't installed |
| ``DEEPSCAN_OPEN_ISSUES_URL`` | DeepScan Zero gate (open_issues mode) | Required when ``DEEPSCAN_POLICY_MODE=open_issues`` |
| ``SENTRY_ORG`` | Sentry Zero gate | Optional |
| ``SENTRY_PROJECT`` | Sentry Zero gate | Optional |

## Per-repo cloud-platform integrations

These need to be installed/configured on the cloud platform side (not
GitHub-secret level):

- **DeepSource GitHub App**: install on each repo so each push gets a
  ``DeepSource: <language>`` GitHub status check. Without this, the
  DeepScan-style "missing status = red-block" guard fires.
- **DeepScan GitHub App**: install on each JavaScript/TypeScript repo
  (or set ``DEEPSCAN_POLICY_MODE=open_issues`` repo variable as the
  alternative).
- **Codacy GitHub App**: must be added to the org / repo so coverage +
  issues sync.
- **Sonar Cloud project**: imported from GitHub.
- **qlty.sh project**: enable the project at [qlty.sh](https://qlty.sh)
  and link the GitHub repo.

## Per-repo config files

Each repo needs the following files at root for analyser scope to match
across platforms:

- ``.codacy.yml`` — Codacy exclude_paths + per-engine settings
- ``.deepsource.toml`` — DeepSource analysers + exclude_patterns
- ``.qlty/qlty.toml`` — qlty smell thresholds + exclude_patterns
- ``sonar-project.properties`` — Sonar project key, sources, exclusions
- ``codecov.yml`` — Codecov flag config
- ``.semgrepignore`` (optional) — when ``semgrep scan --config auto``
  produces false positives that inline ``# nosem`` directives can't
  reliably suppress

## QZP-specific carve-out (currently active)

The platform itself (``Prekzursil/quality-zero-platform``) is
deliberately running with ``profiles/repos/quality-zero-platform.yml``
``issue_policy.mode: audit``. This was set when QZP had 838 Codacy /
1116 DeepSource / 110 Sonar accumulated issues — fixing them all was
out of scope for the v2 rollout.

To move QZP itself onto strict-zero:

1. Triage existing findings (see [QUALITY-GATES.md](QUALITY-GATES.md)).
2. Set ``issue_policy.mode: zero`` (or remove the override entirely).
3. The platform's own CI will then enforce the same contract every
   consumer repo is held to — including red-blocking on its current
   875 DeepSource issues, 71 qlty.sh issues, etc.

## Verification flow per repo

After completing the checklist on a repo:

1. Open a no-op PR against the repo's main branch.
2. Verify each of these GitHub status checks reports ``success``:
   - ``shared-scanner-matrix / Coverage 100 Gate``
   - ``shared-scanner-matrix / Codacy Zero``
   - ``shared-scanner-matrix / Semgrep Zero``
   - ``shared-scanner-matrix / DeepScan Zero``
   - ``shared-scanner-matrix / DeepSource Visible Zero``
   - ``shared-scanner-matrix / QLTY Zero``
   - ``shared-scanner-matrix / Sonar Zero``
   - ``shared-codecov-analytics / Codecov Analytics``
3. If any check is missing entirely, the corresponding scanner isn't
   integrated yet (re-check the cloud-platform integrations section).
4. If any check fails, follow the error message — the strict-zero
   gates always emit actionable output pointing at either the
   missing secret or the specific finding.

## Related changelog

- [PRs #221-#224 (2026-04-29)](https://github.com/Prekzursil/quality-zero-platform/pulls?q=is%3Apr+author%3AAUTHOR+created%3A2026-04-29) closed 12 silent-pass mechanisms in the scanner-matrix gate enforcement layer. Without those PRs landed, the per-repo secret checklist alone would not produce strict-zero — silent-pass at the gate layer would still let findings through.
