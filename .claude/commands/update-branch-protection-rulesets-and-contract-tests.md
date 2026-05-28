---
name: update-branch-protection-rulesets-and-contract-tests
description: Workflow command scaffold for update-branch-protection-rulesets-and-contract-tests in quality-zero-platform.
allowed_tools: ["Bash", "Read", "Write", "Grep", "Glob"]
---

# /update-branch-protection-rulesets-and-contract-tests

Use this workflow when working on **update-branch-protection-rulesets-and-contract-tests** in `quality-zero-platform`.

## Goal

Regenerate branch-protection ruleset payloads and update contract/control-plane tests to reflect new enforcement or provider requirements.

## Common Files

- `profiles/repos/*.yml`
- `profiles/stacks/*.yml`
- `generated/rulesets/*.json`
- `tests/test_control_plane*.py`

## Suggested Sequence

1. Understand the current state and failure mode before editing.
2. Make the smallest coherent change that satisfies the workflow goal.
3. Run the most relevant verification for touched files.
4. Summarize what changed and what still needs review.

## Typical Commit Signals

- Edit one or more profile YAML files to change required contexts or enforcement modes.
- Regenerate the corresponding ruleset JSON files in generated/rulesets/ to match the new profile definitions.
- Update or add control-plane contract tests in tests/ to assert the new enforcement or context requirements.

## Notes

- Treat this as a scaffold, not a hard-coded script.
- Update the command if the workflow evolves materially.