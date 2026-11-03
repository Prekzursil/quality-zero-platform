# Coverage Orchestration Contract (2026-09-04)

Patterns for Architect→Editor coverage work units. No second runtime, no MCP server. Decision SSOT: [`2026-09-04-fleet-autonomy-decision-log.md`](./2026-09-04-fleet-autonomy-decision-log.md).

## Canonical stages

`Ground → Select → Impl → DeSlop → ValidateInner → LaneOuter → RepoVerify → Review → DraftPR`

Stop at draft PR (G-3). No merge.

## Change-spec (Architect → Editor)

```yaml
change_spec:
  wu_id: ms-cov-about
  stages: [Ground, Select, Impl, DeSlop, ValidateInner, LaneOuter, RepoVerify, Review, DraftPR]
  file: frontend/src/app/pages/about/about.component.ts
  anchor: { symbol: "AboutComponent" }
  intent: "100% of PR-added executable lines via diff-coverage; no product behavior change"
  before_invariant: "ValidateInner fails without GITHUB_BASE_REF or with uncovered PR-added lines"
  after_invariant: "ValidateInner pass; LaneOuter green|outer:blocked; RepoVerify pass; Review pass; DraftPR draft-only"
  acceptance: "export GITHUB_BASE_REF=main && npm run test:coverage && node scripts/diff-coverage.mjs && make verify"
  out_of_scope: ["do not change payment/checkout providers", "no secret commits", "no self-mark-done"]
```

Parse defensively when MCP tools are active (Codex `--output-schema` may be ignored). Prefer disabling MCP for typed returns when feasible.

## WorkerAdapter

See decision log. Auth probe fail-closed. Branch prefixes: `codex/fix/`, `cursor/`, `claude/fix/`.

## Escalation N=3 (G-27)

Track consecutive identical failures per WU. On 3rd: escalate to lead → re-decompose / re-route worker / ask owner. No sunk-cost grind. Max draft-PR revisions / WU = 3 (distinct counter).

## L0 lint ladder

After each edit: smallest check on changed files only. After 3 failed reflections → escalate. Full suite / e2e only at WU boundary (INNER then OUTER).

## H6 timeout contracts (docs now; build wires helpers)

Every WU uses `settle()` / `withTimeout()`. Prefer `pipeline()` over bare parallel barriers. Typed drop-receipts (no `.filter(Boolean)`). Barrier-without-timeout = lint reject. Document `H6_TIMEOUT_MS` / `H6_STALL_MS` (or explicit per-WU ms) in loop docs.

## Budget hard halt

`budget.total` **must** be set before coding fan-out. If unset → refuse fan-out. Halt when `spent() >= budget.total` OR `remaining() < floor` (e.g. 50_000). Not advisory.

## Sandbox ladder

Default: Cursor Cloud Linux Tier 0 trusted checkout. Escalate docker/modal for untrusted deps/hostile fixtures. WSL2 is Windows-host-only — not required for docs-phase coverage WUs.

## ResumeManifest + DraftPR idempotency

Path (momentstudio): `docs/coverage-runs/<run_id>/manifest/`

- Append-only `done.jsonl` records `{ts, wu_id, state, meta}` with `state ∈ {partial, done, failed}`
- Ordered terminal: `--mark-partial` → three-layer receipt + Review PASS → **DraftPR (idempotent)** → `--mark-done` **or** `--mark-failed`
- DraftPR: resolve existing draft by `wu_id`/branch/component before open; reuse URL; refuse double open
- `--mark-done` only after INNER ∧ LANE_OUTER ∧ REPO_VERIFY ∧ Review + DraftPR recorded; never from `UNIT-COMPLETE` alone
- Persist `failed[]` in NOTES; never treat failed as silent pending

## Anti-overfit

No second orchestration engine, no vector memory, no local sandbox build, no trained router. Ride QZP + AST + existing workers.
