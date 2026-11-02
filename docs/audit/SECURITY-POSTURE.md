# QZP Fleet Security Posture Checklist

- **Date:** 2026-10-21
- **Lane:** L10 (security posture verification + QZP triage)
- **Scope:** SECURITY.md resolution fleet-wide (account default + repo-level),
  private vulnerability reporting (PVR) on fleet members, and verification of
  the security claims recorded in `inventory/repos.yml`.
- **Rule honored:** fix ONLY clear gaps (a missing file where one was believed
  committed); no re-rollout. **Result: no clear gaps found — nothing fixed.**

## Checks and outcomes

Every row was executed 2026-10-21 with `gh` authenticated as `Prekzursil`.

### 1. Account-default SECURITY.md exists (fallback source)

| Evidence | Outcome |
|---|---|
| `gh api repos/Prekzursil/.github/contents/SECURITY.md` | **EXISTS** — path `SECURITY.md`, size 1237, sha `6ce977f1c94fc2346de3ec9070e19b69d8c0e958` |

This is the fallback policy for every repo owned by the account that has no
repo-level SECURITY.md (root, `docs/`, or `.github/`).

### 2. Public repos — policy resolution spot-check (3 repos)

Method: community/profile endpoint (community files resolve at the account
level) plus repo contents API for the repo-level file. The community/profile
API does not expose `security_policy` as a file key (it lists code_of_conduct,
contributing, issue_template, pull_request_template, license, readme), so the
repo-file check is the security-policy evidence.

| Repo | community/profile | Repo-level SECURITY.md |
|---|---|---|
| `quality-zero-platform` | health 57%, profile files resolve | **OWN** — `SECURITY.md` (root, sha `811df04…`) **and** `.github/SECURITY.md` (sha `a7f25c1…`) |
| `fractal-darkroom` | health 100% | **OWN** — `SECURITY.md` (root) |
| `momentstudio` | health 100% | **OWN** — `SECURITY.md` (root) |

Commands: `gh api repos/Prekzursil/<repo>/community/profile` and
`gh api repos/Prekzursil/<repo>/contents/{SECURITY.md,.github/SECURITY.md,docs/SECURITY.md}`.

### 3. Public QZP-fleet sweep (all remaining fleet members)

Same contents check, root then `.github/`. **All 13 have their OWN root
SECURITY.md:** Airline-Reservations-System, DevExtreme-Filter-Go-Language,
event-link, Personal-Finance-Management, Reframe, SWFOC-Mod-Menu,
Star-Wars-Galactic-Battlegrounds-Save-Game-Editor, TanksFlashMobile, WebCoder,
env-inspector, codeblocks-pretty-prints-stable, tracelines, pbinfo-get-unsolved.

Together with §2: **every public QZP-fleet repo ships a repo-level
SECURITY.md**; none rely on the account default.

### 4. Private repos — sample of 5

Checked root, `.github/`, `docs/` via contents API (community/profile is
public-repo only, so the repo-file API is the verifier):

| Repo | Repo-level SECURITY.md |
|---|---|
| `agent-skills-toolchain` | none — account default applies |
| `PrimariRo` | none — account default applies |
| `security-kb` | none — account default applies |
| `battlecats-toolkit` | none — account default applies |
| `verdict-app` | none — account default applies |

Reading: sampled private repos carry no repo-level file, so the policy that
resolves for members is the account-default `Prekzursil/.github/SECURITY.md`
(§1, exists). Per the no-re-rollout rule this is recorded as posture, not
"fixed": no inventory entry claims an in-repo SECURITY.md for any of them
(see §6), so none is "a missing file where one was believed committed".

### 5. Private Vulnerability Reporting (PVR)

| Repo | `gh api repos/<repo>/private-vulnerability-reporting` | Inventory claim |
|---|---|---|
| `fractal-darkroom` | `{"enabled":true}` | inventory/repos.yml:97 claims enabled — **VERIFIED** |
| `llm-anthology` | `{"enabled":true}` | inventory/repos.yml:87 claims enabled — **VERIFIED** |
| `quality-zero-platform` | `{"enabled":true}` | (control plane; none recorded, verified anyway) |
| `agent-skills-toolchain` | HTTP 404 | no claim recorded — PVR is a public-repo feature; the endpoint 404s for private repos. Not a gap. |
| `PrimariRo` | HTTP 404 | same — not a gap. |

### 6. Inventory security claims vs reality

`inventory/repos.yml` security mentions (grep `security|vulnerab`):
- `fractal-darkroom` (line 97): "Private vulnerability reporting enabled;
  SECURITY.md in-repo" — **both verified true** (§2, §5).
- `llm-anthology` (line 87): "Dependabot + Private Vulnerability Reporting
  enabled" — **PVR verified true** (§5). (Dependabot not re-checked this pass.)
- No other inventory entry claims SECURITY.md or PVR — so §4's private-repo
  findings contradict nothing.

### 7. Non-gaps recorded for the operator

- `quality-zero-platform` ships TWO policy files that DIFFER byte-wise (root
  sha `811df04…` vs `.github/` sha `a7f25c1…`) — both state the same policy
  (use PVR, do not open public issues; root names the repo-specific advisory
  URL). Redundant but consistent; not a missing file, so not auto-fixed.
- Private fleet members expose no PVR endpoint (public-repo-only feature);
  their disclosure channel is the account-default SECURITY.md + private
  contact, which is coherent for private repos.
- Tool pins (biome 2.5.15, oxlint 1.69.0, etc.) were verified 2026-10-21 in
  fractal-darkroom `docs/BIOME-NOTES.md` (pin inventory table) — read this
  pass, not re-measured here.

## Verdict

**Posture is as documented: no clear gaps, nothing fixed, no re-rollout.**
The account default exists as a real fallback; every public fleet repo
additionally carries its own policy; the two inventory PVR claims check out.

## Re-running this checklist

```bash
gh api repos/Prekzursil/.github/contents/SECURITY.md --jq '.sha'          # default exists
for r in <public fleet>; do gh api "repos/Prekzursil/$r/contents/SECURITY.md" --jq '.sha'; done
for r in <private sample>; do gh api "repos/Prekzursil/$r/contents/SECURITY.md" --jq '.sha'; done
gh api repos/Prekzursil/fractal-darkroom/private-vulnerability-reporting  # {"enabled":true}
```
