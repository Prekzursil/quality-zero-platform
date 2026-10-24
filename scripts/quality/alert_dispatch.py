#!/usr/bin/env python3
"""Phase 5 alert-dispatch glue — runs every detector over fleet state.

``dispatch_detected_triggers`` is the thin adapter between the pure
detectors in :mod:`alert_triggers` and the gh-backed opener in
:mod:`alerts`. Each :class:`~alert_triggers.AlertTrigger` becomes one
(or zero) open alert issue, deduped by title.

``build_triggers_from_fleet_state`` composes every detector against
an aggregated ``fleet_state`` dict (profiles + bypass issues + drift
PRs + staging wave results). Callers populate ``fleet_state`` from
whatever source of truth is cheapest (gh API, state-file JSON,
scheduled workflow).

Both functions are IO-free: the actual gh calls happen in ``alerts``
which the caller injects via ``opener``. That keeps the module
unit-testable without network / filesystem.
"""

from __future__ import absolute_import

import datetime as dt
import json
import subprocess  # nosec B404 # noqa: S404 — gh CLI wrapper; args are controlled
import sys
from pathlib import Path
from typing import Any, Callable, Collection, Dict, Iterable, List, Mapping, Optional, Sequence

from scripts.quality import alert_triggers as at
from scripts.quality import alerts


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


Opener = Callable[..., Mapping[str, Any]]


