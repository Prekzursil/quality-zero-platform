# Quality Rollup Guide

## What is the Quality Rollup?

The Quality Rollup is an aggregated report produced by the `rollup_v2` pipeline.
It normalizes findings from multiple static analysis providers (QLTY, SonarCloud,
Codacy, DeepSource, DeepScan, Sentry, Dependabot, and secrets scanning) into a
single canonical format, deduplicates them, generates patches where possible, and
renders a multi-view markdown report suitable for PR comments.

## How to Read the Report

### Provider Summary Table

The top of the report shows a table with per-provider finding counts broken down
by severity. Use this to identify which providers are reporting the most issues.

### By-File View (Default)

Findings are grouped by file path, sorted by count (most findings first). Each
finding shows severity, category, provider links, message, and a diff patch if
one was generated.

### Alternate Views

Collapsed sections offer alternate groupings:

- **By Provider** -- findings grouped by the tool that detected them.
- **By Severity** -- findings grouped from critical down to info.
- **Autofixable Only** -- findings that have a deterministic or LLM-generated patch.

### Severity Meanings

| Severity | Meaning |
|----------|---------|
| Critical | Security vulnerability or data-loss risk requiring immediate action |
| High | Bug or significant quality issue |
| Medium | Maintainability concern |
| Low | Style or minor suggestion |
| Info | Informational observation |

## What to Do with a Patch

Patches in the report are unified diffs. If a finding shows a `diff` block, you
can apply it directly:

```bash
git apply --check <patch-file>  # dry-run
git apply <patch-file>          # apply
```

Review the patch before applying. Deterministic patches have high confidence;
LLM-generated patches should be reviewed more carefully.

## Links

- [Finding Schema v1](schemas/qzp-finding-v1.md)
- [JSON Schema](schemas/qzp-finding-v1.json)
- [Report a format issue](https://github.com/user/quality-zero-platform/issues/new?labels=rollup-format)
