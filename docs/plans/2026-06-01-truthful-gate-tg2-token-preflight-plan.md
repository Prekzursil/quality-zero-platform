# TG-2 — Token-Rotation Preflight (implementation plan, rev 2)

**Parent design:** `docs/plans/2026-06-01-truthful-gate-subsystem-design.md`
(design-review-gate PASSED, Addenda A–D). **Branch:** `feat/truthful-gate-subsystem`.
**Execution:** metaswarm orchestrated (decision E). **First ship of the program.**
**rev 2:** folds the 5 must-fix items + notes from the single-round
plan-review-gate (`PASS_WITH_REQUIRED_FIXES`, `ready_to_build: true`).

## Why TG-2 first

Makes the campaign's master blocker — rotated SaaS tokens — **loud instead
of silent**, before any adapter relies on live dashboard reads. Small,
self-contained, touches no schema (no `audit`/A2 sequencing), verifiable.

## Contract

For every provider that is **block-severity in the resolved profile AND has
a read-capable token wired in platform secrets**, perform a cheap
authenticated probe and classify:

| Outcome | Condition | Exit | Alert |
|---|---|---|---|
| `ok` | authenticated probe → 2xx | 0 | — |
| `secret_missing` | required secret absent from env | 1 | `alert:secret-missing` (EXISTING) |
| `unreadable` | secret present but rejected (HTTP 401/403) OR unreachable/allowlist-reject | **2** | **`alert:scanner-unavailable` (NEW)** |

**Exit precedence:** any `unreadable` dominates → process exit 2 (even if a
`secret_missing` also occurred). Both are non-zero (fail-closed); precedence
is for determinism + branch coverage.

## Provider sets (enumerated; resolves plan-review must-fix #4/#5)

**AUTH-PROBED (4 — read-capable tokens, live-exit-0 set):**
| provider | secret | probe (confirm exact path in TDD) | host suffix |
|---|---|---|---|
| `sonarcloud` | `SONAR_TOKEN` | `GET /api/authentication/validate` | `sonarcloud.io` |
| `codacy` | `CODACY_API_TOKEN` | `GET /api/v3/user` | `codacy.com` |
| `sentry` | `SENTRY_AUTH_TOKEN` | `GET /api/0/` | `sentry.io` |
| `deepscan` | `DEEPSCAN_API_TOKEN` | mirror `check_deepscan_zero.py` | `deepscan.io` |

**EXEMPT (checked-in set, NOT auth-probed — but never silently skipped):**
`codeql`, `dependabot`, `semgrep`, `qlty_check`, `socket_pr_alerts`
(in-CI / GitHub-native — no external auth probe); `codecov`
(`CODECOV_TOKEN` is upload-only — v2 read returns 401/403, verified in
`validate_codecov_flags.py:254-263` — so it has no read-capable whoami);
`deepsource_visible` (`DEEPSOURCE_DSN` is a Sentry-style upload DSN; the
gate reads via HTML scrape — the GraphQL+DSN truthful read is **TG-1**
scope, A.CB-5). Each EXEMPT entry carries an inline reason comment.

