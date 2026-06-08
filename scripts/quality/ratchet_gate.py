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
import re
import subprocess  # nosec B404 -- only invoked with a fixed git argv, no shell
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

RATCHET_SCHEMA_VERSION = "qzp-ratchet/1"


class RatchetError(RuntimeError):
    """Raised when the gate cannot make a safe assertion (e.g. the new-code
    diff command failed). Fail-loud: a silently-empty diff would disable the
    clean-as-you-code check and let a 'fix 1 MINOR, add 1 BLOCKER' swap pass.
    """


# Exact provider literal strings emitted by the rollup_v2 normalizers
# (BaseNormalizer.provider on each normalizer; verified against
# scripts/quality/rollup_v2/normalizers/*.py). These are the ONLY valid
# keys under ratchet.json.providers.<name>.
KNOWN_PROVIDERS: Tuple[str, ...] = (
    "Applitools",
    "Chromatic",
    "Codacy",
    "CodeQL",
    "Coverage",
    "DeepScan",
    "DeepSource",
    "Dependabot",
    "QLTY",
    "QualitySecrets",
    "Semgrep",
    "Sentry",
    "SonarCloud",
)

# Maps a required-context substring -> the canonical provider literal.
# Used to derive the "expected providers" set from a resolved profile's
# required_contexts so an absent provider is recognised as UNMEASURED
# (block-lower) rather than a genuine zero.
CONTEXT_PROVIDER_HINTS: Tuple[Tuple[str, str], ...] = (
    ("Sonar", "SonarCloud"),
    ("SonarCloud", "SonarCloud"),
    ("Codacy", "Codacy"),
    ("CodeQL", "CodeQL"),
    ("DeepScan", "DeepScan"),
    ("DeepSource", "DeepSource"),
    ("Semgrep", "Semgrep"),
    ("Sentry", "Sentry"),
    ("Dependency", "Dependabot"),
    ("Dependabot", "Dependabot"),
    ("qlty", "QLTY"),
    ("QLTY", "QLTY"),
    ("Coverage", "Coverage"),
    ("Chromatic", "Chromatic"),
    ("Applitools", "Applitools"),
)


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(UTC).isoformat()


def today() -> str:
    """Return today's date (UTC) as ``YYYY-MM-DD``."""
    return datetime.now(UTC).date().isoformat()


# --------------------------------------------------------------------------- #
# git new-code (diff-scoped) detection
# --------------------------------------------------------------------------- #
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")


