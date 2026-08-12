#!/usr/bin/env python3
"""Standing dependency-CVE surface — the scheduled fleet-wide osv-scanner sweep.

Every other lane in the lean gate answers a question about the CODE, so a
PR-diff gate is sufficient for it: no diff, no new finding. A dependency
advisory is different in kind. It is **time-varying** — the finding set changes
with the upstream feed while the tree stands still. Measured 2026-08-11 on
DevExtreme's unchanged ``go 1.25.11`` directive: the CI run of 2026-07-25
reported ONE Go stdlib advisory, and the identical scan with the identical
pinned binary reported TWO. The finding set grew 100% with zero code change.

Two consequences follow, and this module is the second one:

1. A per-PR gate must apply a severity floor or it is non-convergent by
   construction. That is gate 6's T0 tier
   (``scripts/quality/osv_severity_gate.py``, PR #286).
2. A per-PR gate **cannot fire without a diff**, so a NEW advisory published
   against UNCHANGED code stays invisible until somebody happens to touch that
   repo. PR #286 correctly demoted dev-only / unscored / unfixable findings out
   of the blocking tier — but nothing surfaced the demoted tier, so those
   findings went nowhere at all. This module is the standing surface that
   catches both classes.

Shape:

* The roster is read from ``inventory/repos.yml`` — never a hardcoded list, so
  enrolling a repo enrolls it here too.
* Each repo is shallow-cloned, scanned with the **same pinned osv-scanner
  binary gate 6 uses**, and the report is split by the **same classifier** gate
  6 uses. T0 is therefore a faithful "this would block a PR today" prediction,
  and T2 is the inventory nothing else was showing.
* Findings land in **ONE tracking issue per repo, updated in place**. An
  issue-per-run is a notification spammer that gets muted inside a week, which
  would defeat the entire purpose. The issue is closed when the findings clear.
* Exit codes are handled exactly as gate 6 handles them. ``0`` clean, ``1``
  findings, ``128`` nothing to scan, ``127``/``129``/``130`` a SCAN ERROR that
  is **not** a vulnerability verdict. **A scan error is never reported as a
  clean result and never closes a tracking issue** — an incomplete scan is not
  evidence of a clean tree.

This is observability, not a gate. It must never become a required status
check: its whole value is that it can go red without blocking anybody, which
is precisely what lets it report the T2 tier honestly.

CLI exit codes: ``0`` every repo scanned AND reconciled, ``1`` at least one
repo could not be scanned or its issue could not be reconciled, ``2`` the
roster itself could not be resolved.
"""

from __future__ import absolute_import

import argparse
import contextlib
import datetime as dt
import enum
import json
import subprocess  # nosec B404 — gh / osv-scanner CLI wrapper; every arg is controlled
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, List, Mapping, Optional, Sequence, Tuple

from scripts.quality import fleet_inventory
from scripts.quality import osv_severity_gate as severity

OSV_SCANNER_VERSION = "v2.3.8"
"""The pinned scanner. MUST match ``reusable-quality.yml``'s gate-6 download.

A T0 "would block" prediction computed by a different scanner version is not a
prediction of anything — advisory matching and severity plumbing both change
between releases. A contract test asserts the two pins are equal.
"""

WORKFLOW_NAME = "scheduled-cve-scan.yml"
OSV_CONFIG_NAME = "osv-scanner.toml"

ALERT_LABEL = "alert:cve-watch"
LABEL_COLOR = "B60205"
LABEL_DESCRIPTION = "Standing dependency-CVE surface (observability, never a gate)."

BODY_MARKER = "<!-- qzp:cve-watch -->"
"""Stable marker so a human (or a later tool) can recognise a body we own."""

DEFAULT_PLATFORM_SLUG = "Prekzursil/quality-zero-platform"
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 20

MAX_ROWS_PER_TIER = 40
MAX_BODY_CHARS = 60000
"""Body cap. GitHub rejects an issue body over 65536 characters, and a rejected
update is a silent gap in exactly the surface this module exists to provide."""

