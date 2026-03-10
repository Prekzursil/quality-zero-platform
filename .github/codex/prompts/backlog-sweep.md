# Backlog Sweep

You are operating inside a strict-zero governed repository backlog lane.

Rules:

- Work one tool lane at a time.
- Never mix coverage, quality, security, and service-integrity changes in the same sweep.
- Never push directly to the default branch.
- Use `codex/backlog/<tool>` branch names only.
- Preserve current public check names and escalate policy drift instead of hiding it in code changes.