def _run_git(args: Sequence[str], repo_dir: Path) -> str:
    """Run ``git`` in ``repo_dir`` and return stdout.

    Raises :class:`RatchetError` when git itself fails (non-zero exit or
    missing binary). Callers that want "no diff" semantics must check for an
    empty *base* up front -- a failed git command is NEVER silently treated
    as an empty diff (that would disable new-code detection).
    """
    try:
        completed = subprocess.run(  # nosec B603 -- fixed argv, shell=False
            ["git", *args],
            cwd=str(repo_dir),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RatchetError(
            f"git {' '.join(args)} failed (exit {exc.returncode}): {(exc.stderr or '').strip()}"
        ) from exc
    except OSError as exc:
        raise RatchetError(f"could not run git: {exc}") from exc
    return completed.stdout


def _resolve_diff_base(repo_dir: Path, base_ref: str, head_sha: str) -> str:
    """Resolve the merge-base of ``base_ref`` and ``head_sha``.

    Raises :class:`RatchetError` if the merge-base cannot be computed (e.g.
    the head SHA is not present in this checkout -- the classic PR
    merge-commit-vs-head-ref mismatch). Returns "" only when ``base_ref`` is
    empty (caller explicitly opted out of new-code detection).
    """
    if not base_ref:
        return ""
    merge_base = _run_git(["merge-base", base_ref, head_sha or "HEAD"],
                          repo_dir).strip()
    if not merge_base:
        raise RatchetError(
            f"empty merge-base for {base_ref}..{head_sha or 'HEAD'} -- the "
            f"checked-out repo at {repo_dir} likely does not contain head SHA "
            f"{head_sha!r}. Ensure checkout ref == --head-sha and fetch-depth: 0."
        )
    return merge_base


def added_line_ranges(repo_dir: Path, base_sha: str,
                      head_sha: str) -> Dict[str, Set[int]]:
    """Return ``{file_path: {added_line_numbers}}`` for ``base..head``.

    Uses ``git diff --unified=0`` so only genuinely added/changed lines are
    captured. The line numbers are HEAD-side (matching the finding line
    numbers, which are anchored to the rollup SHA == head). Raises
    :class:`RatchetError` if the diff command fails (never returns an empty
    dict to mask a broken diff).
    """
    if not base_sha:
        return {}
    diff = _run_git(
        [
            "diff", "--unified=0", "--no-color",
            f"{base_sha}..{head_sha or 'HEAD'}"
        ],
        repo_dir,
    )
    result: Dict[str, Set[int]] = {}
    current_file = ""
    for raw in diff.splitlines():
        file_match = _DIFF_FILE_RE.match(raw)
        if file_match:
            current_file = file_match.group(1)
            result.setdefault(current_file, set())
            continue
        hunk = _HUNK_RE.match(raw)
        if hunk and current_file:
            start = int(hunk.group(1))
            count = int(hunk.group(2)) if hunk.group(2) is not None else 1
            # count == 0 means pure deletion at this point -> no added lines.
            for offset in range(count):
                result[current_file].add(start + offset)
    return result


def _normalize_path(path: str) -> str:
    """Normalize a finding/diff path for comparison (strip leading ./, slashes)."""
    return path.replace("\\", "/").lstrip("./").strip()


def is_new_code_finding(finding: Mapping[str, Any],
                        added: Mapping[str, Set[int]]) -> bool:
    """Return True when a finding lands on an added line in the diff.

    Repo-level / manifest-level / non-positional findings (line <= 0, empty
    file, or Dependabot's synthetic line==1 manifest pointer) are NOT treated
    as new-code: they have no meaningful (file, line) anchor in the diff and
    are governed by the per-provider total ceiling only.
    """
    try:
        line = int(finding.get("line") or 0)
    except (TypeError, ValueError):
        return False
    file_path = _normalize_path(str(finding.get("file") or ""))
    if line <= 0 or not file_path:
        return False
    added_lines = added.get(file_path)
    if added_lines is not None:
        return line in added_lines
    # Exact miss: try a PATH-COMPONENT-boundary suffix match (rollup vs diff
    # path prefix differences), e.g. "app/api.py" vs "apps/api/app/api.py".
    # A plain str.endswith would false-match "api.py" against ".../myapi.py";
    # requiring a leading "/" on the suffix prevents that.
    for diff_file, lines in added.items():
        if _suffix_path_match(diff_file, file_path):
            return line in lines
    return False


def _suffix_path_match(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` share a path-component-aligned suffix."""
    if a == b:
        return True
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    return longer.endswith("/" + shorter)


# --------------------------------------------------------------------------- #
# canonical.json reading
# --------------------------------------------------------------------------- #
def read_provider_totals(canonical: Mapping[str, Any]) -> Dict[str, int]:
    """Return ``{provider: total}`` from ``provider_summaries``.

    ``total`` is corroborator-counted (a multi-provider deduped finding
    increments each of its providers), which is exactly what we want for a
    per-provider ceiling that mirrors each dashboard. We deliberately do NOT
    gate on ``sum(totals)`` -- it is not equal to ``total_findings``.
    """
    totals: Dict[str, int] = {}
    for summary in canonical.get("provider_summaries", []):
        provider = str(summary.get("provider", ""))
        if summary.get("status") == "not-configured":
            continue
        if provider:
            totals[provider] = int(summary.get("total", 0))
    return totals


def measured_providers_from_canonical(
        canonical: Mapping[str, Any]) -> Set[str]:
    """Providers that produced at least one corroborator this run.

    Presence in ``provider_summaries`` (with status != not-configured) means
    the lane ran and emitted findings. A provider that ran clean (0 findings)
    will NOT appear here -- that ambiguity is resolved by the
    ``expected_providers`` set + ``normalizer_errors`` (see ``classify``).
    """
    measured: Set[str] = set()
    for summary in canonical.get("provider_summaries", []):
        if summary.get("status") == "not-configured":
            continue
        provider = str(summary.get("provider", ""))
        if provider:
            measured.add(provider)
    return measured


def providers_with_errors(canonical: Mapping[str, Any]) -> Set[str]:
    """Providers that recorded a normalizer error this run (treat as UNMEASURED)."""
    errored: Set[str] = set()
    for err in canonical.get("normalizer_errors", []):
        provider = str(err.get("provider", "")).strip()
        # normalizer_errors may carry the lowercase lane key; map both forms.
        for known in KNOWN_PROVIDERS:
            if provider and (provider == known
                             or provider.lower() == known.lower()):
                errored.add(known)
    return errored


def expected_providers_from_profile(profile: Mapping[str, Any]) -> Set[str]:
    """Derive the providers a healthy run is EXPECTED to measure.

    Pulled from the resolved profile's ``required_contexts`` (always/target
    lanes). Any provider in this set that is absent from canonical's
    ``provider_summaries`` and not in ``normalizer_errors`` is UNMEASURED ->
    the gate holds the ceiling and fails-closed rather than lowering to 0.
    """
    expected: Set[str] = set()
    contexts: List[str] = []
    raw = profile.get("required_contexts", {})
    if isinstance(raw, Mapping):
        for bucket in ("always", "target", "pull_request_only"):
            value = raw.get(bucket, [])
            if isinstance(value, list):
                contexts.extend(str(item) for item in value)
    elif isinstance(raw, list):
        contexts.extend(str(item) for item in raw)
    contexts.extend(
        str(item) for item in profile.get("active_required_contexts", []))
    for context in contexts:
        for needle, provider in CONTEXT_PROVIDER_HINTS:
            if needle in context:
                expected.add(provider)
    return expected


def count_new_code(canonical: Mapping[str, Any],
                   added: Mapping[str, Set[int]]) -> Dict[str, int]:
    """Return ``{provider: new_code_finding_count}`` over all canonical findings."""
    new_by_provider: Dict[str, int] = {p: 0 for p in KNOWN_PROVIDERS}
    for finding in canonical.get("findings", []):
        if not is_new_code_finding(finding, added):
            continue
        for corroborator in finding.get("corroborators", []):
            provider = str(corroborator.get("provider", ""))
            if provider in new_by_provider:
                new_by_provider[provider] += 1
    return new_by_provider


# --------------------------------------------------------------------------- #
# ratchet.json (baseline) model
# --------------------------------------------------------------------------- #
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
        return int(value) if isinstance(value, (int, float)) else None

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
        self._log("auto-lower", provider, new_ceiling, head_sha)

    def seed(self, provider: str, ceiling: int, head_sha: str,
             actor: str) -> None:
        """Seed/raise ``provider``'s ceiling (human-reviewed; logged)."""
        entry = self.providers.setdefault(provider, {})
        old = entry.get("ceiling")
        entry["ceiling"] = int(ceiling)
        entry["measured"] = True
        entry["seeded_at"] = utc_now()
        entry["seeded_by"] = actor
        action = "raise" if isinstance(old,
                                       (int,
                                        float)) and ceiling > old else "seed"
        self._log(action, provider, ceiling, head_sha, actor=actor)

    def _log(
        self,
        action: str,
        provider: str,
        ceiling: int,
        head_sha: str,
        actor: str = "ci-bot",
    ) -> None:
        """Append one audit record."""
        log = self.raw.setdefault("audit_log", [])
        log.append({
            "action": action,
            "provider": provider,
            "ceiling": int(ceiling),
            "sha": head_sha,
            "actor": actor,
            "at": utc_now(),
        })


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
        if seed_missing:
            baseline.seed(provider, current_val, head_sha, actor="seed-pr")
            return (
                ProviderVerdict(
                    provider,
                    "pass",
                    current_val,
                    current_val,
                    new_code,
                    lowered_to=current_val,
                ),
                True,
            )
        return ProviderVerdict(provider, "unseeded", current, None,
                               new_code), False

    # Clean-as-you-code: any new-code finding fails regardless of ceiling.
    if new_code > 0:
        return (
            ProviderVerdict(provider, "new-code", current_val, ceiling,
                            new_code),
            False,
        )

    if current_val > ceiling:
        return (
            ProviderVerdict(provider, "regression", current_val, ceiling,
                            new_code),
            False,
        )

    # current_val <= ceiling -> pass; auto-lower if strictly below.
    if current_val < ceiling:
        baseline.lower(provider, current_val, head_sha)
        return (
            ProviderVerdict(
                provider,
                "pass",
                current_val,
                ceiling,
                new_code,
                lowered_to=current_val,
            ),
            True,
        )
    return ProviderVerdict(provider, "pass", current_val, ceiling,
                           new_code), False


def classify(
    *,
    baseline: RatchetBaseline,
    totals: Mapping[str, int],
    new_code: Mapping[str, int],
    measured: Set[str],
    errored: Set[str],
    expected: Set[str],
    head_sha: str,
    seed_missing: bool,
) -> GateResult:
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
    universe = set(totals) | measured | expected | set(baseline.providers)
    for provider in sorted(universe):
        current = totals.get(provider)
        nc = int(new_code.get(provider, 0))
        ceiling = baseline.ceiling(provider)
        is_measured = provider in measured and provider not in errored

        if not is_measured:
            verdict = _classify_unmeasured(
                provider=provider,
                baseline=baseline,
                expected=expected,
                errored=errored,
                current=current,
                ceiling=ceiling,
                new_code=nc,
            )
            if verdict is not None:
                result.verdicts.append(verdict)
            continue

        verdict, changed = _classify_measured(
            provider=provider,
            baseline=baseline,
            current=current,
            ceiling=ceiling,
            new_code=nc,
            head_sha=head_sha,
            seed_missing=seed_missing,
        )
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Layer-1 monotonic ratchet gate.")
    parser.add_argument("--canonical-json",
                        required=True,
                        help="Path to quality-rollup/canonical.json.")
    parser.add_argument(
        "--ratchet-json",
        required=True,
        help="Path to the consumer repo's .quality/ratchet.json.",
    )
    parser.add_argument(
        "--repo-dir",
        required=True,
        help="Path to the checked-out consumer repo (with git history).",
    )
    parser.add_argument(
        "--profile-json",
        default="",
        help="Resolved profile.json (for expected-providers derivation).",
    )
    parser.add_argument("--repo-slug", required=True)
    parser.add_argument(
        "--head-sha",
        required=True,
        help="Rollup SHA the findings' line numbers are anchored to.",
    )
    parser.add_argument(
        "--base-ref",
        default="",
        help="Base branch ref (e.g. origin/main) for new-code diff.",
    )
    parser.add_argument("--out-md",
                        default="",
                        help="Optional markdown report path.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Persist auto-lowered/seeded baseline back to --ratchet-json.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help=
        "SEED mode: create ceilings for measured-but-unseeded providers (human-reviewed PR only).",
    )
    parser.add_argument(
        "--github-output",
        default="",
        help="Optional path to write GITHUB_OUTPUT key/values.",
    )
    return parser.parse_args(argv)


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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ratchet gate."""
    args = parse_args(argv)
    canonical_path = Path(args.canonical_json)
    if not canonical_path.is_file():
        print(f"canonical.json not found: {canonical_path}", file=sys.stderr)
        return 2
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))

    profile: Dict[str, Any] = {}
    if args.profile_json and Path(args.profile_json).is_file():
        profile = json.loads(
            Path(args.profile_json).read_text(encoding="utf-8"))

    repo_dir = Path(args.repo_dir)
    try:
        base_sha = _resolve_diff_base(repo_dir, args.base_ref, args.head_sha)
        added = added_line_ranges(repo_dir, base_sha, args.head_sha)
    except RatchetError as exc:
        # Fail loud: a broken diff must not silently disable new-code detection.
        print(f"::error::ratchet new-code diff failed: {exc}", file=sys.stderr)
        return 2

    totals = read_provider_totals(canonical)
    measured = measured_providers_from_canonical(canonical)
    errored = providers_with_errors(canonical)
    expected = expected_providers_from_profile(profile)
    new_code = count_new_code(canonical, added)

    baseline = load_baseline(Path(args.ratchet_json), args.repo_slug,
                             args.head_sha)
    result = classify(
        baseline=baseline,
        totals=totals,
        new_code=new_code,
        measured=measured,
        errored=errored,
        expected=expected,
        head_sha=args.head_sha,
        seed_missing=args.seed,
    )

    markdown = render_markdown(result, args.repo_slug, args.head_sha)
    if args.out_md:
        Path(args.out_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_md).write_text(markdown, encoding="utf-8")
    sys.stdout.write(markdown)

    if args.write and (result.changed or args.seed):
        write_baseline(Path(args.ratchet_json), baseline)

    _emit_github_output(args.github_output, result)
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