TRUNCATION_NOTICE = "\n\n_(truncated)_\n"

# osv-scanner exit codes, verbatim from cmd/osv-scanner/internal/cmd/run.go at
# the pinned tag. These are NOT a 0-vs-non-zero split, and conflating them is
# how a third-party outage becomes indistinguishable from a security regression.
OSV_CLEAN = 0
OSV_VULNS = 1
OSV_GENERIC_ERROR = 127
OSV_NO_PACKAGES = 128
OSV_API_FAILED = 129
OSV_INVALID_CONFIG = 130

RETRYABLE_EXITS = frozenset((OSV_GENERIC_ERROR, OSV_API_FAILED))
"""Transient upstream failures. ``130`` (invalid config) is deterministic, so
retrying it only burns runner minutes.

Measured against the real pinned 2.3.8 binary (2026-08-11): a *malformed* TOML
config surfaced as **127**, not 130 - so 130 alone cannot be relied on to catch
a bad config, and the retry costs one wasted round on that input. This is why
the whole ``{127, 129, 130}`` set maps to ``SCAN_ERROR`` rather than the code
branching on 130 specifically: the classification stays correct either way.
Whether 130 is reserved for a config that PARSES but is semantically invalid is
**UNVERIFIED** - settling experiment: feed a syntactically valid
``osv-scanner.toml`` containing an unknown key or a malformed ``IgnoredVulns``
entry and record the exit code.
"""

EXIT_OK = 0
EXIT_INCOMPLETE = 1
EXIT_CONFIG_ERROR = 2

ProcessRunner = Callable[..., "subprocess.CompletedProcess[str]"]
Sleeper = Callable[[float], None]


class FleetRosterError(RuntimeError):
    """The fleet roster could not be resolved.

    Raised instead of returning an empty roster: "no repositories" and "the
    inventory file is missing" must never be the same observable outcome.
    """


class GhCommandError(RuntimeError):
    """A ``gh`` invocation failed, so the tracking-issue state is UNKNOWN.

    Never downgraded to "no issue exists" — that would open a duplicate on
    every failed lookup, which is the spam mode this module is built to avoid.
    """


class ScanStatus(enum.Enum):
    """What the scan of one repository actually established."""

    CLEAN = "clean"
    FINDINGS = "findings"
    NOTHING_TO_SCAN = "nothing-to-scan"
    SCAN_ERROR = "scan-error"

    @property
    def is_clean_result(self) -> bool:
        """True only when the scan COMPLETED and had nothing to report.

        A ``SCAN_ERROR`` is deliberately excluded: the tree was not fully
        scanned, so it is not evidence of a clean tree and must not clear an
        issue.
        """
        return self in CLEAN_RESULT_STATUSES


CLEAN_RESULT_STATUSES = frozenset((ScanStatus.CLEAN, ScanStatus.NOTHING_TO_SCAN))


class IssueAction(enum.Enum):
    """What the sweep did to a repository's tracking issue."""

    CREATED = "created"
    UPDATED = "updated"
    CLOSED = "closed"
    NOOP = "noop"
    SKIPPED_SCAN_ERROR = "skipped-scan-error"
    FAILED = "failed"


INCOMPLETE_ACTIONS = frozenset((IssueAction.SKIPPED_SCAN_ERROR, IssueAction.FAILED))
"""Actions that mean the sweep did not fully establish the fleet's state."""


_EXIT_MEANING = {
    OSV_CLEAN: (
        ScanStatus.CLEAN,
        "osv-scanner completed and matched no advisory.",
    ),
    OSV_VULNS: (
        ScanStatus.FINDINGS,
        "osv-scanner matched at least one advisory (exit 1 is the ONLY vulnerability code).",
    ),
    OSV_NO_PACKAGES: (
        ScanStatus.NOTHING_TO_SCAN,
        "osv-scanner exited 128 (ErrNoPackagesFound): no dependency manifests or lockfiles to scan.",
    ),
    OSV_GENERIC_ERROR: (
        ScanStatus.SCAN_ERROR,
        "osv-scanner exited 127 (HasErrored): the scanner logged an error - commonly a "
        "deps.dev / registry resolver outage. The tree was NOT fully scanned.",
    ),
    OSV_API_FAILED: (
        ScanStatus.SCAN_ERROR,
        "osv-scanner exited 129 (ErrAPIFailed): the OSV.dev API was unreachable.",
    ),
    OSV_INVALID_CONFIG: (
        ScanStatus.SCAN_ERROR,
        f"osv-scanner exited 130 (HasErroredBecauseInvalidConfig): the {OSV_CONFIG_NAME} config was rejected.",
    ),
}


