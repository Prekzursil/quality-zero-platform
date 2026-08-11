# CodeQL required-context sweep — measured fleet state (2026-08-11)

A required status check that the repo can never report is not a strict gate, it is a
**permanent red**. GitHub offers two ways to run CodeQL and they emit *different* check
names, so migrating a repo from one to the other silently invalidates any required
context pinned to the old spelling.

| setup | how it is configured | check names it emits |
|---|---|---|
| **advanced** | a `.github/workflows/codeql.yml` in the repo (the QZP-managed wrapper) | `codeql / CodeQL` (+ `codeql / Resolve CodeQL Profile`) |
| **default** | GitHub-managed, Security tab → Code scanning → Default setup | aggregate **`CodeQL`** from the GitHub Advanced Security app (`app_id 57789`), plus `Analyze (<language>)` on *some* events only |

Default setup **never** emits `codeql / CodeQL`. That is the whole defect class.

## Which spelling to require

Require the **aggregate `CodeQL`**, not the per-language `Analyze (<lang>)` names.

Measured 2026-08-11 across all 16 open pull-request heads of `Prekzursil/WebCoder`
(`GET /repos/.../commits/<head>/check-runs?per_page=100`):

| context | present on |
|---|---|
| `CodeQL` | **16 / 16** |
| `Analyze (<lang>)` | **1 / 16** (only PR #102) |
| `codeql / CodeQL` | **0 / 16** |

Detector control: the same probe reported `True` for `CodeQL` and `quality / quality` on
every row, so a `False` for the other two is a measurement, not a broken matcher.

`Analyze (<lang>)` is therefore unsafe to require — it would wedge 15 of 16 PRs. The
fleet's already-deployed default-setup repos agree with the aggregate choice:
`DevExtreme-Filter-Go-Language`'s classic protection requires `CodeQL`@57789 (its PR #40
merged under exactly that requirement) and `env-inspector`'s **active** ruleset requires
`CodeQL` with no app pin.

## Fleet measurement

Probe: `GET /repos/Prekzursil/<slug>/actions/workflows?per_page=100` for the state of
`.github/workflows/codeql.yml` and of `dynamic/github-code-scanning/codeql`, corroborated
against the CodeQL-related check-run names actually emitted on `main`'s HEAD commit.

| repo | `codeql.yml` | default setup | emitted on main HEAD | setup |
|---|---|---|---|---|
| pbinfo-get-unsolved | active | active | `codeql / CodeQL` | advanced |
| quality-zero-platform | active | active | `codeql / CodeQL` | advanced |
| Airline-Reservations-System | active | active | `codeql / CodeQL` | advanced |
| DevExtreme-Filter-Go-Language | **disabled_manually** | active | `Analyze (actions\|go\|python)` | **default** |
| event-link | active | active | `codeql / CodeQL` | advanced |
| Personal-Finance-Management | active | — | `codeql / CodeQL` | advanced |
| momentstudio | active | active | `codeql / CodeQL` | advanced |
| Reframe | **absent** | active | `Analyze (actions\|javascript-typescript\|python)` | **default** |
| SWFOC-Mod-Menu | active | active | `codeql / CodeQL` | advanced |
| Star-Wars-…-Save-Game-Editor | active | — | `codeql / CodeQL` | advanced |
| TanksFlashMobile | active | active | `codeql / CodeQL` | advanced |
| WebCoder | **disabled_manually** | active | `Analyze (…)` + a pre-disable `codeql / CodeQL` | **default** |
| env-inspector | **disabled_manually** | active | `Analyze (actions\|python)` | **default** |
| codeblocks-pretty-prints-stable | active | — | `codeql / CodeQL` | advanced |
| PrimariRo | **absent** | — | *(none)* | **default, not yet reporting** |
| tracelines | **absent** | active | `Analyze (actions\|javascript-typescript\|python)` | **default** |
| llm-anthology | active | — | `analyze (javascript-typescript\|python)` | advanced (own wrapper job names) |
| agent-skills-toolchain | **absent** | — | *(none)* | **default, not yet reporting** |

WebCoder's `main` HEAD is `477b7aec` (2026-06-27), which predates the disable, so the
`codeql / CodeQL` runs on it are historical. No PR head carries it — see the 16/16 table
above.

## What was fixed, and what is still pending

**Fixed live (2026-08-11): `Prekzursil/WebCoder` classic branch protection.**

```
before: strict=true  checks=[{codeql / CodeQL, app:any}, {quality / quality, 15368}]
after : strict=true  checks=[{quality / quality, 15368}, {CodeQL, 57789}]
```

Applied with `PATCH /repos/Prekzursil/WebCoder/branches/main/protection/required_status_checks`
(the narrow sub-resource, not the whole-object `PUT`). A before/after diff of the *full*
protection object shows `required_status_checks` as the only changed key — reviews,
`enforce_admins`, force-push, deletions, linear history, conversation resolution, lock and
fork-syncing are byte-identical. Net effect: one **never-emitted** requirement removed, and
`CodeQL` added — which WebCoder's own **active** ruleset `quality-zero-platform / WebCoder`
(id 14488879) already required, so the effective requirement set gained nothing and the
"required ⊆ enabled" invariant is discharged by construction.

Both-states evidence: PR #102 (`fix(deps): clear the critical websocket-driver advisory`)
was `mergeStateStatus: BLOCKED` with every check green before the change and `CLEAN` /
`MERGEABLE` immediately after, with no other edit. The other 15 open PRs stayed `BLOCKED`
— correctly: their `quality / quality` genuinely fails. The phantom was removed without
weakening anything real.

**Fixed in this repo: `profiles/repos/tracelines.yml`** now requires `CodeQL` instead of
`codeql / CodeQL`, with `generated/rulesets/tracelines.json` and the `inventory/repos.yml`
note updated in the same commit (the inventory ↔ profile ↔ generated-ruleset triple).
Membership is unchanged, so the count assertion in `tests/test_control_plane.py` stays 18.

**Pending — five profiles still require the unreportable spelling:** `WebCoder`,
`Reframe`, `env-inspector`, `PrimariRo`, `agent-skills-toolchain`. Three of them
(`WebCoder`, `env-inspector`, `Reframe`) inherit `codeql / CodeQL` from the shared
`profiles/stacks/quality-zero-phase1-common.yml`, so de-listing it is a cohort-wide change
that the ADDITIVE-ONLY lean-gate charter defers to a coordinated fleet runbook. None of
them is *live*-wedged by its profile today, because generated rulesets are **not**
auto-applied: WebCoder's live ruleset requires `{quality / quality, CodeQL}` while
`generated/rulesets/webcoder.json` still renders 15 retired-SaaS contexts, and
env-inspector diverges the same way. Two independent repos, same conclusion.

## The guard

`tests/test_security_baseline_profiles.py` carries the registry
`CODEQL_DEFAULT_SETUP_REPOS` and the assertion
`test_default_setup_repos_never_require_the_advanced_codeql_context`. For every registered
slug it requires **both** halves — the advanced-only spelling is absent, *and* a
default-setup spelling is present — so the check can only be satisfied by substitution,
never by deletion.

Add a slug to the registry in the same commit that fixes its profile. The gate was proven
both-states on `tracelines`: with `codeql / CodeQL` in the profile it exits 1
(`FAILED (failures=1)`), and with `CodeQL` it exits 0 (`OK`).
