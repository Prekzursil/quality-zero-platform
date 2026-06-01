# Truthful-Gate Subsystem — Design

**Status:** reconstructed from session handoff `.remember/today-2026-06-01.md`
("designed truthful-gate subsystem (dashboard-truth scanner → QZP
profiles, zero-conf onboarding)"). Pending **user scope confirmation**,
then the mandatory **design-review-gate** (5 agents) per this repo's
`CLAUDE.md`.

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
```