@dataclass(frozen=True)
class RepoScan:
    """The outcome of scanning one repository."""

    slug: str
    status: ScanStatus
    exit_code: int
    detail: str
    blocking: Tuple[severity.Finding, ...] = ()
    demoted: Tuple[severity.Finding, ...] = ()
    attempts: int = 1
    config_applied: bool = False


@dataclass(frozen=True)
class RepoOutcome:
    """The scan of one repository plus what happened to its tracking issue."""

    slug: str
    status: ScanStatus
    action: IssueAction
    issue_number: int
    detail: str
    dry_run: bool
    t0: int = 0
    t2: int = 0


# ---------------------------------------------------------------------------
# Exit-code contract (gate 6's, verbatim).
# ---------------------------------------------------------------------------


def classify_exit_code(code: int) -> Tuple[ScanStatus, str]:
    """Map an osv-scanner exit code onto a status plus a human explanation.

    An undocumented code fails CLOSED as a scan error rather than being read as
    a clean tree: auto-passing an exit nobody has characterised would fail open
    exactly where this surface exists to stay honest.
    """
    known = _EXIT_MEANING.get(int(code))
    if known is not None:
        return known
    return (
        ScanStatus.SCAN_ERROR,
        f"osv-scanner exited {code}, which is not a documented exit code for the pinned "
        f"{OSV_SCANNER_VERSION} (0/1/127/128/129/130) - treated as a scan failure, NOT a vulnerability finding.",
    )


def is_retryable_exit(code: int) -> bool:
    """True for the exits a transient upstream outage produces (127 / 129)."""
    return int(code) in RETRYABLE_EXITS


# ---------------------------------------------------------------------------
# Roster.
# ---------------------------------------------------------------------------


def load_fleet_slugs(inventory_path: Path, *, only: Sequence[str] = ()) -> List[str]:
    """Return the governed fleet from ``inventory/repos.yml``.

    ``only`` narrows the sweep for a manual dispatch and accepts either the
    full ``owner/name`` slug or the bare repository name. An ``only`` value
    that matches nothing raises rather than quietly sweeping zero repos, and a
    blank value (the shape an empty ``workflow_dispatch`` input arrives as) is
    ignored rather than treated as a filter.
    """
    try:
        slugs = fleet_inventory.load_inventory_slugs(Path(inventory_path))
    except OSError as exc:
        raise FleetRosterError(f"cannot read the fleet inventory '{inventory_path}': {exc}") from exc
    if not slugs:
        raise FleetRosterError(
            f"the fleet inventory '{inventory_path}' declares no repository slugs; "
            "refusing to report an empty sweep as a clean fleet",
        )
    wanted = [str(item).strip() for item in only if str(item).strip()]
    if not wanted:
        return slugs
    selected: List[str] = []
    for value in wanted:
        matches = [slug for slug in slugs if value in (slug, slug.rsplit("/", 1)[-1])]
        if not matches:
            raise FleetRosterError(f"--only {value!r} matches no repository in '{inventory_path}'")
        selected.extend(matches)
    return sorted(set(selected))


# ---------------------------------------------------------------------------
# Command construction + process plumbing.
# ---------------------------------------------------------------------------


def _run(args: Sequence[str], *, runner: ProcessRunner) -> "subprocess.CompletedProcess[str]":
    """Invoke ``args`` through the injected runner, capturing text output."""
    return runner(list(args), capture_output=True, text=True, check=False)


