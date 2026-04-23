# Bump recipes

Bump recipes describe fleet-wide nudges (e.g. `Node 20 -> 24`, `ubuntu-latest -> ubuntu-24.04`). They are consumed by `.github/workflows/reusable-bumps.yml` in a staged-rollout pattern.

## File naming

`profiles/bumps/<YYYY-MM-DD>-<slug>.yml` — the date keeps history sortable, the slug describes the change.

## Schema

| Field | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `name` | string | yes | — | Human-readable label (e.g. `Node 20 -> 24`). |
| `target` | list | yes | — | Non-empty list of target entries. |
| `target[].file_glob` | string | yes | — | `pathlib.Path.glob` pattern (rel. to repo root). |
| `target[].yaml_path` | string | yes | — | YAML selector applied within each matched file. |
| `target[].value` | string | yes | — | New value to write. |
| `affects_stacks` | list\[string\] | yes | — | Stack ids this bump applies to. |
| `staging_repos` | list\[owner/name\] | yes | — | Wave-1 canary repos. |
| `full_rollout_after_staging` | bool | no | `true` | Auto-roll to all affected after staging is green. |
| `rollback_on_failure` | bool | no | `true` | Revert the recipe commit + open `alert:fleet-bump-fail` on staging failure. |

## Validation

All recipes go through `scripts/quality/bumps.py::load_bump_recipe`, which enforces the above schema and raises `BumpRecipeError` on any violation. CI runs the validator as part of the quality rollup.

## Example

See [`2026-04-23-node-24.yml`](./2026-04-23-node-24.yml) for the canonical Phase 5 canary.
