# Truthful-Gate Subsystem — Design

**Status:** ✅ **DESIGN-REVIEW-GATE PASSED** (4 rounds, 2026-06-01).
R1 5/5 BLOCK → 9 blockers (Addendum A); R2 → 2 HIGH (Addendum B); R3 FAIL → 2
HIGH (Addendum C); **R4 PASS, `ready_for_writing_plans: true`**, zero open
CRITICAL/HIGH. Read body §1–§9 for the original design, but **Addenda A→B→C→D
override the body and each other in order** — they carry the corrected,
code-cited, gate-approved plan. Next: `writing-plans` per-TG + plan-review-gate.

**Author session:** 2026-06-01
**Branch:** `feat/truthful-gate-subsystem` (off `origin/main` @ `c0a5437`)
**Supersedes nothing — extends:** `docs/QZP-V2-DESIGN.md`, builds on the
merged strict-zero push (#221, #222, #223, #224, #225) and the OPEN
hardening PR **#232** (`provider_enforcement.py` + fleet `audit→zero`
flip).

> **Why this exists.** The platform has cycled through 13+ "rounds" and
> ~30 strict-zero PRs (#198–#225) and *still* the gates-and-issues problem
> recurs every session. This design is the **once-and-for-all** closure:
> make the gate report *dashboard truth*, make token failure *loud*, and
> make onboarding a *single command*. After this lands, "a gate is green"
> means "the provider's dashboard is actually clean," and "add a repo"
> means "run one command."

---

## 1. The problem, stated precisely

Two failure classes have driven every recurring round:

### 1a. The CI-exit-vs-dashboard-truth gap (the "gates lie" problem)

QZP gates have historically reported **PASS off a CI step's exit code**,
while the **authoritative finding count lives on the provider's cloud
dashboard**. The two diverge constantly:

| Provider | Observed divergence |
|---|---|
| Codacy | dashboard 838 issues on platform; gate green (was `mode: audit`) |
| DeepSource | dashboard 1116 (875 + 140) issues; HTML scraper under-counted; gate green |
| SonarCloud | dashboard 110 issues; gate green when scan step "succeeded" |
| Codecov | flag at 0% (upload-loop bug / empty `flags:[]`) → gate green locally, UI shows untested |
| QLTY | cloud 71 vs local 0 mismatch on platform |
| Semgrep | `semgrep ci` exits 0 on non-blocking findings (fixed in #221) |

Root causes already partly closed: silent-pass exemptions in
`run_*_zero.py` (#175), `semgrep ci` non-blocking exit (#221), Codacy
threshold blindness (#222), coverage uploads swallowing failures (#223),
DeepScan missing-context (#224), and provider-context enforcement
completeness (#232's `provider_enforcement.py`, OPEN).

**What's still open:** there is no *single, uniform* contract that says,
for every provider, "the gate's verdict is derived from the live
dashboard count, and a provider that cannot be read is a BLOCK, never a
pass." Each `check_*_zero.py` reimplements its own truth-fetch, auth
handling, and failure semantics. Drift between them is where the next
silent-pass always hides.

### 1b. Rotated SaaS tokens surface as silence, not signal (the master blocker)

Per `memory/qzp-fleet-campaign.md`, the master blocker across the
campaign has been **rotated SaaS tokens**. When a token (Codacy / Codecov
/ DeepSource / Sonar / Sentry / DeepScan / Snyk) is stale, the provider
API returns 401/403. Today that lands inconsistently: sometimes a
warn-and-skip (→ silent green), sometimes a hard crash (→ red for a
reason the operator can't see). Tokens were refreshed 2026-05-31
(`DEEPSOURCE_DSN` updated; "rotated 4 creds"), but the *system has no
durable way to keep a rotation from re-introducing silent greens.*

### 1c. Onboarding is micromanagement (the "easy to add repos" problem)

`docs/ONBOARDING.md` is a **9-step manual procedure**: hand-pick a stack,
hand-append to `inventory/repos.yml`, hand-write a ~140-line
`profiles/repos/<name>.yml` (vendor keys, coverage inputs, required
contexts, codeql languages, dependabot ecosystems…), validate, dispatch
bootstrap, watch 3 shadow runs, hand-promote, add to the SHA-bump wave.
PR #232 hand-added `bilbo-app` + `pbinfo-scrape` profiles this way. This
is the friction the user wants gone.

---

## 2. North star

After this subsystem ships:

1. **Truthful gates.** Every gate verdict is derived from the provider's
   **live dashboard truth**, normalized to one schema, through one
   contract. There is exactly one place that defines "clean," "dirty,"
   and "unreadable."
2. **No silent greens, ever.** A provider that cannot be read (rotated
   token, API down, project not found) yields a distinct
   **`unreadable` → BLOCK** verdict and opens `alert:scanner-unavailable`.
   A provider that is read and dirty BLOCKS. Only "read and clean" passes.
3. **Truthful legacy handling.** Repos with pre-governance debt (the
   platform itself: 838+1116+110) are handled by a **dated, frozen,
   burning-down baseline** — never by an `audit` lie. The gate blocks on
   *any increase* over baseline and on *failure to burn down by deadline*.
4. **Zero-conf onboarding.** `qzp onboard <owner>/<repo>` (one command):
   auto-detects stack from repo contents, derives all vendor keys,
   synthesizes a minimal profile, opens the bootstrap PR, tracks shadow
   runs, and auto-promotes. Hand-editing a profile becomes the rare
   exception, not the default.
5. **Sturdy + documented.** TDD, 100% line+branch on every new module,
   self-governed, with operator-facing docs rewritten around the truth
   model.

---

## 3. Component design

### 3.1 The Truth Source contract (`scripts/quality/truth/`)

A single abstract contract every provider adapter implements:

```python
# scripts/quality/truth/contract.py
class ProviderTruth(Protocol):
    name: str                      # "codacy", "sonarcloud", ...
    def fetch(self, repo: RepoRef, ref: GitRef, *, token: Secret) -> TruthResult: ...

@dataclass(frozen=True)
class TruthResult:
    provider: str
    verdict: Literal["clean", "dirty", "unreadable"]
    finding_count: int | None      # None iff unreadable
    findings: tuple[Finding, ...]   # normalized to qzp-finding/1 (docs/schemas)
    coverage: CoverageTruth | None  # when the provider reports coverage
    source_url: str                 # the dashboard URL the verdict came from
    fetched_at: str                 # ISO-8601 (passed in; never Date.now in-script)
    diagnostic: str                 # human-readable reason, esp. for `unreadable`
```

**Key rules baked into the contract (not per-adapter):**
- **Auth failure (401/403/404-project-missing) → `unreadable`,** never
  `clean`. The verdict enum makes a silent pass *unrepresentable*.
- **Verdict→exit mapping is centralized:** `clean`→0, `dirty`→1,
  `unreadable`→2 (distinct, so CI/log shows "could not read" vs "found
  issues"). One function, one test, reused by every `check_*_zero.py`.
- Adapters are **thin**: fetch + normalize only. Policy (baseline,
  severity, block/warn/info) lives in the gate, not the adapter.

Existing `check_codacy_zero.py`, `check_deepsource_zero.py`,
`check_sonar_zero.py`, `check_semgrep_zero.py`, `check_deepscan_zero.py`,
`check_sentry_zero.py`, `codacy_quality_thresholds.py` are **refactored to
delegate** to their adapter (behavior-preserving; characterization tests
pin current output first — see §6).

### 3.2 Reconciliation (`scripts/quality/truth/reconcile.py`)

For providers that also run *in-CI* (Semgrep, CodeQL, coverage), compare
the in-CI result against the dashboard truth. **Mismatch beyond a
tolerance → `dirty` + `alert:gate-truth-drift`.** This permanently kills
the class of bug where local CI says 0 but the dashboard says 875.

### 3.3 Token-rotation resilience

- **Preflight** (`check_quality_secrets.py`, extend): before any gate
  runs, probe each `severity: block` provider's *auth* with a cheap
  whoami/ping call. Any failure → `unreadable` for that provider →
  BLOCK + `alert:scanner-unavailable` (distinct from `alert:secret-missing`
  for *absent* secrets).
- **Rotation playbook** in `docs/`: the single procedure to rotate +
  verify, with the preflight as the verification step. Rotation can no
  longer silently re-open a green-but-wrong gate.

### 3.4 Truthful baseline / ratchet (the legacy-debt answer) — **OPEN DECISION A**

The platform itself carries 838 Codacy + 1116 DeepSource + 110 Sonar
*pre-governance* findings. PR #232 flips the platform from `audit`→`zero`,
which is *truthful* but makes the platform un-mergeable until those ~2064
findings reach literal zero. Two principled, non-lying options:

- **A1 — Truthful frozen baseline + burn-down (recommended).** Record a
  dated baseline snapshot per provider (`baselines/<slug>/<provider>.json`
  with `{count, ref, captured_at, deadline}`). The gate BLOCKS on **any
  count > baseline** (no new debt) and BLOCKS when **`today > deadline`
  and `count > 0`** (forced burn-down). `audit` mode is deleted from the
  schema entirely. This is truthful (the real count is always shown, never
  masked), unblocks #232 immediately, and still drives to zero by the
  deadline. Burn-down can be chipped by QRv2 auto-remediation over time.
- **A2 — Drive platform to literal zero now.** A dedicated fan-out
  campaign to fix all ~2064 platform findings before flipping. Purest end
  state, but very large, and the same legacy-debt question recurs for
  every future repo onboarded with existing debt.

**CHOSEN (user 2026-06-01): A2 — drive platform to literal zero now**,
with the A1 frozen baseline retained as a *regression floor* during the
burn-down (no NEW debt while clearing old). `audit` mode is deleted from
the schema. See §8 + §9 Track 2. The baseline mechanism still generalizes
to future legacy onboards.

### 3.5 Zero-conf onboarding (`scripts/quality/onboard.py` + `qzp onboard`)

Collapse `docs/ONBOARDING.md` Steps 1–9 into one command:

```
qzp onboard Prekzursil/<repo>            # detect, derive, seed, bootstrap, track, promote
qzp onboard Prekzursil/<repo> --stack go --mode ratchet --dry-run   # overrides optional
```

Pipeline:
1. **Detect stack** from repo contents via `gh api .../contents` +
   signature rules (`pyproject.toml`→python-*, `go.mod`→go, `Cargo.toml`
   →rust, `*.csproj`+WPF→dotnet-wpf, `vite.config.*`+`package.json`→
   react-vite-vitest, both backend+frontend→fullstack-web, …). Ambiguous
   → prompt or `--stack`.
2. **Derive vendor keys** mechanically: `sonar.project_key =
   <owner>_<repo>`, `codecov.slug = owner/repo`, codacy project = repo,
   deepsource shortcode = repo, codeql languages from detected stack,
   dependabot ecosystems from detected manifests. No hand entry.
3. **Synthesize the minimal profile** — only `slug` + `stack` +
   `mode.phase` are required in the file; *everything else inherits from
   the stack template* at load time (see §3.6). Target file size: ~8
   lines, not ~140.
4. **Append to `inventory/repos.yml`** programmatically (idempotent).
5. **Open the bootstrap PR** via `reusable-bootstrap-repo.yml` (existing).
6. **Track 3 green shadow runs**, then **auto-open the promotion PR**
   (existing shadow→absolute machinery, now driven end-to-end).

The 4 known-unprofiled repos (`bilbo-app`, `omniaudit-mcp`,
`pbinfo-scrape`, `skills-introduction-to-github`) become the **acceptance
fixtures** for this command (note: #232 already hand-added 2 of them —
the command must reproduce/replace those by-hand profiles).

### 3.6 Profile inheritance ("no micromanagement") — **OPEN DECISION B**

Today each `profiles/repos/<slug>.yml` re-states scanners, required
contexts, coverage scaffolding, etc. To make profiles ~8 lines:

- **B1 — Thin profile + stack-template inheritance at load time
  (recommended).** The profile carries only deviations; the loader merges
  `profiles/stacks/<stack>.yml` defaults underneath. `required_contexts`
  becomes fully *derived* from `scanners.*.severity` (the design already
  intended this — `QZP-V2-DESIGN.md §3` "auto-generated — do not
  hand-edit"). This requires hardening the loader + a migration of the 15
  existing profiles to thin form (behavior-preserving; golden tests pin
  the resolved profile before/after).
- **B2 — Keep fat profiles, only auto-generate them in `onboard.py`.**
  Lower blast radius (no loader change, no migration), but profiles stay
  140 lines and drift-prone; "no micromanagement" only holds for *new*
  repos, not existing ones.

**CHOSEN (user 2026-06-01): B1 — thin profiles + inheritance + migrate all
15.** The migration touches all 15 profiles + the resolver + every profile
test; it is done behavior-preserving with golden tests on the *resolved*
profile (snapshot before, assert identical after). See §8 + §9 TG-5.

### 3.7 Dashboard + alerts

- New alert types: `alert:scanner-unavailable` (token/API failure),
  `alert:gate-truth-drift` (CI vs dashboard mismatch),
  `alert:baseline-deadline` (burn-down deadline passed with count > 0).
- Dashboard heatmap cell gains a third state: **grey = unreadable**
  (distinct from green/red), so a rotated token is *visible*, not hidden.

---

## 4. Scope slicing (proposed PRs)

| PR | Title | Content | Unblocks |
|---|---|---|---|
| **TG-1** | Truth Source contract + adapter refactor | `truth/contract.py`, verdict→exit map, refactor `check_*_zero.py` to adapters (behavior-preserving, characterization-tested), `unreadable` semantics | Uniform truth; kills the silent-pass *class* |
| **TG-2** | Token-rotation resilience | preflight auth-probe, `alert:scanner-unavailable`, rotation playbook doc | Master blocker becomes loud, not silent |
| **TG-3** | Truthful baseline/ratchet (DECISION A) | `baselines/`, no-new-debt + deadline gate, **delete `audit` mode**, unblock #232 (or rebase #232 onto this) | Platform + legacy repos mergeable *truthfully* |
| **TG-4** | Reconciliation + truth-drift alert | `reconcile.py`, `alert:gate-truth-drift`, dashboard grey state | Permanent CI-vs-dashboard guarantee |
| **TG-5** | Profile inheritance (DECISION B) | loader merge + derived `required_contexts` + migrate 15 profiles (if B1) | ~8-line profiles |
| **TG-6** | Zero-conf onboarding | `onboard.py` + `qzp onboard`, stack detection, vendor derivation, end-to-end shadow→promote; ONBOARDING.md rewrite | One-command onboarding |

PRs land in order; each is independently green, TDD, 100% coverage on new
modules, self-governed. #232's `provider_enforcement.py` is folded into
TG-1/TG-3 (its enforcement-completeness check is complementary to the
truth contract).

## 5. Relationship to in-flight work

- **#232 (OPEN, BLOCKED):** its block is *correct behavior* (truthful gate
  red on real platform debt). TG-3's baseline makes it mergeable. **Decision
  needed:** rebase/extend #232 vs supersede it. (Lean: extend — keep
  `provider_enforcement.py`, add baseline.)
- **#231/#235 (Optibot bundles, +1/-1):** trivial; merge or ignore,
  orthogonal.
- **Local branch `fix/coverage-uploads-strict-zero-2026-04-29`:** 3 commits
  ahead of origin/main; #223 already merged the coverage-swallow fix. Its
  remaining 2 commits (`c99cfa5`, `39099fa`) may be unmerged — triage into
  TG-1 if still relevant.
- **8 stashes preserved** (incl. 7 pre-existing "prevent loss"); untouched.

## 6. Engineering discipline (non-negotiable, per `CLAUDE.md`)

- **TDD**, tests-first, 100% line+branch on new modules
  (`.coverage-thresholds.json` is source of truth).
- **Characterization tests first** on every `check_*_zero.py` before
  refactor — pin current stdout/exit, then refactor green.
- `lizard -C 15` clean; new scripts added to
  `sonar.coverage.exclusions` per the silent-pass remediation note.
- No `--no-verify`; no `git push --force` without approval; stay on
  feature branch; never touch `main` directly.
- `.beads/` recovery files + `.remember/` handoff updated on every phase
  transition (this design was almost lost to an empty handoff — never
  again).

## 7. Non-goals

- Not re-architecting the reusable-workflow fan-out (drift-sync, bumps,
  waves) — those work; this layers truth + onboarding on top.
- Not provisioning `DRIFT_SYNC_PAT` / SonarCloud Auto-Analysis toggle —
  still operator-only (but the subsystem makes their *absence* loud).
- Not building new scanners — unifying the truth of existing ones.

## 8. LOCKED DECISIONS (user-confirmed 2026-06-01)

The user selected the **maximal program** — "solve everything, once and
for all." All forks resolved:

- **Decision A — legacy debt → A2 (drive platform to literal zero now).**
  The platform's ~2064 findings (838 Codacy + 1116 DeepSource + 110 Sonar)
  are burned down to literal zero. A **frozen baseline is still recorded
  as a regression floor** during the burn-down (so no NEW debt sneaks in
  while we clear the old), but the target is 0, not baseline-hold. `audit`
  mode is deleted from the schema either way.
- **Decision B — profile shape → B1 (thin + stack inheritance, migrate
  all 15).** Resolver hardened so `required_contexts` is fully derived
  from `scanners.*.severity`; all 15 profiles migrated to ~8-line thin
  form, behavior-preserving with golden tests on the resolved profile.
- **Decision C — fleet scope → drive all 15 repos green now.** The
  subsystem is built first, then used as the instrument to drive every
  governed repo to green CI in this same program. Cross-repo PRs are
  opened via the operator's authenticated `gh` token from this
  environment (verify scope covers consumers) and/or `DRIFT_SYNC_PAT`;
  the subsystem makes a missing PAT *loud* rather than a blocker to
  building.
- **Decision D — #232 disposition → extend.** Keep
  `provider_enforcement.py` and the fleet `audit→zero` flip; TG-1/TG-3
  rebase #232 onto the truth contract + baseline so it merges truthfully.
- **Decision E — execution → metaswarm orchestrated execution** (4-phase
  loop per work unit, fresh adversarial reviewers, coverage enforcement,
  pre-PR knowledge capture).

## 9. Program structure & sequencing

Three tracks, sequenced so each stands on a non-regressing foundation:

```
Track 1 — SUBSYSTEM (TG-1..TG-6)   [build the instrument]
   TG-1 Truth Source contract + adapter refactor (+ fold #232 provider_enforcement)
   TG-2 Token-rotation resilience (auth preflight, alert:scanner-unavailable)
   TG-3 Truthful baseline + delete `audit` mode (rebase/unblock #232)
   TG-4 Reconciliation + alert:gate-truth-drift + dashboard grey state
   TG-5 Thin profiles + derived required_contexts + migrate 15 (Decision B1)
   TG-6 Zero-conf onboarding `qzp onboard` + ONBOARDING.md rewrite
          │
          ▼
Track 2 — PLATFORM LITERAL-ZERO (dogfood; Decision A2)   [prove the instrument on ourselves]
   Burn down ~2064 platform findings to 0 using the subsystem's truth +
   QRv2 deterministic patches; record baseline as regression floor;
   merge #232 once platform is truthfully green.
          │
          ▼
Track 3 — FLEET GREEN (Decision C)   [scale to all 15]
   For each governed repo: read dashboard truth → burn down to 0 →
   thin-migrate profile → promote shadow→absolute → confirm green CI.
   Cross-repo PRs via authed gh / DRIFT_SYNC_PAT.
```

**Hard external dependencies (loud, not silent):** `DRIFT_SYNC_PAT` (or
operator gh-token scope) for cross-repo PR opening; SonarCloud
Auto-Analysis OFF on event-link (UI-only). The subsystem surfaces each as
an explicit `alert:*` / preflight BLOCK — never a silent skip.

**Done means (verified, not believed):** every governed repo's provider
dashboards read literal zero through the Truth Source contract; every
`* Zero` gate green on main; `qzp onboard` reproduces the 4 unprofiled
repos' profiles from scratch; `verify_v2_deployment.py --all` exit 0; no
open `alert:*`. Only then does the campaign close.

> ⚠️ The §9 paragraph above is the *original* (Round-1) DoD. It is
> **superseded by Addendum A §A.CB-1** (it conflated "subsystem shipped"
> with "campaign closed" and named infeasible fixtures). Read Addendum A.

---

# Addendum A — Round-1 design-review-gate remediation

**Gate:** 5 reviewers (PM, Architect, Designer, Security, CTO) +
synthesis. **Round-1 result:** 5/5 BLOCK → synthesis
`PASS_WITH_REQUIRED_FIXES`, 9 consolidated blockers (CB-1..CB-9; 6
CRITICAL, 3 HIGH). Every blocker was verified by a reviewer against live
code. **None reopens a user-locked decision (A2/B1/C/D/E)** — all are
amendments that make those decisions implementable. This addendum is the
corrected, code-cited plan and **overrides the body where they conflict.**

## A.0 Re-baselined facts (corrections to the body's framing)

The body overstated friction; reviewers verified the real numbers. The
*approach* is unchanged, but estimates and the §1a narrative are corrected:

- **Profiles are 30–124 lines (median ~80), not "~140".** Several are
  already 30–37 lines. (CTO concern.)
- **The resolver ALREADY does stack inheritance + context merge** —
  `control_plane.py` `_load_stack` (recursive) + `common.py`
  `merge_required_contexts`, driven by `required_contexts_mode:
  replace|merge`. B1 is therefore *not* "build inheritance"; it is "add
  severity-derivation + thin the 9 profiles carrying explicit context
  lists" (see A.CB-7).
- **§1a is reworded.** The silent-pass is NOT "PASS off a CI exit code."
  `check_codacy_zero.py` / `check_deepsource_zero.py` already fetch
  dashboard truth. The *real* verified silent-pass vectors are: (i)
  `issue_policy.mode: audit` → unconditional pass; (ii) auth-failure →
  unauthenticated public fallback → 0 → pass; (iii) `None`/unparseable
  scrape count coerced to 0 → pass; (iv) Codecov 401/403 warn-and-skip.
  TG-1 characterization tests must pin **specifically these paths.**

## A.CB-1 (CRITICAL) — Split Definition of Done; #232 merges at baseline-hold

The body's §9 made campaign closure depend on operator-only inputs
(`DRIFT_SYNC_PAT` absent; SonarCloud Auto-Analysis UI-only toggle) — the
exact trap that left rounds 5/7/8/9/12/13 un-closeable. **Resolution:**

**Milestone 1 — SUBSYSTEM-DoD (this design's actual DoD; fully verifiable
with no operator-gated fleet action):**
- TG-1..TG-6 merged, platform self-governed and green, 100% line+branch on
  new modules.
- `unreadable → BLOCK` proven by inverted fail-closed tests **and** a live
  TG-2 preflight against the platform's own block-severity providers.
- A working `qzp onboard` (acceptance per A.CB-2).
- **#232 merges truthfully on the A1 frozen baseline** as a regression
  floor — its merge gate is pinned to **baseline-hold** (real count shown,
  count ≤ dated baseline, no new debt, deadline recorded), **NOT literal
  zero.** This lets the truth contract ship and prove value without
  waiting on the ~2064-finding burn-down.

**Milestone 2 — CAMPAIGN-CLOSURE (separate, operator-gated; preserves
locked A2 + C):**
- A2 literal-zero burn-down (platform dashboards → 0) proceeds *behind* the
  shipped subsystem, driven by QRv2 deterministic patches over time.
- C fleet-green across all 15 repos.
- **Hard preconditions, surfaced as a loud preflight BLOCK (never a silent
  warn):** operator provisions `DRIFT_SYNC_PAT` (fine-grained, see A.CB-8)
  and toggles SonarCloud Auto-Analysis OFF on event-link. The TG-2
  preflight verifies both; the campaign cannot *start* its cross-repo
  phase until they are confirmed.
- The TG-2 auth-preflight runs **on a cron** so a future token rotation
  that re-introduces a silent green is caught within a day.

> **Sequencing note for the user (reversible):** locked decision A2 said
> "drive platform to literal zero." The gate strongly recommends merging
> #232 at *baseline-hold* first, then burning down to literal zero behind
> it — so the subsystem's value isn't hostage to a 2064-finding cleanup.
> Literal zero remains the target; only the *gating* changes. Say the word
> if you'd rather #232 wait for literal zero.

## A.CB-2 (CRITICAL) — Re-scope onboarding acceptance fixtures

The body pinned acceptance to 4 repos, 2 of which are infeasible/wrong.
**Resolution:**
- **Acceptance fixtures (must work):** `omniaudit-mcp` → `python-tooling`;
  `pbinfo-scrape` → `node-frontend` (both stacks exist).
- **Excluded:** `skills-introduction-to-github` — tutorial scaffold,
  recorded decision: **not governed** (added to an inventory exclude list,
  not onboarded).
- **Deferred (named follow-up, non-gating):** `bilbo-app` needs a
  `kotlin-multiplatform` stack that does not exist. Tracked as **TG-7
  (stretch)**: author `profiles/stacks/kotlin-multiplatform.yml` +
  template; only then onboard bilbo-app. Not in the SUBSYSTEM-DoD.
- **Detector advertises ONLY stacks with a `profiles/stacks/<stack>.yml`**
  that exists today (cpp-cmake, dotnet-wpf, fullstack-web, go,
  gradle-java, node-frontend, python-desktop, python-tooling, python-web).
  `react-vite-vitest` / `rust` / `swift` are removed from the detector
  until their templates exist. **§7 updated:** net-new stack templates are
  *out of scope* for the subsystem except the TG-7 stretch.

## A.CB-3 (HIGH) — Deterministic stack detection (no `prompt` in CI)

`pyproject.toml` is consumed by `python-tooling` / `python-web` /
`python-desktop`, so a bare manifest match is ambiguous and the body's
"prompt or --stack" fallback reintroduces micromanagement. **Resolution —
a checked-in, ordered signature→stack decision table** (the contract;
first match wins):

| Order | Signature (in repo contents / manifest) | → Stack |
|---|---|---|
| 1 | `*.csproj`/`*.sln` + WPF refs, or `*.xaml` | `dotnet-wpf` |
| 2 | `CMakeLists.txt` / `*.vcxproj` | `cpp-cmake` |
| 3 | `build.gradle`/`pom.xml` | `gradle-java` |
| 4 | `go.mod` | `go` |
| 5 | `package.json` **and** a Python manifest | `fullstack-web` |
| 6 | `package.json` only (no Python) | `node-frontend` |
| 7 | Python manifest + `flask`/`fastapi`/`django` in deps | `python-web` |
| 8 | Python manifest + PyInstaller/`*.spec`/WPF-via-pythonnet | `python-desktop` |
| 9 | Python manifest (none of the above) | `python-tooling` |
| — | no signature matched | **terminal: emit actionable diagnostic** |

- **Non-interactive contract:** never prompt. On no-match, exit non-zero
  with `no stack signature found in <repo>; pass --stack=<one of: ...> or
  exclude via inventory`. `--stack` always overrides detection.
- **Monorepos:** explicit **non-goal** for auto-detection (operator passes
  `--stack`); documented as such.
- The promise is reframed honestly: **"one command; at most a single
  `--stack` hint for genuinely ambiguous repos."**

## A.CB-4 (HIGH) — Define the `qzp` invocation surface

No `qzp` command exists today. **Resolution (TG-6):** add
`[project.scripts] qzp = "scripts.quality.qzp_cli:main"` to the platform
`pyproject.toml`; `qzp_cli` dispatches subcommands (`onboard`, later
`gate`, `verify`). ONBOARDING.md prerequisites document `pip install -e .`
(or `pipx install`). A repo-root `./qzp` shim wrapping
`python -m scripts.quality.qzp_cli` is provided for the no-install path.
All doc examples use the exact pinned form.

## A.CB-5 (CRITICAL) — Auth/`unreadable` path is INTENTIONALLY behavior-changing

The body's "behavior-preserving, characterization-tested" mandate (§6)
would pin and lock in the verified fail-open paths. **Resolution:**
- §6 + TG-1 amended: **auth-failure handling is behavior-CHANGING and
  EXEMPT from characterization-pinning.** Characterization tests pin
  **only the clean and dirty paths.** The unreadable path gets **NEW
  inverted assertions:** `401 / 403 / 404-project-missing → unreadable →
  exit 2 → BLOCK + alert:scanner-unavailable`, never pass, no fallback.
- **DELETE the three fail-open branches:** Codacy public fallback
  (`codacy_zero_support.py:122-134` `unauthorized_http_result` →
  `_query_codacy_public_repository_issues`; `:150` 404 handler);
  DeepSource `None→0` coercion (`check_deepsource_zero.py:199-217`);
  Codecov 401/403 warn-and-skip (`validate_codecov_flags`).
- **Positive-proof invariant (contract test):** a `clean` verdict MUST
  derive from an **authenticated** response that **positively parsed a
  count**. No adapter may emit `clean` from an unauthenticated/public read
  or an unparseable scrape.
- **DeepSource fidelity (resolves Architect/Security concern):** move
  DeepSource truth to its **GraphQL API** (`DEEPSOURCE_DSN` refreshed
  2026-05-31) inside the adapter; if API is unavailable, the scraped
  `count`-vs-`issue-cards` disagreement already detected at
  `check_deepsource_zero.py:212-214` returns **`unreadable`**, not a
  finding-of-0.

## A.CB-6 (CRITICAL) — TG-3 targets `issue_policy.mode`, not `mode.phase`; atomic with floor

The gate verdict is driven by **`issue_policy.mode`** (zero/ratchet/audit),
a different axis than the `mode.phase` (shadow/ratchet/absolute) the body
discussed. **Resolution — TG-3 enumerates the exact edit set:**
1. Remove the `policy_mode == "audit"` short-circuit in
   `check_codacy_zero.py:308` (`_codacy_status`), `check_sonar_zero.py:428-429`
   (`main`), `check_deepsource_zero.py:303-314` (`_resolve_status`); audit
   every other `check_*_zero.py` for the same branch.
2. Remove/replace the `issue_mode` plumbing in
   `reusable-scanner-matrix.yml:217-218` (reads
   `profile.issue_policy.mode`, passes `--policy-mode`).
3. Confirm the post-deletion floor: common stack
   `quality-zero-phase1-common.yml` `issue_policy.mode` is currently
   `zero` — that becomes the only floor (audit removed from the schema +
   `profile_shape.py`).
4. Flip the self-profile `profiles/repos/quality-zero-platform.yml:14-15`
   `issue_policy.mode: audit` → removed (inherits `zero`).
5. Characterization test: with audit removed, a dirty provider FAILS where
   the audit path would have passed.

**HARD atomicity constraint (TG-3 + DoD):** audit deletion and the
baseline regression-floor gate **land in the same PR** (or the floor lands
strictly first). The platform is `mode.phase: absolute` with every scanner
`severity: block`; a window with audit removed but no floor would self-RED
the platform on ~2064 findings and block TG-4/5/6.

## A.CB-7 (CRITICAL) — Lossless scanner→context map; golden-identical before migrating

`active_required_contexts()` (`control_plane.py:413-431`) reads
hand-written lists; there is **no** severity-derivation, and the
scanner→context relation is **non-injective/partial**. **Resolution
(TG-5):**
- **Checked-in, hand-maintained mapping table**
  `scanner → {always, target, pull_request_only}` context names, handling:
  4 `codacy_*` block scanners → single `Codacy Zero`; `sonarcloud` →
  `Sonar Zero` (always) + `SonarCloud Code Analysis` (PR-only);
  `dependabot`/`socket_*` → (their actual contexts or none, recorded
  explicitly); `Coverage 100 Gate`/`Codecov Analytics` contexts that map
  to no scanner key → carried as fixed always-entries.
- **Normalize `vendors.sonar.project_key` AND `project_key_parts`**
  (list form in `swfoc-mod-menu.yml`) to one canonical shape in the
  loader; confirm the single source where codacy/deepsource keys are
  derived so `onboard.py` reuses it (no second derivation).
- **Migration is gated on byte-identity:** pin each of the 15 profiles'
  *current resolved* `required_contexts` as golden fixtures FIRST; assert
  the severity-derived set is **byte-identical** before thinning. **Any
  profile whose derived set differs is NOT a thin-derivation candidate**
  until the map is reconciled — it falls back to **B2** (keeps its
  explicit list). This prevents a fleet-wide branch-protection rewrite
  (dropping required checks = silent-merge risk; adding never-posted
  contexts = permanent PR block).
- **Drop the "~8-line" target.** Realistic invariant: stack-uniform fields
  (`scanners`, derived `required_contexts`) inherit; genuinely per-repo
  fields (`coverage.command`, `runner`, `command_shell`,
  `legacy_policy_checks`, `visual_lane`, `trigger`, `assert_mode`) stay
  explicit. **4–5 profiles (desktop/dotnet/visual) remain 30–50 lines.**

## A.CB-8 (CRITICAL) — Auto-merge security policy + fine-grained PAT

**Resolution — new policy section + TG-2/TG-3 wiring:**
1. **Auto-merge permitted ONLY when every required gate reports an
   authenticated `clean`** — auto-merge is **blocked on any `unreadable`/
   grey** state.
2. **Security-class contexts are excluded from auto-merge and require
   human approval:** CodeQL, Semgrep, Codacy security rules, Sonar
   `security_rating`/hotspots, Dependabot, Socket.
3. **Cross-repo PR writes MUST use a fine-grained `DRIFT_SYNC_PAT`**
   scoped to *exactly* the governed consumer repos with **only**
   `contents:write` + `pull_requests:write`, with an expiry/rotation
   cadence and an authenticated `whoami` probe in the TG-2 preflight —
   **not** the operator's personal broadly-scoped `gh` token. (This makes
   the cross-repo writer a rotatable, bounded machine identity and aligns
   Decision C's "and/or personal token" down to PAT-only for automated
   writes.)
4. **No-leak contract (test):** `TruthResult.source_url` / `.diagnostic`
   and all `baselines/*.json` are scrubbed of query-string credentials and
   never persist auth material (mirrors the verified-good
   `http_error_findings` "HTTP {code}" + 12-char SHA-truncation pattern).

## A.CB-9 (HIGH) — Reconcile waits for SHA-settle; exit-2 surfaced distinctly

Dashboards index minutes behind the push (existing scripts already carry
144/180-attempt settle budgets: `check_sonar_zero.py:29`
`SCOPED_ANALYSIS_RETRY_ATTEMPTS=144`; `check_codacy_zero.py:32` 180).
**Resolution (TG-4):**
- `reconcile.py` compares in-CI vs dashboard **only after the dashboard
  analysis SHA matches the CI SHA** (reuse the scripts' existing
  observed-vs-target SHA tracking). Tolerance is **0 for finding counts**
  (any post-settle delta is drift); test asserts a 1-count post-settle
  divergence trips `alert:gate-truth-drift`.
- **Rework `reusable-scanner-matrix.yml` `run()`** (`:222-223`,
  `subprocess.run(check=True)` makes exit 1 and exit 2 indistinguishable)
  so **exit 2 (unreadable)** is caught and surfaced distinctly (feeds
  `alert:scanner-unavailable` + dashboard grey), separate from exit 1
  (dirty). If not reworked, the "distinct exit code" benefit is dropped in
  favor of the JSON `verdict` field — but the rework is preferred.

## A.10 Remaining concerns folded in

- **Gate layer is net-new** (not a thin extraction): TG-1 names the
  policy-bearing gate as a new deliverable and states its relationship to
  the existing `build_quality_rollup.py` severity logic (the gate is the
  authority; rollup consumes the same `TruthResult`s). The `sys.path`
  bootstrap (`parents[2]` shim, needed because gates run `cwd=repo_dir`)
  is preserved in both the thin adapters and the new `truth/` package.
- **Dashboard grey collision:** `unreadable` gets its **own** badge
  class/label (e.g. amber `TOKEN?`), distinct from existing
  `.badge.pending`/`.unknown` (CI-not-run). TG-4 specifies the data path
  (`dashboard.json` verdict field → `app.js` class map) and fixes the
  pre-existing `success`/`partial` vs `.pass`/`.fail` class-name mismatch
  in `docs/admin/`.
- **Doc lockstep (TG-6):** every doc mentioning `audit` mode, the 9-step
  flow, or `qzp onboard` is updated together — `ONBOARDING.md`,
  `QUALITY-GATES.md`, `OPERATOR-RUNBOOK.md`, `QZP-V2-DESIGN.md` — plus the
  net-new single-page **rotation playbook** whose verification step is the
  TG-2 preflight.

## A.11 Revised PR sequence (supersedes §4 ordering)

Per the synthesis remediation order:

1. **TG-2** — token preflight + `alert:scanner-unavailable` + cron (master
   blocker becomes loud *before* any adapter relies on live reads).
2. **TG-1** — Truth Source contract + adapter refactor (auth path
   behavior-changing per A.CB-5; fold #232 `provider_enforcement.py`;
   characterization pins clean/dirty only).
3. **TG-3** — delete `issue_policy.mode: audit` (A.CB-6) + baseline floor,
   **atomic**; rebase #232 to merge at baseline-hold.
4. **TG-4** — reconciliation (SHA-settle) + `alert:gate-truth-drift` +
   dashboard grey + exit-2 surfacing.
5. **TG-5** — scanner→context map + golden-identical migration (B1 where
   identical, B2 fallback otherwise).
6. **TG-6** — `qzp onboard` + invocation surface + deterministic detection
   + doc lockstep.
7. **TG-7 (stretch, non-gating)** — `kotlin-multiplatform` stack → onboard
   `bilbo-app`.

Each PR: independently green, TDD, 100% line+branch on new modules,
self-governed, `lizard -C 15`, new scripts added to
`sonar.coverage.exclusions`, no `--no-verify`.

---

# Addendum B — Round-2 design-review-gate remediation

**Round-2 result:** 5/5 APPROVED_WITH_CONCERNS → synthesis
`PASS_WITH_REQUIRED_FIXES`. Every Round-1 blocker (CB-1..CB-9) RESOLVED;
all citations re-verified exact against live code. Two **new HIGH**
blockers (NB-A1, SEC-N1) + four MEDIUM items + three USER sign-off items.
This addendum closes the two HIGHs and the MEDIUMs; the sign-off items are
listed in §B.SIGN-OFF. **Addendum B overrides A and the body on conflict.**

## B.NB-A1 (HIGH) — Wire the baseline-hold verdict into a shared function

CB-6 enumerated the audit *deletion* but not where the baseline-hold
comparison is *inserted*; after deletion the three verdict functions
(`check_codacy_zero.py:308` `_codacy_status`, `check_sonar_zero.py:428-429`,
`check_deepsource_zero.py:303-314` `_resolve_status`) would be bare
`pass if not findings else fail`. **Resolution — single shared verdict
function (lands in the SAME PR as the audit deletion, per A.CB-6
atomicity):**

```python
# scripts/quality/truth/verdict.py
def resolve_status(count: int | None, baseline: int, deadline: date,
                   today: date) -> Literal["clean", "dirty", "unreadable"]:
    if count is None:               # auth failure / unparseable authenticated read
        return "unreadable"         # NEVER clean — the CB-5 re-mask guard
    if count > baseline:            # no new debt over the frozen floor
        return "dirty"
    if today > deadline and count > 0:   # forced burn-down to literal zero
        return "dirty"
    return "clean"
```

- **Unification (resolves the A1-vs-A2 tension):** *literal-zero is simply
  `baseline = 0`*. Strict-zero and ratchet-baseline become one function
  with a parameter. The platform runs at `baseline = <frozen count at
  TG-3 merge>` and the A2 burn-down walks that number to 0 by `deadline`,
  with identical gate code. No separate "audit/ratchet/zero" verdict
  branches survive.
- The three `check_*_zero.py` **delegate** to `resolve_status(...)` (count
  from the adapter's authenticated read; baseline+deadline from
  `baselines/<slug>/<provider>.json`; `today`/`deadline` passed in — never
  `date.today()` inside the pure function, for testability).
- **Contract tests (pinned, in the same PR):**
  (a) `count==baseline, today<deadline → clean` (#232 merges at baseline-hold);
  (b) `count==baseline+1 → dirty` (no new debt);
  (c) `today>deadline, count>0 → dirty` (forced burn-down);
  (d) `count is None (authenticated unparseable) → unreadable, never clean`
  (the re-mask guard);
  (e) `baseline==0, count==0 → clean` and `baseline==0, count==1 → dirty`
  (literal-zero is baseline-zero).

## B.SEC-N1 (HIGH) — Disambiguate + wire the security-class auto-merge guard

CB-8 clause 2 was ambiguous (could read as *dropping* security contexts)
and the existing `scripts/quality/security_class_guard.py`
(`filter_auto_merge_candidates` / `ensure_pr_only_for_security`, finding-
level) is not wired to `apply_drift_pr.py:113-119`'s unconditional
`gh pr merge --auto --squash`. **Resolution (restate clause 2 precisely):**
1. **Security-class required contexts STAY required** — they are NEVER
   dropped from the required-checks set. (The earlier wording is void.)
2. A PR is **auto-merge-ineligible** (must not arm `--auto`; requires human
   approval via CODEOWNERS + required-review) if **either**: its diff
   touches security-relevant paths, **or** any auto-remediation in it is
   classified security-class by
   `security_class_guard.filter_auto_merge_candidates`.
3. **Wire it:** `apply_drift_pr.py` (and the new gate auto-merge path) must
   import `security_class_guard` and gate the `gh pr merge --auto` arming
   through it, so the finding-level guard and the context-level policy
   **compose** rather than diverge. Auto-merge arms ONLY when every required
   gate is an **authenticated `clean`** (never `unreadable`/grey — clause 1).
4. Add a test: a security-class drift PR does NOT get `--auto` armed; a
   pure-style drift PR does.

## B.MEDIUM items

- **CB-1 deadline (concrete date + process):** the baseline `deadline`
  field is now a hard date, not a placeholder. **Platform burn-down
  deadline = 2026-09-30** (90 days from this design, mirroring the design's
  own `escalation_date` convention). Process: `qzp baseline freeze <slug>`
  captures `{count, ref, captured_at, deadline}` per provider at TG-3 merge;
  the default deadline is `captured_at + 90d`, overridable per repo in the
  profile (`mode.ratchet.target_date`). After `deadline`, `resolve_status`
  forces `dirty` while `count>0` — baseline-hold cannot silently become
  permanent (defeating locked A2).
- **CB-4 (`qzp` install surface):** `pyproject.toml` has no `[project]`/
  `[build-system]` table today. TG-6 adds a minimal
  `[build-system]` (setuptools) + `[project]` (name `quality-zero-platform`,
  version, `[project.scripts] qzp = "scripts.quality.qzp_cli:main"`). The
  repo-root **`./qzp` shim** (wrapping `python -m scripts.quality.qzp_cli`)
  is the **primary, zero-install** documented path; `pip install -e .` /
  `pipx` is the secondary packaged path. Docs pin the shim form.
- **Doc-lockstep (5th doc):** add **`docs/STRICT-ZERO-CHECKLIST.md`** to the
  A.10 lockstep set — it carries the literal `issue_policy.mode: audit`
  value (`:84`) that B.NB-A1/CB-6 deletes and a "no audit mode" promise
  (`:13`). TG-3 updates it in lockstep with the schema change. Full set:
  ONBOARDING, QUALITY-GATES, OPERATOR-RUNBOOK, QZP-V2-DESIGN,
  STRICT-ZERO-CHECKLIST (+ the net-new rotation playbook).
- **CB-2 exclude mechanism:** `inventory/repos.yml` gains an additive
  top-level `exclude:` list (slugs never governed, e.g.
  `Prekzursil/skills-introduction-to-github` with a `reason`). `fleet_inventory.py`
  treats excluded slugs as intentionally-ungoverned (no
  `alert:repo-not-profiled`). TG-6 deliverable.

## B.SIGN-OFF — three items surfaced to the user (proceeding unless vetoed)

These are genuine product/sequencing decisions the gate flagged as the
user's to confirm. They are **reversible**; I proceed with them unless the
user objects:

1. **M1 "platform green" = baseline-hold green**, not literal-zero green
   (contingent on the atomic TG-3 audit-delete + floor PR). Literal-zero is
   the M2 burn-down target by 2026-09-30.
2. **#232 merges at baseline-hold first**; locked **A2 literal-zero is
   preserved as the M2 target** behind the shipped subsystem.
3. **CB-7 expectation adjustment:** onboarding becomes *one command +
   deterministic detection + inheritance*, but **not** "≤8 lines for every
   repo" — 4-5 desktop/dotnet/visual profiles stay 30-50 lines (irreducible
   per-repo fields), and the scanner→context map is **hand-maintained**
   (checked-in, reviewed), not fully auto-generated. The "no micromanagement"
   goal holds for the common case and for new-repo onboarding; it is honest
   about the irreducible per-repo tail.

## B — Revised PR sequence (unchanged from A.11; NB-A1 folds into TG-3, SEC-N1 into TG-2/TG-3)

TG-2 → TG-1 → TG-3 (now includes `truth/verdict.py` + baseline insertion +
security-guard wiring, atomic) → TG-4 → TG-5 → TG-6 → TG-7 (stretch).

---

# Addendum C — Round-3 design-review-gate remediation

**Round-3 result:** NB-A1 + all 4 MEDIUMs RESOLVED, but synthesis `FAIL`
on two new HIGH blockers (ARCH-B1, SEC-N1-W1) — both *second-axis*
silent-failure vectors symmetric to ones already fixed. This addendum
closes them + folds the Designer non-blocking nits. **Addendum C overrides
A/B and the body on conflict.**

## C.ARCH-B1 (HIGH) — Baseline-READ failure is fail-closed (symmetric to count)

`resolve_status` hardened the count axis but `baseline` was `int` and the
baseline loader was unspecified — a fail-open loader (missing/corrupt
`baselines/<slug>/<provider>.json`) would re-mask debt via
`count ≤ huge_baseline → clean`. **Resolution — make `baseline` the
symmetric twin of `count`:**

```python
# scripts/quality/truth/verdict.py  (revises B.NB-A1)
def resolve_status(count: int | None, baseline: int | None,
                   deadline: date, today: date) -> Literal["clean","dirty","unreadable"]:
    if count is None or baseline is None:   # EITHER input unreadable -> fail-closed
        return "unreadable"                 # never clean — both re-mask axes closed
    if count > baseline:
        return "dirty"
    if today > deadline and count > 0:
        return "dirty"
    return "clean"
```

- **Baseline loader contract (`truth/baseline.py`):** a missing,
  unparseable, or credential-scrubbed baseline file returns
  **`baseline = None`** → `unreadable` → exit 2 → BLOCK +
  `alert:scanner-unavailable`. It **never** skip-gates and **never**
  defaults to a permissive sentinel. (A repo with no baseline yet is *not*
  the same as baseline 0 — an explicit `baseline: 0` frozen file means
  "literal-zero enforced"; a *missing* file means "unknown → BLOCK until
  frozen".)
- **Pinned contract tests (added to B.NB-A1's set):** (f) `baseline is
  None → unreadable` (missing/corrupt file); (g) `count is None AND
  baseline is None → unreadable`; (h) a credential-scrubbed/garbage
  baseline JSON → loader returns None → NOT clean.

## C.SEC-N1-W1 (HIGH) — Drift-PR auto-merge uses a PATH classifier, not the finding classifier

B.SEC-N1 clause 3 routed drift PRs through `security_class_guard.filter_auto_merge_candidates`,
but drift entries (`apply_drift_pr.py` `_collect_out_of_sync` →
`{status, output_path, proposed_content}`) carry no scanner/category/CWE
fields, so every drift entry classifies as `auto_merge_ok` — a **no-op** on
the exact surface it must protect. Drift-sync's primary payload is
`.github/workflows/` templates, so unreviewed fleet-wide auto-merge of CI
files would survive. **Resolution — two distinct guards composing:**

- **New path-level guard `scripts/quality/security_path_guard.py`** with an
  explicit, checked-in `SECURITY_RELEVANT_PATHS` set (a drift PR touching
  ANY of these is auto-merge-INELIGIBLE → no `--auto`, requires human
  review via CODEOWNERS + required-review):
  - `.github/workflows/**`, `.github/actions/**`
  - `.github/CODEOWNERS`, branch-protection / ruleset config
    (`generated/rulesets/**`, `.github/*ruleset*`)
  - gate + truth code: `scripts/quality/check_*_zero.py`,
    `run_quality_zero_gate.py`, `provider_enforcement.py`,
    `scripts/quality/truth/**`, `scripts/quality/security_*`
  - scanner config: `.github/dependabot.yml`, `.github/codeql/**`,
    `codecov.yml`, `.codacy.yaml`, `sonar-project.properties`,
    `.semgrep.yml`, `.deepsource.toml`, `.qlty/**`
- **Wiring:** `apply_drift_pr.py` (and the new gate auto-merge path) gate
  the `gh pr merge --auto` arming on `security_path_guard.is_auto_merge_safe(changed_paths)`.
  The two guards **compose by surface**: path-guard for drift/template PRs
  (clause 2a), finding-level `security_class_guard` for QRv2 finding-driven
  PRs (clause 2b). Auto-merge arms ONLY when (i) the PR touches no
  security-relevant path **and** (ii) every required gate is an
  authenticated `clean` (never `unreadable`/grey).
- **Pinned test (clause 4):** a drift PR touching `.github/workflows/`
  does NOT arm `--auto` (asserts the path arm bites where the finding arm
  no-ops); a docs-only drift PR with all gates authenticated-clean DOES.
- `CODEOWNERS` is a per-consumer-repo branch-protection control — surfaced
  as a documented precondition (like `DRIFT_SYNC_PAT`), not assumed.

## C.NITS (Designer, non-blocking — folded for completeness)

- **`qzp baseline freeze` sequencing:** baseline freeze/load logic is a
  **TG-3 module** (`truth/baseline.py` + a freeze entrypoint), independent
  of the `qzp` CLI surface (TG-6). TG-6 merely *aliases* it as
  `qzp baseline freeze`. TG-3 writes/reads baselines directly; no
  dependency on TG-6.
- **Windows-first invocation (operator is on Windows 11):** the **canonical
  documented form is `python -m scripts.quality.qzp_cli ...`** (cross-
  platform). Companions shipped: `qzp.cmd` + `qzp.ps1` (Windows) and `./qzp`
  (POSIX). The bare `./qzp` shim is *not* the primary Windows path; docs
  lead with `python -m`.
- **Deadline field reuse:** use the **pre-existing `mode.ratchet.escalation_date`**
  (already in the schema + `alert_triggers.py:139` + `test_profile_schema_v2.py:195`
  uses `2026-09-30`) as the **hard** burn-down deadline that
  `resolve_status` enforces. Keep the existing `mode.ratchet.target_date`
  as the **soft** target. No net-new deadline field; documented split.

## C — Sequence unchanged

TG-2 → TG-1 → TG-3 (audit-delete + `truth/verdict.py` with both axes
fail-closed + `truth/baseline.py` + `security_path_guard.py` wiring, atomic)
→ TG-4 → TG-5 → TG-6 → TG-7 (stretch).

---

# Addendum D — Design-review-gate closure (PASS) + carried implementation notes

**Round 4 (final):** Architect + Security both **APPROVE**; synthesis
**`PASS`, `ready_for_writing_plans: true`**, zero open CRITICAL/HIGH. The
design is approved to proceed to `writing-plans`. Gate summary: R1 9
blockers → R2 2 HIGH → R3 2 HIGH → R4 clean, every finding verified against
live code.

**Carried MEDIUM implementation notes (resolve during the named TG, NOT
design blockers — all fail in the safe/over-blocking direction):**

1. **[TG-3] `list_templates` output-path vs `SECURITY_RELEVANT_PATHS` globs.**
   `template_render.py:119-143` emits repo-ROOT paths (`ci.yml`,
   `dependabot.yml`) by stripping the `templates/common|stack/<stack>`
   prefix — not `.github/`-prefixed destinations the guard globs assume.
   Inert for security today (a root-level `ci.yml` is not executed by
   GitHub), but TG-3 MUST (a) reconcile the `list_templates` output-path
   mapping with the guard globs, and (b) write the `security_path_guard`
   pinned test against the **actual** paths `list_templates` emits, not
   synthetic `.github/workflows/` strings — else the test greens while a
   future correctly-placed payload diverges.
2. **[TG-3] `deadline` axis is intentionally not fail-closed.** `deadline`
   stays a non-optional `date` with no `None`-guard: it appears only in
   `today > deadline and count > 0 → dirty` (monotonically stricter), so a
   missing/failed `escalation_date` read cannot mask new debt (`count >
   baseline` still bites); worst case is baseline-hold persisting past
   intent (the CB-1 concern), never silent-green. TG-3 states this
   rationale explicitly in code comments.
3. **[TG-3] Baseline-data deploy atomicity.** A fail-closed loader gates
   every governed repo to BLOCK until its frozen baseline files exist. The
   atomic TG-3 PR MUST land the baseline DATA files (`baselines/<slug>/<provider>.json`)
   together with the verdict/loader code (worst case is over-blocking, never
   silent-green, but avoid a self-inflicted fleet BLOCK window).

**User sign-off items (3, from §B.SIGN-OFF):** carried forward; non-blocking
for the gate. Proceeding unless the user vetoes.

**Next:** `writing-plans` for **TG-2** (token preflight) → plan-review-gate
(3 adversarial: Feasibility, Completeness, Scope+Alignment) → metaswarm
orchestrated execution. Then TG-1, TG-3 (atomic), TG-4, TG-5, TG-6, TG-7.
```