def _tail(text: str) -> str:
    """Return the last line of ``text``, or an empty string."""
    return " ".join(str(text).strip().splitlines()[-1:])


def clone_command(slug: str, dest: Path) -> List[str]:
    """Build the shallow single-branch clone for one fleet repository.

    A full clone of the whole fleet every night is wasted minutes and disk, and
    osv-scanner only ever reads the checked-out manifests.
    """
    return [
        "gh",
        "repo",
        "clone",
        slug,
        str(dest),
        "--",
        "--depth=1",
        "--single-branch",
        "--no-tags",
    ]


def osv_command(repo_dir: Path, *, config: Optional[Path] = None) -> List[str]:
    """Build the osv-scanner invocation, mirroring gate 6's scan shape.

    ``--config`` is passed only when the caller actually ships the file:
    osv-scanner rejects a missing config with exit 130, and gate 6 likewise
    only runs when the file exists. Passing it where present keeps T0 a
    faithful prediction of the gate's own verdict, since the caller's
    hand-written ignores apply first in both places.
    """
    args = ["osv-scanner", "scan", "source", "--recursive", "--format", "json"]
    if config is not None:
        args.append(f"--config={config}")
    args.append(str(repo_dir))
    return args


def _scan_with_retries(
    command: Sequence[str],
    *,
    runner: ProcessRunner,
    sleeper: Sleeper,
    max_attempts: int,
) -> Tuple["subprocess.CompletedProcess[str]", int]:
    """Run the scan, retrying ONLY the transient exits, with gate 6's backoff."""
    attempts_allowed = max(1, int(max_attempts))
    completed = _run(command, runner=runner)
    attempt = 1
    while attempt < attempts_allowed and is_retryable_exit(completed.returncode):
        sleeper(attempt * RETRY_BACKOFF_SECONDS)
        attempt += 1
        completed = _run(command, runner=runner)
    return completed, attempt


