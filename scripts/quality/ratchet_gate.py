#!/usr/bin/env python3
"""Layer-1 monotonic-decreasing ratchet gate (QZP Layer-1).

Reads the canonical rollup (``quality-rollup/canonical.json`` produced by
``scripts.quality.rollup_v2``) and a committed per-repo baseline
(``.quality/ratchet.json`` in the *consumer* repo) and enforces the
strict-zero regression contract:

    1. ASSERT  -- for every MEASURED provider, ``current_count <= ceiling``
                  AND new-code findings == 0. Fails CI otherwise.
    2. AUTO-LOWER -- when ``current_count < ceiling`` for a measured
                  provider, the ceiling is lowered to ``current_count``
                  (monotone; never raised) and the updated baseline is
                  written back so the caller can commit it to the PR branch.
    3. RAISE  -- never automatic. A ceiling may only increase via a
                  human-reviewed PR (``--seed``) which is logged in
                  ``ratchet.json.audit_log``.
    4. SEED   -- first baseline, or a provider transitioning from
                  ``unmeasured`` to ``measured`` (e.g. DeepSource 0 runs ->
                  real analysis). Seeding is the *only* allowed raise and is
                  human-reviewed + dated + logged.

WHY THIS IS NOT A DASHBOARD SUPPRESSION
---------------------------------------
* The gate writes NOTHING to any analyzer: no inline lint-suppression
  comments, no SARIF dismissal, no SonarCloud won't-fix, no Codacy ignore,
  no scanner ignore-file entry. Every finding stays OPEN on its provider
  dashboard and in ``canonical.json`` (Codacy still shows 1,975; Sonar still
  shows 329).
* ``.quality/ratchet.json`` is read ONLY by this CI gate. It is never fed
  back to a provider and never changes what a provider reports.
* The ceiling is monotone-decreasing + measured-guarded, so it can NEVER
  silently *permit more* findings. It can only answer the single question
  "did this PR make provider X worse than the committed baseline?" and it
  converges to 0.
* A provider whose lane is absent / errored / stale is treated as
  ``unmeasured`` -- the gate HOLDS the ceiling and fails-closed; it never
  lowers off a broken scan. This is the load-bearing guard that prevents the
  ratchet from accidentally locking in a false floor.

Exit codes: 0 = pass (gate green), 1 = regression / unmeasured-block (gate
red), 2 = usage / IO error.
"""

from __future__ import absolute_import

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from scripts.quality.ratchet_canonical import (
    CONTEXT_PROVIDER_HINTS,
    KNOWN_PROVIDERS,
    count_new_code,
    expected_providers_from_profile,
    measured_providers_from_canonical,
    providers_with_errors,
    read_provider_totals,
)
from scripts.quality.ratchet_diff import (
    RatchetError,
    _normalize_path,
    _resolve_diff_base,
    _run_git,
    _suffix_path_match,
    added_line_ranges,
    is_new_code_finding,
)

# Re-export the git/new-code detection + canonical-reading helpers so callers
# and tests can keep using ``scripts.quality.ratchet_gate.<name>`` after those
# units moved to ``ratchet_diff`` / ``ratchet_canonical`` (keeps each module
# under its complexity ceiling without changing the public surface).
__all__ = [
    "CONTEXT_PROVIDER_HINTS",
    "KNOWN_PROVIDERS",
    "RatchetError",
    "_normalize_path",
    "_resolve_diff_base",
    "_run_git",
    "_suffix_path_match",
    "added_line_ranges",
    "count_new_code",
    "expected_providers_from_profile",
    "is_new_code_finding",
    "measured_providers_from_canonical",
    "providers_with_errors",
    "read_provider_totals",
]

RATCHET_SCHEMA_VERSION = "qzp-ratchet/1"


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def today() -> str:
    """Return today's date (UTC) as ``YYYY-MM-DD``."""
    return datetime.now(UTC).date().isoformat()


# --------------------------------------------------------------------------- #
# ratchet.json (baseline) model
# --------------------------------------------------------------------------- #
@dataclass
class AuditEntry:
    """One ``audit_log`` record (bundled so ``_log`` takes a single arg)."""

    action: str
    provider: str
    ceiling: int
    sha: str
    actor: str = "ci-bot"

    def as_dict(self) -> Dict[str, Any]:
        """Render the record as the JSON-serialisable audit dict."""
        return {
            "action": self.action,
            "provider": self.provider,
            "ceiling": int(self.ceiling),
            "sha": self.sha,
            "actor": self.actor,
            "at": utc_now(),
        }


