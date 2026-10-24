# Code-scanning dismissals: the governing rule

"0 alerts" is only worth something if anyone can later reconstruct how it was
reached: what was **fixed**, what was **dismissed**, and why. This rule governs
QZP and every repository QZP governs. It applies to CodeQL and to any other
SARIF producer that feeds GitHub code scanning (opengrep, zizmor, ...).

## The rule

1. **Fix beats dismiss.** A dismissal is the exception. It is allowed only for
   a demonstrable false positive or a genuinely by-design pattern, and never
   as a way to reach a green badge or an "always green" target.
2. **Every dismissal carries a written rationale** that is specific to the
   finding:
   - **Security findings**: name *why the alert cannot be exploited in that
     repository's context*, that is, which input the finding is about, who
     controls it, and what it can reach. "Not exploitable" or "by design"
     alone is not a rationale. Whichever tool raised it, a finding is a
     security finding when it has a security severity, a `security`, CWE or
     OWASP tag, or comes from a security-only scanner (Bandit, gitleaks,
     Grype, OSV-Scanner, Trivy, zizmor). A Semgrep result tagged only
     `security` and `CWE-95` counts. When in doubt, treat it as one.
   - **Correctness and maintainability findings** (unused code, import
     style, ...): name why the flagged construct is intended. For example,
     "fixture of the anti-pattern `<generator>` rewrites; its test asserts
     the rewrite".