def scan_repo(
    slug: str,
    *,
    workdir: Path,
    floor: float,
    runner: ProcessRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> RepoScan:
    """Clone and scan one repository, splitting its report into T0 and T2.

    A repository that could not be cloned, or whose report could not be
    parsed, is a ``SCAN_ERROR``: we did not establish anything about it, so we
    must not claim it is clean.
    """
    dest = Path(workdir) / slug.replace("/", "__")
    clone = _run(clone_command(slug, dest), runner=runner)
    if clone.returncode != 0:
        return RepoScan(
            slug=slug,
            status=ScanStatus.SCAN_ERROR,
            exit_code=clone.returncode,
            detail=(
                f"`gh repo clone` exited {clone.returncode}, so the repository was never scanned - "
                f"this is NOT a vulnerability verdict. {_tail(clone.stderr)}"
            ),
        )

    config_path = dest / OSV_CONFIG_NAME
    config_applied = config_path.is_file()
    completed, attempts = _scan_with_retries(
        osv_command(dest, config=config_path if config_applied else None),
        runner=runner,
        sleeper=sleeper,
        max_attempts=max_attempts,
    )
    code = completed.returncode
    status, detail = classify_exit_code(code)
    blocking: Tuple[severity.Finding, ...] = ()
    demoted: Tuple[severity.Finding, ...] = ()
    if status is ScanStatus.FINDINGS:
        try:
            document = json.loads(completed.stdout)
        except ValueError as exc:
            status = ScanStatus.SCAN_ERROR
            detail = (
                f"osv-scanner exited 1 (findings) but its JSON report could not be parsed ({exc}), so the "
                "findings could not be classified - this is NOT a clean result."
            )
        else:
            found_blocking, found_demoted = severity.classify(severity.iter_findings(document), floor)
            blocking = tuple(found_blocking)
            demoted = tuple(found_demoted)
    return RepoScan(
        slug=slug,
        status=status,
        exit_code=code,
        detail=detail,
        blocking=blocking,
        demoted=demoted,
        attempts=attempts,
        config_applied=config_applied,
    )


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def issue_title(slug: str) -> str:
    """Canonical, dedupable tracking-issue title. This IS the dedupe key."""
    return f"[{ALERT_LABEL}] {slug}"


def _rows(findings: Sequence[severity.Finding], *, with_reasons: bool) -> List[str]:
    """Render one tier's rows, capped, with any remainder disclosed."""
    if not findings:
        return ["_none_"]
    rows: List[str] = []
    for finding in findings[:MAX_ROWS_PER_TIER]:
        prefix = "[" + "+".join(finding.reasons) + "] " if with_reasons else ""
        rows.append("- " + prefix + severity.format_finding(finding))
    remainder = len(findings) - MAX_ROWS_PER_TIER
    if remainder > 0:
        rows.append(f"- _... and {remainder} more (see the workflow run log for the full report)_")
    return rows


def _findings_section(scan: RepoScan) -> List[str]:
    """Render the two-tier body for a repository with findings."""
    section = [f"## T0 - WOULD BLOCK ({len(scan.blocking)})", ""]
    section.append("Production dependency, at or above the CVSS floor, and a published fix exists.")
    section.append("Gate 6 reds a PR on each of these today, so each is closable by a bump you control.")
    section.append("")
    section.extend(_rows(scan.blocking, with_reasons=False))
    section.extend(
        [
            "",
            f"## T2 - INVENTORY ({len(scan.demoted)})",
            "",
            "Demoted, not hidden: dev-only, unscored, below the floor, or no published fix.",
            "These never block a PR. This section is the only place they are visible.",
            "",
        ]
    )
    section.extend(_rows(scan.demoted, with_reasons=True))
    section.append("")
    section.append("This issue closes automatically on the first sweep that finds nothing.")
    return section


def _status_section(scan: RepoScan) -> List[str]:
    """Render the body section that corresponds to the scan's status."""
    if scan.status is ScanStatus.FINDINGS:
        return _findings_section(scan)
    if scan.status is ScanStatus.CLEAN:
        return [
            "## No dependency advisories",
            "",
            "The scan completed and matched nothing. This issue is being closed.",
        ]
    if scan.status is ScanStatus.NOTHING_TO_SCAN:
        return [
            "## Nothing to scan",
            "",
            "osv-scanner found no dependency manifests or lockfiles here, so there is no advisory "
            "surface to track. This issue is being closed.",
        ]
    return [
        "## SCAN ERROR - this is NOT a vulnerability verdict",
        "",
        scan.detail,
        "",
        "The dependency tree was not fully scanned, so the run is **not a clean result**. No tracking "
        "issue was opened, updated or closed on this evidence - a failed scan must never clear a finding.",
    ]


def render_issue_body(
    scan: RepoScan,
    *,
    floor: float,
    generated_at: dt.datetime,
    run_url: str = "",
    max_body_chars: int = MAX_BODY_CHARS,
) -> str:
    """Render the tracking-issue body (or close comment) for one scan.

    ``generated_at`` is injected rather than read from the clock so the output
    is deterministic and the body's timestamp is the sweep's, not the
    renderer's.
    """
    lines = [
        BODY_MARKER,
        f"# Dependency-CVE watch - `{scan.slug}`",
        "",
        "A dependency advisory is time-varying, so a PR-diff gate cannot see one published against",
        "unchanged code. This issue is that standing surface. **It is not a gate**: it blocks nothing,",
        f"it is updated in place by `{WORKFLOW_NAME}`, and it closes itself when the findings clear.",
        "",
        f"- Scanner: `osv-scanner {OSV_SCANNER_VERSION}` - the same pinned binary gate 6 runs",
        f"- Last scanned (UTC): `{generated_at.isoformat()}`",
        f"- Scan result: `{scan.status.value}` (osv-scanner exit `{scan.exit_code}`, {scan.attempts} attempt(s))",
        f"- Caller `{OSV_CONFIG_NAME}` ignores applied: `{'yes' if scan.config_applied else 'no'}`",
        f"- T0 floor: production dependency AND CVSS >= {format(floor, 'g')} AND a published fix",
    ]
    if run_url:
        lines.append(f"- Produced by: {run_url}")
    lines.append("")
    lines.extend(_status_section(scan))
    body = "\n".join(lines) + "\n"
    if len(body) <= max_body_chars:
        return body
    return body[: max_body_chars - len(TRUNCATION_NOTICE)] + TRUNCATION_NOTICE


def render_summary(outcomes: Sequence[RepoOutcome]) -> str:
    """Render the markdown block the workflow appends to its step summary."""
    lines = ["## Scheduled dependency-CVE sweep", ""]
    if not outcomes:
        lines.append("_No repositories were scanned._")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            f"`osv-scanner {OSV_SCANNER_VERSION}`. T0 = would block gate 6 today. T2 = inventory only, "
            "never blocking. A `scan-error` row is NOT a clean result.",
            "",
            "| repo | status | T0 | T2 | issue | detail |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for outcome in outcomes:
        reference = f"#{outcome.issue_number}" if outcome.issue_number else "-"
        detail = outcome.detail.replace("|", "/").replace("\n", " ") or "-"
        lines.append(
            f"| `{outcome.slug}` | {outcome.status.value} | {outcome.t0} | {outcome.t2} | "
            f"{outcome.action.value} {reference} | {detail} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Issue reconciliation.
# ---------------------------------------------------------------------------


def _require_ok(completed: "subprocess.CompletedProcess[str]", *, what: str) -> None:
    """Raise ``GhCommandError`` when a ``gh`` call did not succeed."""
    if completed.returncode != 0:
        raise GhCommandError(f"{what} exited {completed.returncode}: {_tail(completed.stderr)}")


def _issue_number(issue: Mapping[str, Any]) -> int:
    """Read the issue number out of a ``gh issue list`` record."""
    raw = issue.get("number")
    return int(raw) if isinstance(raw, int) else 0


def _issue_number_from_url(stdout: str) -> int:
    """Parse the trailing ``/<n>`` of ``gh issue create`` output (0 if absent)."""
    tail = str(stdout).rstrip().rsplit("/", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


def find_open_issue(
    platform_slug: str,
    *,
    slug: str,
    runner: ProcessRunner = subprocess.run,
) -> Optional[Mapping[str, Any]]:
    """Return the open tracking issue for ``slug``, or ``None``.

    A failed or unparseable lookup RAISES. Returning ``None`` there would read
    as "no issue exists" and open a duplicate on every transient gh failure.
    """
    completed = _run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            platform_slug,
            "--label",
            ALERT_LABEL,
            "--state",
            "open",
            "--search",
            slug,
            "--json",
            "number,title,state",
            "--limit",
            "100",
        ],
        runner=runner,
    )
    _require_ok(completed, what="`gh issue list`")
    try:
        payload = json.loads(completed.stdout or "[]")
    except ValueError as exc:
        raise GhCommandError(f"`gh issue list` returned unparseable JSON for {slug}: {exc}") from exc
    if not isinstance(payload, list):
        raise GhCommandError(f"`gh issue list` returned {type(payload).__name__}, expected a list, for {slug}")
    expected = issue_title(slug)
    for issue in payload:
        if isinstance(issue, Mapping) and issue.get("title") == expected:
            return issue
    return None


def ensure_label(platform_slug: str, *, runner: ProcessRunner = subprocess.run) -> bool:
    """Create or refresh the tracking label. A failure here is non-fatal.

    ``gh issue create --label`` fails outright on an unknown label, so the
    label is ensured first; but losing a finding because of a label race would
    be a worse outcome than an unlabelled issue.
    """
    completed = _run(
        [
            "gh",
            "label",
            "create",
            ALERT_LABEL,
            "--repo",
            platform_slug,
            "--color",
            LABEL_COLOR,
            "--description",
            LABEL_DESCRIPTION,
            "--force",
        ],
        runner=runner,
    )
    if completed.returncode != 0:
        print(
            f"WARN scheduled-cve-scan: could not ensure label {ALERT_LABEL} "
            f"(exit {completed.returncode}): {_tail(completed.stderr)}",
            file=sys.stderr,
        )
        return False
    return True


def reconcile(
    scan: RepoScan,
    *,
    platform_slug: str,
    floor: float,
    generated_at: dt.datetime,
    runner: ProcessRunner = subprocess.run,
    run_url: str = "",
    dry_run: bool = False,
) -> RepoOutcome:
    """Bring one repository's tracking issue in line with its scan.

    Findings create the issue or update it IN PLACE; a clean result closes it;
    a scan error touches nothing at all.
    """
    counts = (len(scan.blocking), len(scan.demoted))
    if scan.status is ScanStatus.SCAN_ERROR:
        return RepoOutcome(
            scan.slug,
            scan.status,
            IssueAction.SKIPPED_SCAN_ERROR,
            0,
            scan.detail,
            dry_run,
            *counts,
        )

    body = render_issue_body(scan, floor=floor, generated_at=generated_at, run_url=run_url)
    existing = find_open_issue(platform_slug, slug=scan.slug, runner=runner)

    if scan.status.is_clean_result:
        if existing is None:
            return RepoOutcome(scan.slug, scan.status, IssueAction.NOOP, 0, scan.detail, dry_run, *counts)
        number = _issue_number(existing)
        if not dry_run:
            _require_ok(
                _run(
                    ["gh", "issue", "close", str(number), "--repo", platform_slug, "--comment", body],
                    runner=runner,
                ),
                what="`gh issue close`",
            )
        return RepoOutcome(scan.slug, scan.status, IssueAction.CLOSED, number, scan.detail, dry_run, *counts)

    if existing is not None:
        number = _issue_number(existing)
        if not dry_run:
            _require_ok(
                _run(
                    ["gh", "issue", "edit", str(number), "--repo", platform_slug, "--body", body],
                    runner=runner,
                ),
                what="`gh issue edit`",
            )
        return RepoOutcome(scan.slug, scan.status, IssueAction.UPDATED, number, scan.detail, dry_run, *counts)

    if dry_run:
        return RepoOutcome(scan.slug, scan.status, IssueAction.CREATED, 0, scan.detail, dry_run, *counts)

    ensure_label(platform_slug, runner=runner)
    completed = _run(
        [
            "gh",
            "issue",
            "create",
            "--repo",
            platform_slug,
            "--title",
            issue_title(scan.slug),
            "--label",
            ALERT_LABEL,
            "--body",
            body,
        ],
        runner=runner,
    )
    _require_ok(completed, what="`gh issue create`")
    return RepoOutcome(
        scan.slug,
        scan.status,
        IssueAction.CREATED,
        _issue_number_from_url(completed.stdout),
        scan.detail,
        dry_run,
        *counts,
    )


def run_sweep(
    slugs: Sequence[str],
    *,
    workdir: Path,
    platform_slug: str,
    floor: float,
    generated_at: dt.datetime,
    runner: ProcessRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    run_url: str = "",
    dry_run: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> List[RepoOutcome]:
    """Scan and reconcile every slug. One repo's failure never aborts the rest."""
    outcomes: List[RepoOutcome] = []
    root = Path(workdir)
    for slug in slugs:
        scan = scan_repo(
            slug,
            workdir=root,
            floor=floor,
            runner=runner,
            sleeper=sleeper,
            max_attempts=max_attempts,
        )
        try:
            outcome = reconcile(
                scan,
                platform_slug=platform_slug,
                floor=floor,
                generated_at=generated_at,
                runner=runner,
                run_url=run_url,
                dry_run=dry_run,
            )
        except GhCommandError as exc:
            outcome = RepoOutcome(
                slug,
                scan.status,
                IssueAction.FAILED,
                0,
                str(exc),
                dry_run,
                len(scan.blocking),
                len(scan.demoted),
            )
        outcomes.append(outcome)
    return outcomes


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _resolve_workdir(value: str) -> Iterator[Path]:
    """Yield the requested clone root, or a self-cleaning temporary one."""
    if value:
        yield Path(value)
        return
    with tempfile.TemporaryDirectory(prefix="qzp-cve-scan-") as temp_dir:
        yield Path(temp_dir)


def _json_payload(outcomes: Sequence[RepoOutcome], *, generated_at: dt.datetime) -> Mapping[str, Any]:
    """Build the machine-readable sweep result."""
    return {
        "generated_at": generated_at.isoformat(),
        "osv_scanner_version": OSV_SCANNER_VERSION,
        "repos": [
            {
                "slug": outcome.slug,
                "status": outcome.status.value,
                "action": outcome.action.value,
                "issue": outcome.issue_number,
                "t0": outcome.t0,
                "t2": outcome.t2,
                "detail": outcome.detail,
                "dry_run": outcome.dry_run,
            }
            for outcome in outcomes
        ],
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(
        description=(
            "Scan the governed fleet for dependency advisories and maintain one auto-updated "
            "tracking issue per repository. Observability, not a gate."
        ),
    )
    parser.add_argument(
        "--inventory",
        default=str(Path(__file__).resolve().parents[2] / "inventory" / "repos.yml"),
        help="Path to the fleet roster (inventory/repos.yml).",
    )
    parser.add_argument("--platform-slug", default=DEFAULT_PLATFORM_SLUG, help="Repo that hosts the tracking issues.")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Scan only this repo (owner/name or bare name). Repeatable; blank values are ignored.",
    )
    parser.add_argument(
        "--min-severity",
        type=float,
        default=severity.SEVERITY_FLOOR,
        help=f"CVSS floor separating T0 from T2 (default {severity.SEVERITY_FLOOR}).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Scan attempts per repo before a transient exit is final (default {DEFAULT_MAX_ATTEMPTS}).",
    )
    parser.add_argument("--workdir", default="", help="Clone root. Default: a self-cleaning temporary directory.")
    parser.add_argument("--run-url", default="", help="Workflow-run URL recorded in each issue body as provenance.")
    parser.add_argument("--summary-file", default="", help="Append the markdown summary here (GITHUB_STEP_SUMMARY).")
    parser.add_argument("--dry-run", action="store_true", help="Compute everything; make no gh write call.")
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Emit the machine-readable payload instead of the markdown summary.",
    )
    return parser


def main(
    argv: Optional[List[str]] = None,
    *,
    runner: ProcessRunner = subprocess.run,
    sleeper: Sleeper = time.sleep,
    now: Optional[dt.datetime] = None,
) -> int:
    """Sweep the fleet and reconcile every tracking issue.

    Findings alone exit ``0``: this is observability, and turning a new upstream
    advisory into a red required check is precisely the treadmill the tiering
    was introduced to end. Only an INCOMPLETE sweep - a repo that could not be
    scanned, or an issue that could not be reconciled - exits non-zero.
    """
    args = _build_parser().parse_args(argv)
    try:
        slugs = load_fleet_slugs(Path(args.inventory), only=args.only)
    except FleetRosterError as exc:
        print(f"ERROR scheduled-cve-scan: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    generated_at = now if now is not None else dt.datetime.now(dt.UTC)
    with _resolve_workdir(args.workdir) as workdir:
        outcomes = run_sweep(
            slugs,
            workdir=workdir,
            platform_slug=args.platform_slug,
            floor=args.min_severity,
            generated_at=generated_at,
            runner=runner,
            sleeper=sleeper,
            run_url=args.run_url,
            dry_run=args.dry_run,
            max_attempts=args.max_attempts,
        )

    summary = render_summary(outcomes)
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as handle:
            handle.write(summary)
    if args.json_output:
        print(json.dumps(_json_payload(outcomes, generated_at=generated_at), indent=2, sort_keys=True))
    else:
        print(summary, end="")

    incomplete = [outcome for outcome in outcomes if outcome.action in INCOMPLETE_ACTIONS]
    if incomplete:
        print(
            f"INCOMPLETE scheduled-cve-scan: {len(incomplete)} of {len(outcomes)} repo(s) were not fully "
            "established (scan error or unreconciled issue). This is NOT a clean-fleet result.",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