@dataclass
class RatchetBaseline:
    """In-memory view of ``.quality/ratchet.json``."""

    raw: Dict[str, Any]

    @property
    def providers(self) -> Dict[str, Any]:
        """Return the mutable per-provider ceiling map."""
        return self.raw.setdefault("providers", {})

    def ceiling(self, provider: str) -> int | None:
        """Return the committed ceiling for ``provider`` (None if not seeded)."""
        entry = self.providers.get(provider)
        if not isinstance(entry, Mapping):
            return None
        if not entry.get("measured", True):
            return None
        value = entry.get("ceiling")
        return int(value) if isinstance(value, int | float) else None

    def is_seeded(self, provider: str) -> bool:
        """Return True when ``provider`` has a committed, measured ceiling."""
        return self.ceiling(provider) is not None

    def lower(self, provider: str, new_ceiling: int, head_sha: str) -> None:
        """Lower ``provider``'s ceiling to ``new_ceiling`` (monotone)."""
        entry = self.providers.setdefault(provider, {})
        entry["ceiling"] = int(new_ceiling)
        entry["measured"] = True
        entry["last_lowered_sha"] = head_sha
        entry["last_lowered_at"] = utc_now()
        self._log(
            AuditEntry("auto-lower", provider, new_ceiling, head_sha))

    def seed(self, provider: str, ceiling: int, head_sha: str,
             actor: str) -> None:
        """Seed/raise ``provider``'s ceiling (human-reviewed; logged)."""
        entry = self.providers.setdefault(provider, {})
        old = entry.get("ceiling")
        entry["ceiling"] = int(ceiling)
        entry["measured"] = True
        entry["seeded_at"] = utc_now()
        entry["seeded_by"] = actor
        action = ("raise"
                  if isinstance(old, int | float) and ceiling > old else "seed")
        self._log(AuditEntry(action, provider, ceiling, head_sha, actor=actor))

    def _log(self, record: "AuditEntry") -> None:
        """Append one audit record."""
        log = self.raw.setdefault("audit_log", [])
        log.append(record.as_dict())


def new_baseline(repo_slug: str, head_sha: str) -> RatchetBaseline:
    """Return an empty baseline scaffold."""
    return RatchetBaseline(
        raw={
            "schema_version": RATCHET_SCHEMA_VERSION,
            "repo": repo_slug,
            "baseline_date": today(),
            "baseline_sha": head_sha,
            "providers": {},
            "audit_log": [],
        })


def load_baseline(path: Path, repo_slug: str,
                  head_sha: str) -> RatchetBaseline:
    """Load ``ratchet.json`` or scaffold a fresh one if missing."""
    if not path.is_file():
        return new_baseline(repo_slug, head_sha)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return new_baseline(repo_slug, head_sha)
    if not isinstance(raw, dict):
        return new_baseline(repo_slug, head_sha)
    raw.setdefault("schema_version", RATCHET_SCHEMA_VERSION)
    raw.setdefault("providers", {})
    raw.setdefault("audit_log", [])
    return RatchetBaseline(raw=raw)


# --------------------------------------------------------------------------- #
# core evaluation
# --------------------------------------------------------------------------- #
@dataclass
class ProviderVerdict:
    """Per-provider gate outcome."""

    provider: str
    state: str  # "pass" | "regression" | "new-code" | "unmeasured" | "unseeded"
    current: int | None
    ceiling: int | None
    new_code: int
    lowered_to: int | None = None


@dataclass
class GateResult:
    """Aggregate gate outcome."""

    verdicts: List[ProviderVerdict] = field(default_factory=list)
    changed: bool = False

    @property
    def failed(self) -> bool:
        """True when any provider failed the assert phase.

        ``unseeded`` fails too: a measured provider with no committed ceiling
        means the baseline is incomplete, so the gate cannot make a safe
        assertion. The fix is a human-reviewed SEED PR (``--seed``), not a
        silent pass that would let the first PR sail through with no ceiling.
        """
        return any(
            v.state in {"regression", "new-code", "unmeasured", "unseeded"}
            for v in self.verdicts)


def _classify_unmeasured(
    *,
    provider: str,
    baseline: "RatchetBaseline",
    expected: Set[str],
    errored: Set[str],
    current: int | None,
    ceiling: int | None,
    new_code: int,
) -> ProviderVerdict | None:
    """Return an UNMEASURED verdict when a provider must be held, else None."""
    if provider in expected or baseline.is_seeded(
            provider) or provider in errored:
        return ProviderVerdict(provider, "unmeasured", current, ceiling,
                               new_code)
    # Not expected, not seeded, not measured -> ignore silently.
    return None


def _classify_unseeded_ceiling(
    *,
    provider: str,
    baseline: "RatchetBaseline",
    current: int | None,
    current_val: int,
    new_code: int,
    head_sha: str,
    seed_missing: bool,
) -> Tuple[ProviderVerdict, bool]:
    """Resolve a measured provider that has no committed ceiling yet."""
    if seed_missing:
        baseline.seed(provider, current_val, head_sha, actor="seed-pr")
        verdict = ProviderVerdict(provider, "pass", current_val, current_val,
                                  new_code, lowered_to=current_val)
        return verdict, True
    return ProviderVerdict(provider, "unseeded", current, None, new_code), False