3. **Every dismissal is logged where it can be audited**, with the columns
   of the [log below](#log-quality-zero-platform):
   - QZP's own: a row in this file, in the PR that justifies it or right
     after.
   - A governed repo's: a row in that repo's own
     `docs/CODE-SCANNING-DISMISSALS.md`, the per-repo receipt.
4. **A dismissal you would repeat is a defect in the shared layer.** If the
   same alert would be dismissed in several repos because they share a
   template, a reusable workflow or other production code, or again after
   every edit, fix that shared source or the query configuration once
   instead. Intentional fixtures are the exception: a repo whose tests need
   a fixture of the flagged pattern (the input of a detector or fixer they
   assert on) dismisses it there as `used in tests`, even if other repos
   carry the same fixture. Excluding the query instead would hide the same
   pattern in production code.

## Choosing the reason

GitHub offers three reasons. Use them for what they mean:

| Reason | Use it when | Never for |
| --- | --- | --- |
| `false positive` | The query is wrong about this code: the flagged flow cannot happen. Say why (for example, the trigger can never carry untrusted input). | Code that is merely "unlikely" to be reached. |
| `used in tests` | The code exists only to exercise a detector or fixer, for example a fixture of the anti-pattern a patch generator rewrites. | Workflow or production code. |
| `won't fix` | A real, understood risk that is accepted on purpose. It needs the strongest rationale, plus an **owner** and a **review-by** date in the log. | Anything a reasonable change could fix. |

## How to dismiss

GitHub caps the dismissal comment at 280 characters. The command below
appends a 42-character log pointer, so keep the rationale core at **238
characters or fewer**.

The validator needs only Python (3.9 or later) and PyYAML. In QZP it is
`scripts/quality/check_dismissal_log.py`. A governed repository fetches it at
the platform commit its callers pin, with no QZP checkout. A pin from before
the validator existed has no such file (`curl` fails with 404); use the
platform SHA your next pin bump will move to:

```bash
# The validator (in QZP itself: qzp_validator=scripts/quality/check_dismissal_log.py).
sha="<the QZP commit your callers pin (or a later one that has the validator)>"
qzp_validator="$(mktemp -d)/check_dismissal_log.py"
curl -fsSL -o "$qzp_validator" \
  "https://raw.githubusercontent.com/Prekzursil/quality-zero-platform/$sha/scripts/quality/check_dismissal_log.py"
python -m pip install --quiet pyyaml

# 0. Which rationale does rule 2 require? Prints "security" or "correctness".
gh api repos/OWNER/REPO/code-scanning/alerts/NUMBER | python "$qzp_validator" --classify
# 1. Add the log row first (docs/CODE-SCANNING-DISMISSALS.md).
# 2. Dismiss with the rationale's core (<= 238 chars) and the log pointer.
gh api -X PATCH repos/OWNER/REPO/code-scanning/alerts/NUMBER \
  -f state=dismissed \
  -f dismissed_reason="<false positive | won't fix | used in tests>" \
  -f dismissed_comment="<rationale core>. Logged: docs/CODE-SCANNING-DISMISSALS.md"
# 3. Check the log.
python "$qzp_validator" --repo OWNER/REPO
```

Reviewers reject a dismissal PR that has no log row. They also reject a log
row that breaks rule 2, or a `won't fix` row without an owner and a
review-by date. The validator (step 3) checks the log's shape. QZP's own CI
runs it on every change, through `tests/test_dismissal_log.py`, and
`dismissal-log.yml` runs it every day, so a review-by date that passes fails
CI without waiting for an unrelated commit. That daily run also reconciles
the log with GitHub (`--dismissed-jsonl`): an alert dismissed in the UI
without a log row, or with another reason, fails it. A governed repo runs step 3
against its receipt; wiring it into the fleet's reusable gate is a follow-up.
It checks:
- one log section, with the columns and a real separator row, and one
  unbroken table: a row that lost its leading pipe, or that sits outside the
  table (before it, or cut off by a blank line), is reported rather than
  skipped;
- an alert link into *this* repository that matches its own number;
- a backticked rule and a backticked `path:line`;
- a GitHub reason and a real calendar date that is not in the future;
- a rationale and a verdict (`-`, the missing-value marker, is neither), and
  no duplicate alerts;
- an owner plus a real review-by date on every `won't fix`. Only a row
  dismissed before this rule (2026-10-24) and marked **Re-review.** or
  **Fix.** may defer them until that review;
- on any row that has one, a review-by date after the dismissal that has not
  passed. From the day after it, the check fails until the dismissal is
  re-reviewed and the row records the outcome. A false positive that rests
  on a tool limitation carries one, so the dismissal cannot become permanent
  by accident;
- the decisions table below, when present: its columns, backticked query
  ids, a real date (or `before YYYY-MM-DD`) that is not in the future, a
  decision, and one row per query (a changed decision is updated in place);
- headings and tables inside a code fence or an HTML comment are examples
  or hidden text, not the document;
- with `--codeql-config` (default `.github/codeql/codeql-config.yml`, skipped
  when absent): for every query the configuration excludes, a well-formed
  decision that starts with **Excluded**. A malformed row documents nothing,
  and neither does a decision not to exclude. A filter that excludes by
  anything other than a query id is rejected, since it could not be listed
  one decision at a time.

## Query configuration decisions

Rule 4 also covers a dismissal that recurs over *time*: fix it once in the
query configuration (`.github/codeql/codeql-config.yml`, with the reason next
to the filter) or, better, in the code. Every decision is listed here, and
the check above fails while an excluded query id has no row.

| Query | Decided | Decision |
| --- | --- | --- |
| `actions/untrusted-checkout/medium`, `actions/cache-poisoning/poisonable-step` | 2026-10-24 | **Fixed in the code by #336, not excluded.** Each reusable workflow checked out QZP's scripts at a caller-supplied `platform_ref`. Every action-pin bump re-fingerprinted those alerts, and in QZP's own `pull_request` flow the ref is the PR head. The platform is now checked out at the workflow file's own commit (`job.workflow_repository` / `job.workflow_sha`), and every alert on in-repo-called workflows, including all 7 high, is gone from main. CodeQL does not model `job.workflow_sha` yet, so 6 own-commit checkouts in externally-called reusables are still flagged. They are dismissed below as demonstrable false positives, and the steps are named so the fingerprint survives Dependabot SHA bumps. CodeQL's fingerprint hashes the first 100 non-space characters from the step's first line, so naming the steps re-identified five of them once (#306-#310, logged below as the same findings). A comment line after each name keeps the pinned SHA out of that window from #345 on. **These dismissals expire.** They rest only on CodeQL not modelling `job.workflow_sha`. Each row carries review-by 2027-01-24, and the check fails from the day after it. Once CodeQL models the property the alerts should close on their own; one that stays open is a real finding to fix. Excluding the query was considered and rejected. |
| `py/clear-text-storage-sensitive-data`, `py/clear-text-logging-sensitive-data` | before 2026-10-24 | **Excluded (pre-policy); under re-review.** One verified false positive in `scripts/quality/secrets_sync.py` (its records pass `_sanitise_audit_record`'s allow-list) led to a GLOBAL exclusion. That silences both queries for all code, not just the verified call site. |

## Log (quality-zero-platform)

Seeded on 2026-10-24 from the code-scanning API (all 33 dismissed alerts at
that time). Rows marked **Re-review** or **Fix** predate this rule and do not
yet meet it. Each is worked through as a fix, or re-dismissed with a
compliant rationale and the correct category. Rows #297-#302 are the first
dismissals made under this rule.

| Alert | Rule | Location (at dismissal) | Reason | Dismissed | Rationale (as recorded) | Owner | Review by | Verdict under this rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [#25](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/25) | `actions/untrusted-checkout/critical` | `.github/workflows/reusable-remediation-loop.yml:125` | used in tests | 2026-04-27 | reusable-remediation-loop runs on trusted private runners only (codex-trusted label, see docs/codex-private-runner-auth.md). workflow_run + checkout + cache are by design; writes scoped to codex/fix/<slug>/<sha> via security_class_guard. | - | - | **Re-review.** Pre-policy. `used in tests` is the wrong category for workflow code, and the rationale must name the exploit boundary. |
| [#26](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/26) | `actions/cache-poisoning/poisonable-step` | `.github/workflows/reusable-remediation-loop.yml:59` | used in tests | 2026-04-27 | reusable-remediation-loop runs on trusted private runners only (codex-trusted label, see docs/codex-private-runner-auth.md). workflow_run + checkout + cache are by design; writes scoped to codex/fix/<slug>/<sha> via security_class_guard. | - | - | **Re-review.** Pre-policy. `used in tests` is the wrong category for workflow code, and the rationale must name the exploit boundary. |
| [#27](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/27) | `actions/cache-poisoning/poisonable-step` | `.github/workflows/reusable-remediation-loop.yml:61` | used in tests | 2026-04-27 | reusable-remediation-loop runs on trusted private runners only (codex-trusted label, see docs/codex-private-runner-auth.md). workflow_run + checkout + cache are by design; writes scoped to codex/fix/<slug>/<sha> via security_class_guard. | - | - | **Re-review.** Pre-policy. `used in tests` is the wrong category for workflow code, and the rationale must name the exploit boundary. |
| [#28](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/28) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-backlog-sweep.yml:38` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#29](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/29) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-codeql.yml:52` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#30](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/30) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-quality-zero-gate.yml:35` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#31](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/31) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-ruleset-sync.yml:27` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#32](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/32) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-remediation-loop.yml:50` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#57](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/57) | `py/import-and-import-from` | `tests/test_check_codacy_zero_retry.py:17` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#82](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/82) | `py/import-and-import-from` | `tests/test_check_codacy_zero_edge.py:11` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#100](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/100) | `py/import-and-import-from` | `tests/test_check_codacy_zero.py:9` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#109](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/109) | `py/unreachable-statement` | `tests/quality/rollup_v2/fixtures/patches/tab_vs_space/case_01.input.py:3` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#110](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/110) | `py/unreachable-statement` | `tests/quality/rollup_v2/fixtures/patches/dead_code/case_01.input.py:3` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#111](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/111) | `py/unused-import` | `tests/quality/rollup_v2/fixtures/patches/unused_import/case_01.input.py:1` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#112](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/112) | `py/unused-import` | `tests/quality/rollup_v2/fixtures/patches/wrong_import_order/case_01.input.py:1` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#113](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/113) | `py/unused-import` | `tests/quality/rollup_v2/fixtures/patches/wrong_import_order/case_01.input.py:2` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#114](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/114) | `py/unused-import` | `tests/quality/rollup_v2/fixtures/patches/wrong_import_order/case_01.input.py:3` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#126](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/126) | `py/modification-of-default-value` | `tests/quality/rollup_v2/fixtures/patches/mutable_default/case_01.input.py:2` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#127](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/127) | `py/catch-base-exception` | `tests/quality/rollup_v2/fixtures/patches/broad_except/case_02.input.py:3` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#128](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/128) | `py/catch-base-exception` | `tests/quality/rollup_v2/fixtures/patches/broad_except/case_03.input.py:3` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#129](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/129) | `py/empty-except` | `tests/quality/rollup_v2/fixtures/patches/broad_except/case_02.input.py:3` | won't fix | 2026-04-27 | Test fixture intentionally containing the anti-pattern this rule detects. The QZP rollup_v2 patch generators (mutable_default, broad_except, dead_code, empty_except, etc.) are tested against these fixtures — fixing the fixtures would make the patch generators untestable. | - | - | **Re-review.** The fixture IS the anti-pattern its patch generator rewrites, which is by design. But it is recorded as `won't fix`, and must be re-dismissed as `used in tests`. |
| [#138](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/138) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-applitools.yml:46` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#139](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/139) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-chromatic.yml:47` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#166](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/166) | `py/import-and-import-from` | `tests/test_fleet_inventory.py:928` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#167](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/167) | `py/import-and-import-from` | `tests/test_fleet_inventory.py:963` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#168](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/168) | `py/import-and-import-from` | `tests/test_fleet_inventory.py:840` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#169](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/169) | `py/import-and-import-from` | `tests/test_fleet_inventory.py:882` | used in tests | 2026-04-27 | Test uses both import styles: from-import for symbol access + module-as-name for runtime monkey-patching. Refactoring to unittest.mock.patch is a larger change; current dual-import pattern is clearer. | - | - | **Fix.** Pre-policy. One import style per module is a small refactor, so it must not be dismissed. |
| [#173](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/173) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-quality-zero-bypass.yml:61` | used in tests | 2026-04-27 | QZP reusable workflow invoked via workflow_call. Consumer's caller workflow controls the trigger; permissions blocks scope writes per docs/QZP-V2-DESIGN.md A.2.4 trusted-runner contract. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#245](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/245) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:102` | false positive | 2026-07-11 | FP: reusable workflow_call, invoked ONLY via plain pull_request (never pull_request_target), so fork PRs get no secrets / no privileged untrusted-code exec; persist-credentials:false already set. CodeQL heuristic flags any ref-bearing checkout in workflow_call. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#246](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/246) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:107` | false positive | 2026-07-11 | FP: reusable workflow_call, invoked ONLY via plain pull_request (never pull_request_target), so fork PRs get no secrets / no privileged untrusted-code exec; persist-credentials:false already set. CodeQL heuristic flags any ref-bearing checkout in workflow_call. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#247](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/247) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:664` | false positive | 2026-07-11 | FP: reusable workflow_call, invoked ONLY via plain pull_request (never pull_request_target), so fork PRs get no secrets / no privileged untrusted-code exec; persist-credentials:false already set. CodeQL heuristic flags any ref-bearing checkout in workflow_call. | - | - | **Re-review.** Pre-policy. The flagged step follows a platform checkout at a caller-supplied `platform_ref`. Fixed at the root by checking the platform out at the workflow's own commit (`job.workflow_sha`). |
| [#272](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/272) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-codeql.yml:53` | false positive | 2026-10-24 | Same finding as triaged #29/#31; only the action ref changed (tag to SHA pin). Checks out QZP's own platform scripts at the caller's platform_ref, never PR head code; contents: read, no secrets. A fork that can change the ref already controls its own workflow. | - | - | **Re-review.** The recorded rationale is wrong for QZP's own `pull_request` flow: there `platform_ref` IS the PR head, so PR code runs as the platform. Same root fix (`job.workflow_sha`). |
| [#273](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/273) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-ruleset-sync.yml:27` | false positive | 2026-10-24 | Same finding as triaged #29/#31; only the action ref changed (tag to SHA pin). Checks out QZP's own platform scripts at the caller's platform_ref, never PR head code; contents: read, no secrets. A fork that can change the ref already controls its own workflow. | - | - | **Re-review.** The recorded rationale is wrong for QZP's own `pull_request` flow: there `platform_ref` IS the PR head, so PR code runs as the platform. Same root fix (`job.workflow_sha`). |
| [#302](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/302) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:671` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. | Prekzursil | 2027-01-24 | Kept, and **expires**: dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). Once it does, the alert should close on its own; one still open is a real finding to fix. Re-check by the review-by date. |
| [#301](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/301) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:113` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. | Prekzursil | 2027-01-24 | Kept, and **expires**: dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). Once it does, the alert should close on its own; one still open is a real finding to fix. Re-check by the review-by date. |
| [#300](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/300) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-ruleset-sync.yml:29` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. | Prekzursil | 2027-01-24 | Kept, and **expires**: dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). Once it does, the alert should close on its own; one still open is a real finding to fix. Re-check by the review-by date. |
| [#299](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/299) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-quality-zero-bypass.yml:69` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. | Prekzursil | 2027-01-24 | Kept, and **expires**: dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). Once it does, the alert should close on its own; one still open is a real finding to fix. Re-check by the review-by date. |
| [#298](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/298) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-chromatic.yml:49` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. | Prekzursil | 2027-01-24 | Kept, and **expires**: dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). Once it does, the alert should close on its own; one still open is a real finding to fix. Re-check by the review-by date. |
| [#297](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/297) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-applitools.yml:48` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. | Prekzursil | 2027-01-24 | Kept, and **expires**: dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). Once it does, the alert should close on its own; one still open is a real finding to fix. Re-check by the review-by date. |
| [#310](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/310) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:684` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. Re-identified by #331's step names. | Prekzursil | 2027-01-24 | Kept, and **expires**: the same finding as #302, under a new fingerprint. Dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). |
| [#309](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/309) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-scanner-matrix.yml:126` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. Re-identified by #331's step names. | Prekzursil | 2027-01-24 | Kept, and **expires**: the same finding as #301, under a new fingerprint. Dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). |
| [#308](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/308) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-quality-zero-bypass.yml:69` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. Re-identified by #331's step names. | Prekzursil | 2027-01-24 | Kept, and **expires**: the same finding as #299, under a new fingerprint. Dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). |
| [#307](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/307) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-chromatic.yml:49` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. Re-identified by #331's step names. | Prekzursil | 2027-01-24 | Kept, and **expires**: the same finding as #298, under a new fingerprint. Dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). |
| [#306](https://github.com/Prekzursil/quality-zero-platform/security/code-scanning/306) | `actions/untrusted-checkout/medium` | `.github/workflows/reusable-applitools.yml:48` | false positive | 2026-10-24 | Checkout ref is job.workflow_sha: the commit of this reusable workflow file itself (GitHub job context, 2026-04). Neither the caller nor a PR head chooses it. CodeQL does not model job.workflow_sha yet. Re-identified by #331's step names. | Prekzursil | 2027-01-24 | Kept, and **expires**: the same finding as #297, under a new fingerprint. Dismissed only BECAUSE CodeQL does not model `job.workflow_sha` yet (#336). |
