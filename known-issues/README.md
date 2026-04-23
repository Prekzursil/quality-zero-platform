# Known-issues registry

Phase 4 of `docs/QZP-V2-DESIGN.md` §6. Every entry here documents a
static-analyzer false positive or a recurring code pattern that has
a verified, safe fix. QRv2 reads these entries into its Codex prompt
so the remediation loop can apply the canonical fix instead of
re-deriving it every run.

## Entry schema

Each file is a YAML document with these fields:

| field              | required | description                                                                       |
|--------------------|----------|-----------------------------------------------------------------------------------|
| `id`               | yes      | Registry id, e.g. `QZ-FP-001`. `FP` = false-positive, `CV` = coverage, etc.       |
| `title`            | yes      | Short one-line summary.                                                           |
| `description`      | yes      | Longer prose — what the analyzer says, what's actually true, why it fires.        |
| `affects`          | yes      | List of scanner IDs where this fires (e.g. `codeql`, `sonarcloud`, `codacy`).     |
| `feeds_qrv2`       | yes      | `true`/`false`. When true, QRv2 loads this entry into its Codex prompt.           |
| `fix_snippet`      | yes*     | Code snippet the remediation loop applies. Required when `feeds_qrv2: true`.      |
| `verified_at`      | yes      | ISO date when the fix was last validated against the analyzer's current rules.    |
| `verified_by`      | optional | PR number or commit SHA on the platform or a governed repo that proved the fix.   |
| `references`       | optional | Upstream issue / docs URLs.                                                       |

## Current entries

- `QZ-FP-001.yml` — CodeQL `py/ineffectual-statement` on `await <task>` inside `with suppress(...)`.
- `QZ-FP-002.yml` — SonarCloud `typescript:S3735` flagging `void asyncCall()` as unnecessary.
- `QZ-FP-003.yml` — Codacy metric engine firing on net-new CLI scripts' PR-level complexity delta.
- `QZ-CV-001.yml` — Codecov merging multiple `coverage.inputs[]` under one unflagged blob.
