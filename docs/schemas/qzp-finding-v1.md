# QZP Finding Schema v1

Schema version identifier: `qzp-finding/1`

## Migration Policy (per B.3.10)

Consumers MUST check the MAJOR portion of the schema version and fail closed on
unrecognized majors. Minor additions (new optional fields) are backward-compatible
and do not bump the major version.

## Finding Fields

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `schema_version` | `string` | No | Always `"qzp-finding/1"` for this version. |
| `finding_id` | `string` | No | Stable identifier, e.g. `"qzp-0001"`. Assigned after dedup. |
| `file` | `string` | No | Repository-relative file path, e.g. `"src/app.py"`. |
| `line` | `integer` | No | 1-based start line number. |
| `end_line` | `integer` | No | 1-based end line number (same as `line` for single-line findings). |
| `column` | `integer` | Yes | 1-based column number, if available. |
| `category` | `string` | No | Canonical category from taxonomy, e.g. `"unused-import"`. |
| `category_group` | `string` | No | One of: `"security"`, `"quality"`, `"style"`. |
| `severity` | `string` | No | One of: `"critical"`, `"high"`, `"medium"`, `"low"`, `"info"`. |
| `corroboration` | `string` | No | `"single"` if one provider, `"multi"` if deduplicated from multiple. |
| `primary_message` | `string` | No | Human-readable description of the finding. |
| `corroborators` | `array` | No | Array of Corroborator objects (at least one). |
| `fix_hint` | `string` | Yes | Optional human-readable fix suggestion. |
| `patch` | `string` | Yes | Unified diff patch, if a generator produced one. |
| `patch_source` | `string` | No | One of: `"deterministic"`, `"llm"`, `"none"`. |
| `patch_confidence` | `string` | Yes | One of: `"high"`, `"medium"`, `"low"`, or null. |
| `context_snippet` | `string` | No | Source code context around the finding (may be empty). |
| `source_file_hash` | `string` | No | Hash of the source file at scan time (may be empty). |
| `cwe` | `string` | Yes | CWE identifier if applicable, e.g. `"CWE-798"`. |
| `autofixable` | `boolean` | No | `true` if `patch_source` is not `"none"`. Derived at pipeline level. |
| `tags` | `array` | No | Array of string tags (may be empty). |
| `patch_error` | `string` | Yes | Error message if patch generation failed for this finding. |

## Corroborator Fields

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `provider` | `string` | No | Provider name, e.g. `"QLTY"`, `"SonarCloud"`. |
| `rule_id` | `string` | No | Provider-specific rule identifier. |
| `rule_url` | `string` | Yes | URL to the rule documentation. |
| `original_message` | `string` | No | The provider's original finding message. |
| `provider_priority_rank` | `integer` | No | Priority rank for merge ordering. Lower is higher priority. |

## Example

```json
{
  "schema_version": "qzp-finding/1",
  "finding_id": "qzp-0001",
  "file": "src/app.py",
  "line": 42,
  "end_line": 42,
  "column": null,
  "category": "unused-import",
  "category_group": "quality",
  "severity": "low",
  "corroboration": "single",
  "primary_message": "Unused import 'os'",
  "corroborators": [
    {
      "provider": "QLTY",
      "rule_id": "W0611",
      "rule_url": null,
      "original_message": "Unused import os",
      "provider_priority_rank": 5
    }
  ],
  "fix_hint": "Remove the unused import.",
  "patch": "--- a/src/app.py\n+++ b/src/app.py\n@@ -42,1 +42,0 @@\n-import os",
  "patch_source": "deterministic",
  "patch_confidence": "high",
  "context_snippet": "import os",
  "source_file_hash": "",
  "cwe": null,
  "autofixable": true,
  "tags": [],
  "patch_error": null
}
```