def dispatch_detected_triggers(
    *,
    platform_slug: str,
    triggers: Iterable[at.AlertTrigger],
    opener: Opener = alerts.open_alert_issue,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Open one issue per trigger; return the list of opener results."""
    results: List[Dict[str, Any]] = []
    for trigger in triggers:
        if dry_run:
            results.append(
                {
                    "number": 0,
                    "title": alerts.alert_issue_title(
                        trigger.alert_type,
                        trigger.subject,
                    ),
                    "created": False,
                }
            )
            continue
        result = opener(
            platform_slug,
            alert_type=trigger.alert_type,
            subject=trigger.subject,
            body=trigger.body,
        )
        results.append(dict(result))
    return results


def _profile_triggers(
    profile_entry: Mapping[str, Any],
    *,
    today: dt.date,
) -> List[at.AlertTrigger]:
    """Run the per-profile detectors (regression, deadlines, flags)."""
    slug = str(profile_entry.get("slug", ""))
    profile = profile_entry.get("profile") or {}
    baseline = float(profile_entry.get("baseline_coverage", 0.0))
    current = float(profile_entry.get("current_coverage", 0.0))
    declared = list(profile_entry.get("declared_flags") or [])
    reported = list(profile_entry.get("reported_flags") or [])
    triggers: List[at.AlertTrigger] = []
    triggers.extend(
        at.detect_coverage_regression(
            slug=slug,
            baseline_percent=baseline,
            current_percent=current,
        )
    )
    triggers.extend(
        at.detect_deadline_missed(
            slug=slug,
            profile=profile,
            today=today,
        )
    )
    triggers.extend(
        at.detect_escalation(
            slug=slug,
            profile=profile,
            today=today,
        )
    )
    triggers.extend(
        at.detect_flag_missing(
            slug=slug,
            declared_flags=declared,
            reported_flags=reported,
        )
    )
    return triggers


def _bypass_issue_triggers(
    issues: Iterable[Mapping[str, Any]],
    *,
    now: dt.datetime,
) -> List[at.AlertTrigger]:
    """Run ``detect_bypass_stale`` over every tracking issue."""
    triggers: List[at.AlertTrigger] = []
    for issue in issues:
        triggers.extend(
            at.detect_bypass_stale(
                slug=str(issue.get("slug", "")),
                issue_number=int(issue.get("issue_number", 0)),
                opened_at=issue["opened_at"],
                now=now,
            )
        )
    return triggers


def _drift_pr_triggers(
    prs: Iterable[Mapping[str, Any]],
    *,
    now: dt.datetime,
) -> List[at.AlertTrigger]:
    """Run ``detect_drift_stuck`` over every open drift-sync PR."""
    triggers: List[at.AlertTrigger] = []
    for pr in prs:
        triggers.extend(
            at.detect_drift_stuck(
                slug=str(pr.get("slug", "")),
                pr_number=int(pr.get("pr_number", 0)),
                opened_at=pr["opened_at"],
                now=now,
            )
        )
    return triggers


#: Outcomes that neither fail nor pass a workflow: they never break a failure
#: streak and never count as a recovery.
NEUTRAL_CONCLUSIONS = frozenset({"cancelled", "skipped", "neutral", "action_required", "stale"})


def workflow_conclusions(runs: Iterable[Mapping[str, Any]]) -> Dict[str, List[str]]:
    """Group ``gh run list --json workflowName,status,conclusion,createdAt`` rows by workflow.

    Each list is newest first and holds only COMPLETED runs whose conclusion
    says something (success or a failure kind).
    """
    ordered = sorted(
        (run for run in runs if isinstance(run, Mapping)),
        key=lambda run: str(run.get("createdAt", "")),
        reverse=True,
    )
    grouped: Dict[str, List[str]] = {}
    for run in ordered:
        conclusion = str(run.get("conclusion") or "")
        if run.get("status") != "completed" or not conclusion or conclusion in NEUTRAL_CONCLUSIONS:
            continue
        grouped.setdefault(str(run.get("workflowName", "")), []).append(conclusion)
    return grouped


def _gh_list(args: Sequence[str], *, runner: Callable[..., "subprocess.CompletedProcess[str]"]) -> List[Any]:
    """Run one ``gh`` listing and return its JSON array; raise when ``gh`` fails."""
    completed = runner(["gh", *args], capture_output=True, text=True, check=False)  # nosec B603 — fixed argv
    if completed.returncode != 0:
        raise alerts.AlertCommandError(
            f"`gh {' '.join(args[:2])}` exited {completed.returncode}: {completed.stderr.strip()}"
        )
    payload = json.loads(completed.stdout or "[]")
    return payload if isinstance(payload, list) else []


def _decisive_runs(runs: Iterable[Mapping[str, Any]]) -> int:
    """How many of ``runs`` say something: completed with a success or a failure kind."""
    return sum(len(conclusions) for conclusions in workflow_conclusions(runs).values())


def _workflow_runs(
    platform_slug: str,
    workflow_id: str,
    *,
    branch: str,
    limit: int,
    runner: Callable[..., "subprocess.CompletedProcess[str]"],
) -> List[Mapping[str, Any]]:
    """The newest ``limit`` runs of one workflow on ``branch``; raise when ``gh`` fails."""
    listed = _gh_list(
        [
            "run",
            "list",
            "--repo",
            platform_slug,
            "--workflow",
            workflow_id,
            "--branch",
            branch,
            "--limit",
            str(limit),
            "--json",
            "workflowName,status,conclusion,createdAt",
        ],
        runner=runner,
    )
    return [run for run in listed if isinstance(run, Mapping)]


def fetch_active_workflows(
    platform_slug: str,
    *,
    runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> List[Mapping[str, Any]]:
    """The platform's ACTIVE workflows (id, name, state); raise when ``gh`` fails."""
    listed = _gh_list(
        ["workflow", "list", "--repo", platform_slug, "--json", "id,name,state", "--limit", "200"], runner=runner
    )
    return [workflow for workflow in listed if isinstance(workflow, Mapping) and workflow.get("state") == "active"]


def fetch_workflow_runs(
    platform_slug: str,
    *,
    branch: str = "main",
    per_workflow: int = 10,
    max_per_workflow: int = 200,
    decisive: int = at.WORKFLOW_FAILING_THRESHOLD_RUNS,
    workflows: Optional[Sequence[Mapping[str, Any]]] = None,
    runner: Callable[..., "subprocess.CompletedProcess[str]"] = subprocess.run,
) -> List[Mapping[str, Any]]:
    """Read every active workflow's newest default-branch runs through ``gh``; raise if it cannot.

    Per workflow, because ``gh run list --limit`` caps the COMBINED result: the
    hourly dashboard alone would push a weekly workflow's last three runs out
    of any shared window, so it could fail forever below the threshold.

    A full window holding fewer than ``decisive`` completed, non-neutral runs
    is read again at ``max_per_workflow``. Cancelled and skipped runs never
    break a streak, so a burst of them (in-progress cancellation) must not
    push the failure that completes one out of view either.
    """
    active = workflows if workflows is not None else fetch_active_workflows(platform_slug, runner=runner)
    runs: List[Mapping[str, Any]] = []
    for workflow in active:
        workflow_id = str(workflow.get("id"))
        listed = _workflow_runs(platform_slug, workflow_id, branch=branch, limit=per_workflow, runner=runner)
        if len(listed) >= per_workflow and _decisive_runs(listed) < decisive:
            listed = _workflow_runs(platform_slug, workflow_id, branch=branch, limit=max_per_workflow, runner=runner)
        # A renamed workflow's older runs still carry the OLD name. File every
        # run under the current one, so a rename never opens (and at once
        # closes) an alert for a name that no longer exists.
        name = str(workflow.get("name") or "")
        runs.extend({**run, "workflowName": name} if name else run for run in listed)
    return runs


def close_recovered_workflow_alerts(
    *,
    platform_slug: str,
    workflow_runs: Mapping[str, Sequence[str]],
    active_workflows: Optional[Collection[str]] = None,
    closer: Callable[..., Mapping[str, Any]] = alerts.close_alert_issue,
    lister: Callable[..., Sequence[Mapping[str, Any]]] = alerts.list_open_alert_issues,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Close the failing-workflow alerts that no longer describe a failing workflow.

    A workflow whose newest completed run succeeded has recovered. With
    ``active_workflows`` given, an open alert for a workflow that is no longer
    active (deleted, renamed or disabled) closes too: it can never produce the
    successful run that would otherwise close it.
    """
    results: List[Dict[str, Any]] = []
    if active_workflows is not None:
        prefix = alerts.alert_issue_title(alerts.AlertType.WORKFLOW_FAILING, "")
        for issue in lister(platform_slug, alert_type=alerts.AlertType.WORKFLOW_FAILING):
            title = str(issue.get("title", ""))
            subject = title[len(prefix) :]
            if not title.startswith(prefix) or subject in active_workflows:
                continue
            results.append(
                dict(
                    closer(
                        platform_slug,
                        alert_type=alerts.AlertType.WORKFLOW_FAILING,
                        subject=subject,
                        dry_run=dry_run,
                        close_comment=(
                            f"`{subject}` is no longer an active workflow (deleted, renamed or disabled); closing."
                        ),
                    )
                )
            )
    for workflow in sorted(workflow_runs):
        conclusions = list(workflow_runs[workflow])
        if not conclusions or conclusions[0] != "success":
            continue
        results.append(
            dict(
                closer(
                    platform_slug,
                    alert_type=alerts.AlertType.WORKFLOW_FAILING,
                    subject=workflow,
                    dry_run=dry_run,
                    close_comment=f"`{workflow}` succeeded on its newest completed run; closing.",
                )
            )
        )
    return results


def close_resolved_alerts(
    *,
    platform_slug: str,
    alert_type: alerts.AlertType,
    live_subjects: Collection[str],
    close_comment: str,
    unverifiable: Callable[[str], bool] = lambda _subject: False,
    closer: Callable[..., Mapping[str, Any]] = alerts.close_alert_issue,
    lister: Callable[..., Sequence[Mapping[str, Any]]] = alerts.list_open_alert_issues,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Close every open ``alert_type`` issue whose subject is no longer live.

    The bypass-stale and drift-stuck detectors only OPEN alerts. Once a
    follow-up issue closes or a drift PR merges, its subject simply leaves the
    live set, so without this the alert stayed open forever. A subject the
    live read could not see at all (``unverifiable``, e.g. a private member
    without the fleet-read token) is left alone rather than closed on missing
    evidence. ``close_comment`` may use ``{subject}``.
    """
    prefix = alerts.alert_issue_title(alert_type, "")
    results: List[Dict[str, Any]] = []
    for issue in lister(platform_slug, alert_type=alert_type):
        title = str(issue.get("title", ""))
        subject = title[len(prefix) :]
        if not title.startswith(prefix) or subject in live_subjects or unverifiable(subject):
            continue
        results.append(
            dict(
                closer(
                    platform_slug,
                    alert_type=alert_type,
                    subject=subject,
                    dry_run=dry_run,
                    close_comment=close_comment.format(subject=subject),
                )
            )
        )
    return results


def build_triggers_from_fleet_state(
    state: Mapping[str, Any],
) -> List[at.AlertTrigger]:
    """Run every detector over the aggregated fleet state."""
    today: dt.date = state.get("today", dt.date.today())
    now: dt.datetime = state.get("now", dt.datetime.now(dt.UTC))

    triggers: List[at.AlertTrigger] = []
    for profile_entry in state.get("profiles", []):
        triggers.extend(_profile_triggers(profile_entry, today=today))
    triggers.extend(
        _bypass_issue_triggers(
            state.get("bypass_issues", []),
            now=now,
        )
    )
    triggers.extend(
        _drift_pr_triggers(
            state.get("drift_prs", []),
            now=now,
        )
    )

    workflow_runs: Mapping[str, Sequence[str]] = state.get("workflow_runs", {})
    for workflow in sorted(workflow_runs):
        triggers.extend(at.detect_workflow_failing(workflow=workflow, conclusions=list(workflow_runs[workflow])))

    recipe_name = str(state.get("recipe_name", ""))
    staging = list(state.get("staging_results", []))
    if recipe_name and staging:
        triggers.extend(
            at.detect_fleet_bump_fail(
                recipe_name=recipe_name,
                staging_results=staging,
            )
        )
    return triggers


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import json

    print(
        json.dumps(
            {
                "module": "alert_dispatch",
                "detectors": 7,
                "public_api": [
                    "dispatch_detected_triggers",
                    "build_triggers_from_fleet_state",
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
