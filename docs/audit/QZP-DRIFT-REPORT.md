# QZP Drift Report

- **Date:** 2026-10-21
- **Audited commit:** `d368772061e18bd3db9d108cc027a0d98f432b73` (`origin/main`, "Onboard fractal-darkroom as lean member; bump lean biome pin to 2.5.15 (#312)")
- **Command run:** `python scripts/quality/generate_ruleset_payload.py --inventory inventory/repos.yml --output-dir generated/rulesets`
- **Result:** `git diff` after regeneration was **empty** — zero drift.

## What was checked

The generator re-emitted all **19** ruleset payloads listed in
`inventory/repos.yml` into `generated/rulesets/`:

agent-skills-toolchain, airline-reservations-system, codeblocks-pretty-prints-stable,
devextreme-filter-go-language, env-inspector, event-link, fractal-darkroom,
llm-anthology, momentstudio, pbinfo-get-unsolved, personal-finance-management,
primariro, quality-zero-platform, reframe,
star-wars-galactic-battlegrounds-save-game-editor, swfoc-mod-menu,
tanksflashmobile, tracelines, webcoder.

`git status --porcelain` was empty both before and after the run, so no file
content changed and no stray files were produced. The committed
`generated/rulesets/*.json` artifacts are byte-identical to what the current
profiles + inventory regenerate.

## Interpretation

The checked-in ruleset payloads are in sync with the checked-in profiles and
inventory at the audited commit. No drift-only fix patch was needed.

## Re-running this audit

```bash
python scripts/quality/generate_ruleset_payload.py \
  --inventory inventory/repos.yml --output-dir generated/rulesets
git diff --exit-code generated/rulesets/   # empty = no drift
```

This mirrors what CI already enforces: `.github/workflows/verify.yml` →
`scripts/verify` runs
`scripts/quality/validate_control_plane.py --write-generated generated/rulesets`
plus a full regeneration into `build/rulesets`, with a working-tree churn guard,
on every push and pull_request to main.