**`run_preflight` must RAISE/diagnose loudly** on any block-severity scanner
that is in NEITHER `PROVIDER_PROBES` NOR `EXEMPT` (no silent skip — this is
the truth-model's north-star #2). Tested.

## Deliverables

1. `scripts/quality/truth/__init__.py` + `scripts/quality/truth/preflight.py`:
   - `ProbeResult` frozen dataclass `{provider, outcome: Literal["ok","secret_missing","unreadable"], http_status: int|None, diagnostic: str}`.
   - `PROVIDER_PROBES` table (4 entries above) + `EXEMPT_BLOCK_SCANNERS` set (with reasons).
   - `probe_provider(provider, *, env)` — routes through
     **`from scripts.security_helpers import load_json_https`** (NOT
     `scripts/quality/...`; use the `parents[2]` `sys.path` bootstrap as
     other gates do). `load_json_https` RAISES `urllib.error.HTTPError` on
     status ≥ 400. Exception taxonomy (for 100% branch coverage):
     `except HTTPError as e → unreadable, http_status=e.code`;
     `except (URLError, OSError, TimeoutError, ValueError, ssl.SSLError) →
     unreadable, http_status=None` (ValueError = `normalize_https_url`
     rejecting a non-allowlisted host / non-HTTPS URL). Auth via
     `Authorization: Bearer <token>` header (mirror `check_deepscan_zero.py`),
     never in the URL. **`allowed_host_suffixes` is MANDATORY per provider**
     (empty set silently disables the SSRF guard).
   - **Token non-leak:** `diagnostic` = `f"{provider}: HTTP {status}"` /
     `f"{provider}: unreachable"` only — never the token value AND never the
     full URL/query string (A.CB-8 clause 4).
   - `run_preflight(profile, *, env) -> list[ProbeResult]` — probes
     block-severity providers in `PROVIDER_PROBES`; raises on unmapped
     non-exempt block scanners.
   - CLI `main(argv)`: `--profile <slug>`, `--open-alerts`. Exit 2 if any
     `unreadable`; else 1 if any `secret_missing`; else 0.
2. `scripts/quality/alerts.py`: add `AlertType.SCANNER_UNAVAILABLE`
   (`alert:scanner-unavailable`, dedupe-by-title), mirroring the existing
   `MISSING_SCANNER_AUTH` (`alert:secret-missing`). **Coverage note:**
   `alerts.py` is already in `sonar.coverage.exclusions` and NOT in
   coverage-source; the new enum member is **unit-tested via
   `tests/test_alerts.py`** but is NOT coverage-enforced (do NOT pull the
   whole file into coverage-source — out of scope). The "100% per
   `.coverage-thresholds.json`" claim applies ONLY to `truth/*`.
3. **`pyproject.toml`** (now in scope): add `scripts.quality.truth.preflight`
   + `scripts.quality.truth.__init__` (dotted form, mirroring
   `fleet_inventory`) to `[tool.coverage.run].source`.
4. **`sonar-project.properties`**: do **NOT** add `truth/*` to
   `sonar.coverage.exclusions` (the `test_exclusions_match_coverage_source_complement`
   contract requires exclusions to be the EXACT complement of
   coverage-source — an in-source module must be absent from exclusions; it
   auto-passes once truth/ is in `[tool.coverage.run].source`). No edit
   needed unless the contract test says otherwise.
5. **Cron (`.github/workflows/scheduled-alerts.yml`):** add a **dedicated
   non-dry-run preflight step/job** whose `env` injects the 4 probed
   secrets (`SONAR_TOKEN`, `CODACY_API_TOKEN`, `SENTRY_AUTH_TOKEN`,
   `DEEPSCAN_API_TOKEN`) and runs `preflight --profile quality-zero-platform
   --open-alerts` with `QZ_DRY_RUN: 'false'` (the existing digest dispatch
   stays dry-run). Without injected secrets the step would see all-absent →
   only ever `secret_missing` → never the rotation case.
6. Tests `tests/test_truth_preflight.py` (+ `tests/test_alerts.py` delta):
   TDD, **100% line+branch on `truth/*`**.

## TDD order (red → green → refactor)

1. `test_probe_ok_when_authenticated` (mock `load_json_https` → dict) → happy path.
2. `test_probe_unreadable_on_http_401_403` (mock raises `HTTPError(code=401/403)`) → `unreadable`, http_status set.
3. `test_probe_unreadable_on_unreachable` (mock raises `URLError`/`TimeoutError`/`OSError`) → `unreadable`, http_status None.
4. `test_probe_unreadable_on_allowlist_reject` (mock raises `ValueError`) → `unreadable`, None.
5. `test_probe_secret_missing_when_env_absent` → `secret_missing`, no network call.
6. `test_diagnostic_never_contains_token_or_url` (assert token literal AND host/query NOT in diagnostic).
7. `test_run_preflight_probes_only_block_severity` (info/non-block skipped).
8. `test_run_preflight_raises_on_unmapped_block_scanner` (block scanner not in PROBES∪EXEMPT → raise/diagnose).
9. `test_run_preflight_skips_exempt_block_scanner_with_reason` (exempt → not probed, recorded, not silent).
10. `test_main_exit_2_on_unreadable / exit_1_on_secret_missing / exit_0_on_all_ok / unreadable_dominates_secret_missing`.
11. `test_main_open_alerts_opens_scanner_unavailable` (mock `open_alert_issue`).
12. `test_alert_type_scanner_unavailable_label_and_dedupe` (in test_alerts.py).
13. Refactor for `lizard -C 15` (extract probe-spec table; keep functions ≤ ~15 CCN).

## Acceptance (verified, not believed)

- **Local (agent verifies before commit):** full unittest suite green;
  **100% line+branch on `truth/preflight.py` + `truth/__init__.py`** (per
  `.coverage-thresholds.json`); `bash scripts/verify` exit 0 (incl. the
  sonar-exclusions contract test — proves the coverage-source edit is
  consistent); `lizard scripts/quality/truth -C 15` clean; ruff/flake8/
  pylint/bandit/semgrep clean on touched files.
- **CI (verified on the PR, not locally — provider secrets live only in CI):**
  `preflight --profile quality-zero-platform` exits 0 against the 4 live
  auth-probed tokens; the 7 EXEMPT block scanners are skipped-with-reason
  (not silently). A deliberately bad token (CI test) → exit 2 +
  `alert:scanner-unavailable` (deduped), NOT `secret-missing`.

## Scope guards

- **Files:** new `scripts/quality/truth/{__init__,preflight}.py`,
  `tests/test_truth_preflight.py`; minimal deltas to `scripts/quality/alerts.py`,
  `tests/test_alerts.py`, `pyproject.toml` (coverage-source),
  `.github/workflows/scheduled-alerts.yml`, and `sonar-project.properties`
  only if the contract test requires.
- **Out of scope (recorded deferrals, NOT omissions):** the full Truth
  Source contract / adapter refactor (TG-1); `audit` deletion + baseline
  (TG-3); reconciliation (TG-4). **Per A.CB-8 clause 3 + A.CB-1 Milestone 2,
  the `DRIFT_SYNC_PAT` authenticated-whoami probe and the SonarCloud
  Auto-Analysis-OFF check are M2 (operator-gated) preflight extensions —
  explicitly deferred, not part of TG-2's M1 deliverable.**
- No `--no-verify`; stay on `feat/truthful-gate-subsystem`; never touch main.