def _classify_against_ceiling(
    *,
    provider: str,
    baseline: "RatchetBaseline",
    current_val: int,
    ceiling: int,
    new_code: int,
    head_sha: str,
) -> Tuple[ProviderVerdict, bool]:
    """Resolve a measured, already-seeded provider against its ceiling."""
    # Clean-as-you-code: any new-code finding fails regardless of ceiling.
    if new_code > 0:
        state = "new-code"
    elif current_val > ceiling:
        state = "regression"
    elif current_val < ceiling:
        # Below ceiling -> pass + auto-lower (monotone).
        baseline.lower(provider, current_val, head_sha)
        verdict = ProviderVerdict(provider, "pass", current_val, ceiling,
                                  new_code, lowered_to=current_val)
        return verdict, True
    else:
        state = "pass"
    return ProviderVerdict(provider, state, current_val, ceiling,
                           new_code), False


def _classify_measured(
    *,
    provider: str,
    baseline: "RatchetBaseline",
    current: int | None,
    ceiling: int | None,
    new_code: int,
    head_sha: str,
    seed_missing: bool,
) -> Tuple[ProviderVerdict, bool]:
    """Classify a measured provider. Returns ``(verdict, baseline_changed)``."""
    current_val = int(current or 0)
    if ceiling is None:
        return _classify_unseeded_ceiling(
            provider=provider,
            baseline=baseline,
            current=current,
            current_val=current_val,
            new_code=new_code,
            head_sha=head_sha,
            seed_missing=seed_missing,
        )
    return _classify_against_ceiling(
        provider=provider,
        baseline=baseline,
        current_val=current_val,
        ceiling=ceiling,
        new_code=new_code,
        head_sha=head_sha,
    )


@dataclass
class ClassifyInputs:
    """Bundle the per-run inputs to :func:`classify` (one object, few locals)."""

    baseline: RatchetBaseline
    totals: Mapping[str, int]
    new_code: Mapping[str, int]
    measured: Set[str]
    errored: Set[str]
    expected: Set[str]
    head_sha: str
    seed_missing: bool

    def universe(self) -> List[str]:
        """Return the sorted provider universe to evaluate."""
        return sorted(
            set(self.totals) | self.measured | self.expected
            | set(self.baseline.providers))


def _classify_one(provider: str,
                  inputs: ClassifyInputs) -> Tuple[ProviderVerdict | None, bool]:
    """Classify a single provider. Returns ``(verdict_or_none, changed)``."""
    current = inputs.totals.get(provider)
    nc = int(inputs.new_code.get(provider, 0))
    ceiling = inputs.baseline.ceiling(provider)
    if provider not in inputs.measured or provider in inputs.errored:
        verdict = _classify_unmeasured(
            provider=provider,
            baseline=inputs.baseline,
            expected=inputs.expected,
            errored=inputs.errored,
            current=current,
            ceiling=ceiling,
            new_code=nc,
        )
        return verdict, False
    return _classify_measured(
        provider=provider,
        baseline=inputs.baseline,
        current=current,
        ceiling=ceiling,
        new_code=nc,
        head_sha=inputs.head_sha,
        seed_missing=inputs.seed_missing,
    )


def classify(inputs: ClassifyInputs) -> GateResult:
    """Run the ASSERT + AUTO-LOWER state machine and return the verdict.

    For each provider in (measured u expected u already-seeded):

      * UNMEASURED  -> expected but absent-and-not-measured, OR errored.
                       HOLD ceiling, FAIL (never lower off a broken scan).
      * UNSEEDED    -> measured but no committed ceiling yet. Either seed
                       (``seed_missing``) or FAIL asking for a SEED PR.
      * NEW-CODE    -> measured, new_code > 0 -> FAIL (clean-as-you-code).
      * REGRESSION  -> measured, current > ceiling -> FAIL.
      * PASS/LOWER  -> measured, current <= ceiling. If current < ceiling,
                       AUTO-LOWER (monotone) and mark changed.
    """
    result = GateResult()
    for provider in inputs.universe():
        verdict, changed = _classify_one(provider, inputs)
        if verdict is not None:
            result.verdicts.append(verdict)
        if changed:
            result.changed = True
    return result


