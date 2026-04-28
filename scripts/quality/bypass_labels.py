#!/usr/bin/env python3
"""Handle ``quality-zero:break-glass`` + ``quality-zero:skip`` label events.

Phase 4 of ``docs/QZP-V2-DESIGN.md`` §5.2. Both labels let a PR
merge past a red quality-zero gate, but with different audit trails:

* ``quality-zero:break-glass`` — requires an ``Incident: <id>`` line in
  the PR body. The incident id (plus the PR slug and actor) are
  appended to ``audit/break-glass.jsonl`` AND a post-merge tracking
  issue is opened in the platform repo so the incident gets debriefed.
* ``quality-zero:skip`` — discretionary bypass, no incident required.
  Appended to ``audit/skip.jsonl`` only; the weekly digest flags
  frequent users so we don't normalise skipping the gates.

The handler NEVER logs secret values; only PR metadata (number, head
SHA, actor, body-excerpt) lands in the audit log. Each jsonl record
is one compact line so ``jq`` can stream it.
"""

from __future__ import absolute_import

import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Optional


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


BREAK_GLASS_LABEL = "quality-zero:break-glass"
SKIP_LABEL = "quality-zero:skip"

# Match ``Incident: <id>`` on its own line, case-insensitive.
# The id grammar is intentionally permissive (letters/digits/dashes/
# dots/underscores/slashes) so both ``INC-1234`` and
# ``pagerduty/INC-1234`` are accepted. Whitespace either side of the
# colon is tolerated.
_INCIDENT_RE = re.compile(
    # IGNORECASE → drop A-Z so the character class has no duplicate ranges.
    r"^\s*Incident\s*:\s*([a-z0-9_./-]+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class BypassError(ValueError):
    """Raised when a break-glass label is applied without a valid Incident id."""


@dataclass(frozen=True)
class BypassDecision:
    """The verdict the handler returns for a label event.

    ``audit_record`` is the structured dict that should be appended to
    the jsonl audit log (empty for ``allowed=False`` — when the bypass
    is rejected nothing is logged because the merge didn't happen).
    """

    label: str
    allowed: bool
    incident: Optional[str]
    audit_record: Optional[Mapping[str, Any]]
    tracking_issue_title: Optional[str]
    tracking_issue_body: Optional[str]


def extract_incident_id(pr_body: str) -> Optional[str]:
    """Return the first ``Incident: <id>`` occurrence in ``pr_body``, or ``None``."""
    if not isinstance(pr_body, str):
        return None
    match = _INCIDENT_RE.search(pr_body)
    if not match:
        return None
    return match.group(1).strip()


def _utc_iso_now() -> str:
    """Current UTC timestamp in ISO-8601 with ``Z`` suffix."""
    return (
        datetime.now(UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _build_audit_record(
    label: str,
    *,
    pr_slug: str,
    pr_number: int,
    head_sha: str,
    actor: str,
    incident: Optional[str],
) -> Mapping[str, Any]:
    """Build the jsonl-safe audit record. No secret values leak here."""
    record: dict = {
        "timestamp_utc": _utc_iso_now(),
        "label": label,
        "pr": {
            "slug": pr_slug,
            "number": pr_number,
            "head_sha": head_sha,
        },
        "actor": actor,
    }
    if incident:
        record["incident"] = incident
    return record


def _build_tracking_issue(
    pr_slug: str, pr_number: int, actor: str, incident: str,
) -> tuple:
    """Return ``(title, body)`` for the post-merge tracking issue."""
    title = f"break-glass follow-up: {pr_slug}#{pr_number} (Incident: {incident})"
    body = (
        f"Automated tracking issue from the `{BREAK_GLASS_LABEL}` handler.\n\n"
        f"- PR: {pr_slug}#{pr_number}\n"
        f"- Actor: @{actor}\n"
        f"- Incident: {incident}\n\n"
        "Post-merge remediation required: the PR merged past a red\n"
        "quality-zero gate using break-glass. Debrief the incident,\n"
        "resolve the underlying failure, and close this issue when the\n"
        "gate is green again on main.\n"
    )
    return title, body


def evaluate_break_glass(
    pr_body: str,
    pr_slug: str,
    pr_number: int,
    head_sha: str,
    actor: str,
) -> BypassDecision:
    """Decide whether a ``quality-zero:break-glass`` label can proceed."""
    incident = extract_incident_id(pr_body)
    if not incident:
        raise BypassError(
            "``quality-zero:break-glass`` requires an ``Incident: <id>`` "
            "line in the PR body (e.g. ``Incident: INC-1234``)."
        )
    record = _build_audit_record(
        BREAK_GLASS_LABEL,
        pr_slug=pr_slug,
        pr_number=pr_number,
        head_sha=head_sha,
        actor=actor,
        incident=incident,
    )
    title, body = _build_tracking_issue(pr_slug, pr_number, actor, incident)
    return BypassDecision(
        label=BREAK_GLASS_LABEL,
        allowed=True,
        incident=incident,
        audit_record=record,
        tracking_issue_title=title,
        tracking_issue_body=body,
    )


def evaluate_skip(
    pr_body: str,
    pr_slug: str,
    pr_number: int,
    head_sha: str,
    actor: str,
) -> BypassDecision:
    """Decide whether a ``quality-zero:skip`` label can proceed.

    Always allows (the label itself is the authorisation); the caller
    is expected to restrict WHO can add the label via repo settings.
    ``pr_body`` is accepted for signature symmetry with
    :func:`evaluate_break_glass` but isn't inspected — skip has no
    incident requirement.
    """
    _ = pr_body  # unused; see docstring
    record = _build_audit_record(
        SKIP_LABEL,
        pr_slug=pr_slug,
        pr_number=pr_number,
        head_sha=head_sha,
        actor=actor,
        incident=None,
    )
    return BypassDecision(
        label=SKIP_LABEL,
        allowed=True,
        incident=None,
        audit_record=record,
        tracking_issue_title=None,
        tracking_issue_body=None,
    )


def append_jsonl(audit_path: Path, record: Mapping[str, Any]) -> None:
    """Append ``record`` to ``audit_path`` as a single compact JSON line.

    Creates the parent directory if necessary. Uses ``sort_keys=True``
    so the stored records are deterministic — important for diffable
    audit logs.
    """
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _run_cli() -> None:  # pragma: no cover — ad-hoc CLI
    """Dispatch one bypass-label decision from argv + stdin to stdout.

    Wrapped in a function (rather than inlined under ``if __name__ ==
    "__main__":``) so all locals stay local-scoped — at module level
    pylint treats every name as a constant and flags ``filename`` as a
    C0103 invalid-name finding for not being UPPER_CASE.
    """
    if len(sys.argv) < 6:
        print(
            "usage: bypass_labels.py <label> <pr_slug> <pr_number> "
            "<head_sha> <actor> <audit_dir>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    label, slug, pr_num, sha, actor, audit_dir = sys.argv[1:7]
    body = sys.stdin.read()
    if label == BREAK_GLASS_LABEL:
        decision = evaluate_break_glass(body, slug, int(pr_num), sha, actor)
        filename = "break-glass.jsonl"
    elif label == SKIP_LABEL:
        decision = evaluate_skip(body, slug, int(pr_num), sha, actor)
        filename = "skip.jsonl"
    else:
        print(f"unknown label: {label}", file=sys.stderr)
        raise SystemExit(2)
    if decision.audit_record is not None:
        append_jsonl(Path(audit_dir) / filename, decision.audit_record)
    print(json.dumps({
        "label": decision.label,
        "allowed": decision.allowed,
        "incident": decision.incident,
        "tracking_issue_title": decision.tracking_issue_title,
    }))


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    _run_cli()
