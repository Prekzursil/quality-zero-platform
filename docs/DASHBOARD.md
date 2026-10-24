# Admin Dashboard — data flow

The operator-facing dashboard is a **read-only GitHub Pages site** published from
this repo by [`publish-admin-dashboard.yml`](../.github/workflows/publish-admin-dashboard.yml).
This document describes where every panel's data comes from, what the build
tolerates, and what the page is allowed to know. It is the map an operator
needs before extending the surface (Lane 8 extended it — see
[Operator panels](#operator-panels-lane-8) below for the three operator
questions the dashboard now answers).

## Publish pipeline (as-is)

```
push to main / hourly cron (15 * * * *) / workflow_dispatch
        │
        ▼
publish-admin-dashboard.yml · build job (ubuntu-latest)
  permissions: contents: read, pages: write, id-token: write
        │
        ├─ actions/checkout@v4 (persist-credentials: false)
        │
        ├─ build_admin_dashboard.py --assets-dir docs/admin --output-dir site
        │     env GITHUB_TOKEN = github.token   (job-scoped, never leaves CI)
        │       1. inventory/repos.yml          → fleet roster (slug, profile,
        │                                             rollout, default_branch)
        │       2. profiles/repos/<name>.yml    → per-repo gate configuration
        │       3. GitHub REST API per repo (each call 404-tolerant — a 404
        │          degrades THAT repo's row, it never fails the build):
        │             GET /repos/<slug>/actions/runs?branch=<default_branch>
        │             GET /repos/<slug>/rulesets
        │             GET /repos/<slug>                      (visibility)
        │       4. payload rows → redact_private_repos() masks every slug whose
        │          visibility == private ("<private>" placeholder, Phase 5 §8)
        │       5. writes  site/data/dashboard.json
        │          copies   docs/admin/{index.html,styles.css,app.js} → site/
        │
        ├─ admin_dashboard_pages.py --audit-jsonl audit/break-glass.jsonl --audit-jsonl audit/skip.jsonl
        │                                --coverage-json quality-rollup/coverage-trend.json
        │                                --drift-jsonl  audit/drift-sync.jsonl
        │                                --output-dir  site
        │     renders server-side pages: coverage.html, drift.html, audit.html
        │     (all inputs are optional; absent file ⇒ empty-state text)
        │
        ├─ stage self-contained sub-pages: every docs/admin/<name>/ directory
        │  is copied verbatim to site/<name>/ (today: codex-fleet-github)
        │
        └─ configure-pages + upload-pages-artifact(site)
        │
        ▼
deploy job: deploy-pages → https://prekzursil.github.io/quality-zero-platform/
```

## Runtime data flow (browser side)

`site/index.html` + `site/app.js` are the client-side SPA. On load it does
exactly one thing: `fetch('data/dashboard.json')` — a static JSON file baked at
build time. **No tokens, no provider credentials, and no GitHub API calls
client-side.** Everything privileged happens in the build job; the page only
ever renders what was persisted to `site/data/*.json`. Private-repo slugs are
masked before the payload is serialised, so the public site cannot leak them.

## Panel → source table (pre-Lane-8)

| Panel | Renderer | Data source | Notes |
| --- | --- | --- | --- |
| Repo heatmap (main SPA) | `docs/admin/app.js` (client) | `site/data/dashboard.json` ← inventory + profiles + live REST queries | per-repo: profile, rollout, issue policy, scanners, branch-coverage min, deps policy, default-branch health, open-PR health, ruleset presence |
| Coverage trend | `admin_dashboard_pages.py` (server) | `quality-rollup/coverage-trend.json` (build-time file) | No job produces it yet. Absent ⇒ "**No data.** … does not exist … NOT an all-clear." |
| Drift-sync status | `admin_dashboard_pages.py` (server) | `audit/drift-sync.jsonl` (runtime artifact) | No job produces it yet. Absent ⇒ "**No data.** … does not exist … NOT an all-clear." Present but empty ⇒ "No drift entries recorded." |
| Bypass audit feed | `admin_dashboard_pages.py` (server) | `audit/break-glass.jsonl` and `audit/skip.jsonl`, merged oldest first (runtime artifacts, committed by `reusable-quality-zero-bypass.yml` only when the caller has `BYPASS_AUDIT_PAT`) | Every state first says what the feed can show. The recording step never ran before 2026-10-24 (#336), so that period is **unaudited, not clean**. No fleet repo calls the bypass workflow yet, and admin ruleset bypasses are logged by GitHub's rule insights, not here. Neither file present ⇒ "**No data.** … NOT an all-clear." Present but empty ⇒ "No bypass events recorded since 2026-10-24." |
| Sub-pages (e.g. codex fleet) | self-contained HTML under `docs/admin/<name>/` | none (static) | copied verbatim |

## Health semantics

- **Default branch:** the NEWEST decisive run of each workflow among the 100 newest runs of the repository's own default-branch code. A workflow with no run in that window (dormant for 100+ runs) does not count. That is a deliberate bound: per-workflow paging would cost one API call per workflow per repo on every hourly build.
  Runs a pull request triggered (`pull_request*` events) or that come from another repository
  do not count: the branch filter matches a run's head branch, so a fork PR from its own `main`
  would otherwise land in the window. Up to 3 pages are read to fill the 100 with eligible runs.
  If PR runs crowd all 3, the verdict is `unknown`, unless a failure is already visible.
  `success` means every workflow's newest completed run passed. `partial` means at least one failed.
  `pending` means a workflow has only runs still in flight. `unknown` means no run says anything.
  Cancelled, skipped, neutral, stale and action-required runs never decide (the same set as the
  platform's own failing-workflow alert). So an old failure that a later run fixed, or a run that
  concurrency cancelled, no longer paints a green branch red.
- **Open PRs:** the same rule per open PR head commit, over the 100 newest `pull_request` runs.
  When two open PRs share a head commit (one branch, two bases), GitHub cannot say which PR a run
  belongs to. That head reads `unknown` unless a failure is already visible: green runs from one
  PR prove nothing about the other, which may have no run at all.
  `none` means no PR is open. Until 2026-10 this field filtered the *default-branch* runs for
  `pull_request` events, and a PR run never carries the default branch, so all 19 repos read
  `unknown`.
- **A page whose source file does not exist says so.** It renders "No data", names the missing
  file, and says it is not an all-clear. Before, it claimed "Fleet is in sync" or "No bypass
  events recorded" with nothing behind the claim.

## Known gaps (pre-Lane-8)

- The heatmap's "Default branch" health is workflow-run granularity
  (`success` / `partial` / `unknown`) — it cannot say **which gate** is red.
- The drift page depends on a JSONL that a plain checkout does not contain, so
  it usually renders its empty state; committed-vs-generated ruleset drift (the
  check `fleet-health.yml` runs weekly) had no dashboard representation.
- Nothing surfaced which open PRs have no aggregated quality-rollup comment
  (`<!-- quality-zero-rollup -->`, posted by `post_pr_quality_comment.py`).

These three gaps are exactly the Lane 8 operator panels.

---

## Operator panels (Lane 8)

Lane 8 extends the **same** publish pipeline, the **same** build job, and the
**same** SPA — no new app, no new deployment. Three new data files are
persisted into `site/data/` and rendered by `docs/admin/panels.mjs` (imported
by `app.js`):

| Operator question | Panel | Data file | Built by |
| --- | --- | --- | --- |
| Which repo is failing which gate right now? | **Gate status** (per-repo required-context table) | `site/data/dashboard.json` — each repo row gains `gates[]`, `gate_head_sha`, `gate_head_date`, `gate_status`, `gate_note` | `build_admin_dashboard.py` `_lane_gates()` — check-runs (**all Link-header pages**, 5-page cap: a re-run-flooded HEAD carries 100+ runs and page 1 alone hides the required contexts) + commit statuses of the default-branch HEAD, evaluated against the profile's `required_contexts` (exact name or unique ``<job> / <context>`` suffix match, same semantics as the quality gate). Absent PR-only contexts (the aggregate `CodeQL`) render `pr_only`, not `missing` |
| Where does committed-vs-generated ruleset/profile drift exist? | **Ruleset drift** | `site/data/ruleset-drift.json` | `build_dashboard_extras.py` — regenerates `generated/rulesets` from profiles+inventory (`generate_rulesets.generate_rulesets`) into a temp dir and byte-compares against the committed artifacts (the same check `fleet-health.yml` runs weekly, emitted as JSON; `drift_sync.py`'s status vocabulary: `in_sync`/`drift`/`missing` + which side) |
| Which PRs lack a rollup comment? | **Rollup gaps** | `site/data/rollup-gaps.json` | `build_dashboard_extras.py` — lists open PRs per enrolled repo and checks issue comments for the `<!-- quality-zero-rollup -->` marker (`post_pr_quality_comment.py`'s sticky-comment contract); PR rows render `yes` / `missing` / `unknown` |

### Failure & staleness policy

The Lane-8 gate fetches (`_github_fetch_degraded`) tolerate **404 and 403**
per call — measured live 2026-10-21: a token can read a private member's
head commit while being denied its check-runs feed (PrimariRo: head 200,
check-runs 403), and the hourly publish must degrade that one repo, never
fail. Degrades render as:

- head unreachable → `gate_status: stale` + reason;
- both check feeds denied → `stale` + reason;
- walk ended on a denial or hit the 5-page cap → verdicts still render, with
  `gate_note: "partial check-runs feed (page cap or mid-walk denial)"`.

Every row carries `gate_head_sha` + `gate_head_date` so an operator can see
how fresh the measurement is. The pre-existing heatmap fetches keep their
documented 404-only contract (unchanged).

Least privilege: the extras step uses the same job-scoped `GITHUB_TOKEN`
(`contents: read`); public fleet repos answer fully, private members surface as
stale rows — mirroring the documented `fleet-health.yml` token-scope behaviour.
Nothing new is exposed client-side: the page reads the three static JSON files
only; private slugs (and their PR titles/urls) are masked before serialisation.

### CI smoke (fixture render)

`scripts/dashboard_ui/dashboard_render.test.mjs` (run by `scripts/verify`,
wired into the required **Control Plane Verify** check) imports the real
`docs/admin/panels.mjs` renderers and executes them against the committed
fixture JSONs in `scripts/quality/fixtures/dashboard/`
(`dashboard.fixture.json`, `ruleset-drift.fixture.json`,
`rollup-gaps.fixture.json`) under a minimal DOM shim. The test fails unless
all three panels render their fixture content (repo/gate rows, drift entries,
PR-gap rows). Python-side, `tests/test_dashboard_extras.py` and
`tests/test_build_admin_dashboard_lanes.py` hold the data builders to the
repo's 100 % line+branch bar.
