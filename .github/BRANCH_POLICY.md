# Branch Policy

`quality-zero-platform` uses feature branches and pull requests only.

## Local Policy

- develop on topic branches
- verify with `bash scripts/verify`
- keep generated ruleset payloads in the same change as the profile update that produced them

## Automation Policy

- reusable workflows in this repo never push to a governed repo's default branch
- remediation branches must use `codex/fix/<context>/<shortsha>`
- backlog branches must use `codex/backlog/<tool>`

## Review Policy

- pull requests require human review
- policy and workflow changes should include validation or fixture coverage
