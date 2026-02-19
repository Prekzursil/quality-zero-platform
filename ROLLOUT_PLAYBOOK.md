# Fleet Baseline Lite v2 Rollout Playbook

## Adoption Steps
1. Copy baseline files into target repository root, preserving paths.
2. Replace `scripts.verify.template` with `scripts/verify` customized for the repository stack.
3. Validate `AGENTS.md` verify command and queue warning text against repo reality.
4. Open bootstrap PR named `chore/governance-wave2-bootstrap`.
5. Require green `verify` and zero unresolved review threads.
6. Merge with `squash` after non-author human approval.
7. Run `agent-label-sync.yml` on default branch once post-merge.
8. Create one pilot issue from `agent_task.yml` with `agent:ready`, `risk:low`, and area label.
9. Confirm queue transition to `agent:in-progress` and exactly one execution-contract comment.
10. Merge pilot PR after human review; only then activate Phase-2 hardening issue.

## Rollback Steps
1. Disable queue workflow by removing `agent:ready` usage and pausing agent assignment.
2. Revert bootstrap PR via a single rollback PR if governance workflow causes blocking regressions.
3. Keep branch protections intact; do not loosen protections during rollback.
4. Re-run `agent-label-sync.yml` after rollback to reconcile labels.
5. Document rollback root cause in tracker issue before attempting re-adoption.
