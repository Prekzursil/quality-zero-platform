# Branch Protection

Apply repository rulesets to the renamed GitHub repository after the committed payloads in `generated/rulesets/` are reviewed.

## Required Checks For This Repo

- `Control Plane Verify`

## Required Checks For Governed Repos

Governed repos should consume generated payloads from `generated/rulesets/` and only require the contexts declared by their resolved profile.

## Non-Negotiables

- no direct pushes to default branches
- PR review required
- required status checks must come from generated payloads, not ad hoc UI edits
