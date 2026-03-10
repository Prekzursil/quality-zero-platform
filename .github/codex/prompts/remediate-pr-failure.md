# Remediate PR Failure

You are operating inside a strict-zero governed repository.

Rules:

- Treat missing external statuses as policy drift, provider drift, or secret drift before changing code.
- Never push directly to the default branch.
- Use `codex/fix/<context>/<shortsha>` branch names only.
- Preserve public check names during phase-1 migration.
- Re-run the repo's canonical verify command before concluding the work is complete.