# --------------------------------------------------------------------------- #
# rendering + IO
# --------------------------------------------------------------------------- #
def render_markdown(result: GateResult, repo_slug: str, head_sha: str) -> str:
    """Render the gate verdict as a markdown table."""
    overall = "FAIL" if result.failed else "PASS"
    lines = [
        "# Layer-1 Ratchet Gate",
        "",
        f"- Repo: `{repo_slug}`",
        f"- SHA: `{head_sha}`",
        f"- Verdict: **{overall}**",
        f"- Baseline changed (auto-lowered/seeded): `{result.changed}`",
        "",
        "| Provider | State | Current | Ceiling | New-code | Lowered to |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for v in sorted(result.verdicts, key=lambda x: x.provider):
        lines.append(
            f"| `{v.provider}` | `{v.state}` | "
            f"{'-' if v.current is None else v.current} | "
            f"{'-' if v.ceiling is None else v.ceiling} | "
            f"{v.new_code} | {'-' if v.lowered_to is None else v.lowered_to} |"
        )
    return "\n".join(lines) + "\n"


def write_baseline(path: Path, baseline: RatchetBaseline) -> None:
    """Persist ``ratchet.json`` deterministically (sorted keys, trailing nl)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(baseline.raw, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    """Construct the ratchet-gate CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Layer-1 monotonic ratchet gate.")
    parser.add_argument("--canonical-json", required=True,
                        help="Path to quality-rollup/canonical.json.")
    parser.add_argument("--ratchet-json", required=True,
                        help="Path to the consumer repo's .quality/ratchet.json.")
    parser.add_argument("--repo-dir", required=True,
                        help="Path to the checked-out consumer repo (with git history).")
    parser.add_argument("--profile-json", default="",
                        help="Resolved profile.json (for expected-providers derivation).")
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument("--head-sha", required=True,
                        help="Rollup SHA the findings' line numbers are anchored to.")
    parser.add_argument("--base-ref", default="",
                        help="Base branch ref (e.g. origin/main) for new-code diff.")
    parser.add_argument("--out-md", default="",
                        help="Optional markdown report path.")
    parser.add_argument(
        "--write", action="store_true",
        help="Persist auto-lowered/seeded baseline back to --ratchet-json.")
    parser.add_argument(
        "--seed", action="store_true",
        help=("SEED mode: create ceilings for measured-but-unseeded providers "
              "(human-reviewed PR only)."))
    parser.add_argument("--github-output", default="",
                        help="Optional path to write GITHUB_OUTPUT key/values.")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    return _build_arg_parser().parse_args(argv)


def _emit_github_output(path: str, result: GateResult) -> None:
    """Append ``changed``/``failed`` to a GITHUB_OUTPUT file when requested."""
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"changed={'true' if result.changed else 'false'}\n")
            handle.write(f"failed={'true' if result.failed else 'false'}\n")
    except OSError:
        pass


def _load_profile(profile_json: str) -> Dict[str, Any]:
    """Load the resolved profile.json, or an empty dict when absent."""
    if profile_json and Path(profile_json).is_file():
        return json.loads(Path(profile_json).read_text(encoding="utf-8"))
    return {}


def _build_classify_inputs(args: argparse.Namespace,
                           canonical: Mapping[str, Any],
                           added: Mapping[str, Set[int]]) -> ClassifyInputs:
    """Assemble the :class:`ClassifyInputs` for one gate run."""
    return ClassifyInputs(
        baseline=load_baseline(Path(args.ratchet_json), args.repo_slug,
                               args.head_sha),
        totals=read_provider_totals(canonical),
        new_code=count_new_code(canonical, added),
        measured=measured_providers_from_canonical(canonical),
        errored=providers_with_errors(canonical),
        expected=expected_providers_from_profile(_load_profile(args.profile_json)),
        head_sha=args.head_sha,
        seed_missing=args.seed,
    )


def _emit_reports(args: argparse.Namespace, result: GateResult,
                  inputs: ClassifyInputs) -> None:
    """Write the markdown report, persist the baseline, and emit CI outputs."""
    markdown = render_markdown(result, args.repo_slug, args.head_sha)
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)
    if args.write and (result.changed or args.seed):
        write_baseline(Path(args.ratchet_json), inputs.baseline)
    _emit_github_output(args.github_output, result)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ratchet gate."""
    args = parse_args(argv)
    canonical_path = Path(args.canonical_json)
    if not canonical_path.is_file():
        print(f"canonical.json not found: {canonical_path}", file=sys.stderr)
        return 2
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))

    repo_dir = Path(args.repo_dir)
    try:
        base_sha = _resolve_diff_base(repo_dir, args.base_ref, args.head_sha)
        added = added_line_ranges(repo_dir, base_sha, args.head_sha)
    except RatchetError as exc:
        # Fail loud: a broken diff must not silently disable new-code detection.
        print(f"::error::ratchet new-code diff failed: {exc}", file=sys.stderr)
        return 2

    inputs = _build_classify_inputs(args, canonical, added)
    result = classify(inputs)
    _emit_reports(args, result, inputs)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
