#!/usr/bin/env bash
# Charter-check (lean 6-gate). FAILS CI if the active gate set in the reusable
# workflow drifts from the charter declared in .quality/charter.yml:
#   (a) every charter gate's workflow_step MUST be wired as a step in
#       .github/workflows/reusable-quality.yml, AND
#   (b) NO deleted/banned gate (sonar/codacy/.../renovate) may be wired as a
#       `gate-*` enforcement step.
#
# Pure bash + grep/sed (no Python, no PyYAML) so it sidesteps QZT's py2-compat
# commit contract and runs identically locally and in CI. Terminal-state output:
# emits PASS / FAIL lines and exits non-zero on any drift.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHARTER="$ROOT/.quality/charter.yml"
WORKFLOW="$ROOT/.github/workflows/reusable-quality.yml"

fail=0
note() { printf '%s\n' "$*"; }

[ -f "$CHARTER" ]  || { note "FAIL charter-check: missing $CHARTER"; exit 1; }
[ -f "$WORKFLOW" ] || { note "FAIL charter-check: missing $WORKFLOW"; exit 1; }

# Strip inline comments + surrounding quotes from a scalar value.
clean() { sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'; }

# ── (a) every charter gate workflow_step is present in the workflow ──────────
required_steps="$(grep -E '^\s*workflow_step:' "$CHARTER" | sed -E 's/^\s*workflow_step:\s*//' | clean)"
if [ -z "$required_steps" ]; then
  note "FAIL charter-check: no workflow_step entries found in charter"
  exit 1
fi
while IFS= read -r step; do
  [ -z "$step" ] && continue
  if grep -Eq "name:\s*${step}" "$WORKFLOW"; then
    note "PASS charter-gate step wired: ${step}"
  else
    note "FAIL charter-gate step MISSING from workflow: ${step}"
    fail=1
  fi
done <<< "$required_steps"

# ── (b) no deleted/banned gate is wired as a gate-* enforcement step ─────────
# Collect the deleted_gates list (lines between 'deleted_gates:' and the next
# top-level key). Each banned name must not appear as a `name: gate-<name>` step.
banned="$(awk '
  /^deleted_gates:/ {grab=1; next}
  grab && /^[a-zA-Z]/ {grab=0}
  grab && /^\s*-\s*/ {sub(/^\s*-\s*/,""); sub(/#.*/,""); gsub(/[[:space:]]/,""); if (length($0)) print}
' "$CHARTER")"

# Active gate-* enforcement step names in the workflow.
active_gate_steps="$(grep -E '^\s*-\s*name:\s*gate-' "$WORKFLOW" | sed -E 's/^\s*-\s*name:\s*//' | clean)"

while IFS= read -r bad; do
  [ -z "$bad" ] && continue
  if printf '%s\n' "$active_gate_steps" | grep -Eiq "(^|[^a-z])${bad}([^a-z]|$)"; then
    note "FAIL banned gate '${bad}' is wired as an enforcement step (charter forbids it)"
    fail=1
  fi
done <<< "$banned"

# ── CodeQL must not be a gate (nightly/public-only) ──────────────────────────
if printf '%s\n' "$active_gate_steps" | grep -Eiq 'codeql'; then
  note "FAIL CodeQL wired as a gate step; charter allows CodeQL only as a nightly scan on public repos"
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  note "FAILED: charter-check — active gate set != lean 6-gate charter"
  exit 1
fi
note "SUCCESS: charter-check — active gate set matches the lean 6-gate charter"
