# TG-2 — Token-Rotation Preflight (implementation plan)

**Parent design:** `docs/plans/2026-06-01-truthful-gate-subsystem-design.md`
(design-review-gate PASSED, Addenda A–D). **Branch:** `feat/truthful-gate-subsystem`.
**Execution:** metaswarm orchestrated (decision E). **First ship of the program.**

## Why TG-2 first

It makes the campaign's master blocker — rotated SaaS tokens — **loud
instead of silent**, before any adapter relies on live dashboard reads. It
is small, self-contained, touches no schema (no `audit` deletion → no A2
sequencing question), and is fully verifiable with the platform's own live
tokens. Shipping it proves the truth-model's fail-closed primitive end to end.

## Goal / contract

A preflight that, for every provider at `severity: block` in a resolved
profile, performs a **cheap authenticated probe** (whoami/validate/ping) and
classifies each as:

| Outcome | Condition | Result |
|---|---|---|
| `ok` | authenticated probe succeeds | provider may run |
| `secret_missing` | required secret absent from env | BLOCK + `alert:secret-missing` (EXISTING type) |
| `unreadable` | secret present but rejected (401/403) or API unreachable | **BLOCK (exit 2) + `alert:scanner-unavailable` (NEW type)** |

`unreadable` is the rotated-token case: a present-but-stale token must
**never** silently degrade to a pass. Distinct from `secret_missing`
(absent secret) so the operator sees *which* failure it is.

## Deliverables

1. **`scripts/quality/truth/__init__.py`** + **`scripts/quality/truth/preflight.py`**
   - `ProbeResult` frozen dataclass: `{provider, outcome: Literal["ok","secret_missing","unreadable"], http_status: int|None, diagnostic: str}`.
   - `PROVIDER_PROBES`: map of provider-key → probe spec (env var name +
     authenticated endpoint + expected-success predicate). Cover the
     block-severity providers that have a token: `codacy`, `codecov`,
     `deepsource`, `sonarcloud`, `sentry`, `deepscan`, `snyk`.
   - `probe_provider(provider, *, env) -> ProbeResult`: routes through
     `security_helpers.load_bytes_https` (HTTPS-only + host allowlist +
     SSRF guard — reuse, do NOT hand-roll urllib). Auth via `Authorization:
     Bearer`/token header per provider (mirror `check_deepscan_zero.py:86`).
   - `run_preflight(profile, *, env) -> list[ProbeResult]`: probes only
     providers at `severity: block` in the resolved profile.
   - **Token non-leak invariant:** `diagnostic` emits only `f"{provider}: HTTP {status}"`
     / `"unreachable"` — NEVER the token value or full URL with query creds
     (mirror `codacy_zero_support.http_error_findings` "HTTP {code}" pattern).
   - CLI `main(argv)`: `--profile <slug>`; exit 0 if all `ok`; **exit 2** if
     any `unreadable`; exit 1 if any `secret_missing`. `--open-alerts` flag
     opens the matching alert issue(s) via `alerts.open_alert_issue`.
2. **`scripts/quality/alerts.py`** — add `AlertType.SCANNER_UNAVAILABLE`
   (label `alert:scanner-unavailable`, dedupe-by-title), mirroring the
   existing `SECRET_MISSING` entry. No other behavior change.
3. **Cron wiring** — add a scheduled job that runs the preflight on the
   platform's own block providers daily and opens `alert:scanner-unavailable`
   on a rotation-induced failure within a day. Reuse the existing
   `scheduled-alerts.yml` cron surface (add a preflight step) rather than a
   new workflow, unless the step coupling is awkward.
4. **`sonar-project.properties`** — add the new `truth/*.py` modules to
   `sonar.coverage.exclusions` (per the `test_exclusions_match_coverage_source_complement`
   contract — a NEW script not listed fails the pre-push verify).
5. **Tests** `tests/test_truth_preflight.py` (+ `tests/test_alerts.py` delta):
   TDD, **100% line+branch** on the new modules.

## TDD order (red → green → refactor)

1. RED: `test_probe_ok_when_authenticated` (mock 200) → implement `probe_provider` happy path.
2. RED: `test_probe_unreadable_on_401_403` (mock 401/403) → `unreadable` + http_status.
3. RED: `test_probe_unreadable_on_unreachable` (mock URLError/timeout) → `unreadable`, status None.
4. RED: `test_probe_secret_missing_when_env_absent` → `secret_missing` (no network call).
5. RED: `test_diagnostic_never_contains_token` (assert token string not in any diagnostic) → non-leak.
6. RED: `test_run_preflight_only_probes_block_severity` (info/absent scanners skipped).
7. RED: `test_main_exit_2_on_unreadable / exit_1_on_secret_missing / exit_0_on_all_ok`.
8. RED: `test_main_open_alerts_opens_scanner_unavailable` (mock `open_alert_issue`).
9. RED (alerts): `test_alert_type_scanner_unavailable_label_and_dedupe`.
10. Refactor for `lizard -C 15` (keep each function ≤ ~15 CCN; extract probe-spec table).

## Acceptance (verified, not believed)

- `python -m scripts.quality.truth.preflight --profile quality-zero-platform`
  exits 0 against the live platform tokens (all 8 secrets present, refreshed
  2026-05-31) — **a live run, not just mocked tests.**
- Forcing a bad token (test env) → exit 2 + `alert:scanner-unavailable`
  opened (deduped), NOT `secret-missing`.
- `python -m unittest discover -s tests -p 'test_*.py'` green; 100%
  line+branch on `truth/preflight.py` + `truth/__init__.py` + the `alerts.py`
  delta (per `.coverage-thresholds.json`).
- `bash scripts/verify` exit 0 (incl. the sonar-exclusions contract test).
- `lizard scripts/quality/truth -C 15` clean.
- Linters clean on touched files (ruff/flake8/pylint/bandit/semgrep).

## Scope guards

- **In scope:** preflight probe + `unreadable` classification + new alert
  type + cron + tests + sonar-exclusions.
- **Out of scope (later TGs):** the full Truth Source contract / adapter
  refactor (TG-1); `audit` deletion + baseline (TG-3); reconciliation (TG-4).
  TG-2 only adds the *preflight*; it does not refactor the `check_*_zero.py`
  verdict path.
- No `--no-verify`; stay on `feat/truthful-gate-subsystem`; new files only +
  the minimal `alerts.py` / `scheduled-alerts.yml` / `sonar-project.properties`
  deltas.
