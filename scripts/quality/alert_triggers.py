#!/usr/bin/env python3
"""Phase 5 alert-trigger detectors.

Each detector here is the *synthetic trigger* for one of the 8 alert
types in ``docs/QZP-V2-DESIGN.md`` §8 (``repo-not-profiled`` already
ships in ``fleet_inventory.py``). A detector is a pure function over
its inputs that returns a list of :class:`AlertTrigger` records.

Each :class:`AlertTrigger` is a dataclass with ``alert_type``,
``subject`` (deduper for the issue title), and a pre-formatted
``body`` explaining the trigger in human terms.

The caller dispatches the triggers via ``scripts.quality.alerts``:

.. code-block:: python

    for trigger in detect_coverage_regression(...):
        alerts.open_alert_issue(
            platform_slug,
            alert_type=trigger.alert_type,
            subject=trigger.subject,
            body=trigger.body,
            runner=subprocess.run,
        )

The pure-function shape means every detector is unit-testable
without hitting GitHub or Codecov; the integration happens at the
call site.
"""

from __future__ import absolute_import

import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Sequence

from scripts.quality import alerts


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


COVERAGE_REGRESSION_THRESHOLD_POINTS = 0.5  # percentage-point drop
BYPASS_STALE_THRESHOLD_DAYS = 7
DRIFT_STUCK_THRESHOLD_DAYS = 3


@dataclass(frozen=True)
class AlertTrigger:
    """One alert-eligible event; callers hand this to ``alerts.open_alert_issue``."""

    alert_type: alerts.AlertType
    subject: str
    body: str


def detect_coverage_regression(
    *,
    slug: str,
    baseline_percent: float,
    current_percent: float,
) -> List[AlertTrigger]:
    """Fire when coverage drops by strictly more than 0.5 percentage points."""
    drop = baseline_percent - current_percent
    if drop <= COVERAGE_REGRESSION_THRESHOLD_POINTS:
        return []
    body = (
        f"Main-branch coverage dropped by **{drop:.2f}** percentage points "
        f"on `{slug}`: baseline was `{baseline_percent:.2f}%`, current is "
        f"`{current_percent:.2f}%`."
    )
    return [AlertTrigger(
        alert_type=alerts.AlertType.REGRESSION, subject=slug, body=body,
    )]


def _iso_date(value: Any) -> dt.date:
    """Parse ``YYYY-MM-DD`` or return a low sentinel so comparisons fail closed."""
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return dt.date.min


def _ratchet_block(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the ``mode.ratchet`` sub-block, defaulting to empty."""
    mode = profile.get("mode") if isinstance(profile, Mapping) else None
    if not isinstance(mode, Mapping):
        return {}
    ratchet = mode.get("ratchet")
    return ratchet if isinstance(ratchet, Mapping) else {}


def _phase(profile: Mapping[str, Any]) -> str:
    """Return ``mode.phase`` or empty string."""
    mode = profile.get("mode") if isinstance(profile, Mapping) else None
    if not isinstance(mode, Mapping):
        return ""
    return str(mode.get("phase", ""))


def detect_deadline_missed(
    *, slug: str, profile: Mapping[str, Any], today: dt.date,
) -> List[AlertTrigger]:
    """Fire when ``ratchet.target_date`` passed and phase is not absolute."""
    if _phase(profile) == "absolute":
        return []
    ratchet = _ratchet_block(profile)
    if "target_date" not in ratchet:
        return []
    target = _iso_date(ratchet["target_date"])
    if target >= today:
        return []
    body = (
        f"Ratchet target date **{target.isoformat()}** has passed without "
        f"`{slug}` reaching `mode.phase: absolute`. Current phase: "
        f"`{_phase(profile) or '<unset>'}`."
    )
    return [AlertTrigger(
        alert_type=alerts.AlertType.DEADLINE_MISSED, subject=slug, body=body,
    )]


def detect_escalation(
    *, slug: str, profile: Mapping[str, Any], today: dt.date,
) -> List[AlertTrigger]:
    """Fire when ``ratchet.escalation_date`` passed and phase is not absolute."""
    if _phase(profile) == "absolute":
        return []
    ratchet = _ratchet_block(profile)
    if "escalation_date" not in ratchet:
        return []
    escalation = _iso_date(ratchet["escalation_date"])
    if escalation >= today:
        return []
    body = (
        f"Ratchet escalation date **{escalation.isoformat()}** has passed. "
        f"`{slug}` must flip to `mode.phase: absolute` now."
    )
    return [AlertTrigger(
        alert_type=alerts.AlertType.ESCALATION, subject=slug, body=body,
    )]


def detect_bypass_stale(
    *,
    slug: str, issue_number: int,
    opened_at: dt.datetime, now: dt.datetime,
    threshold_days: int = BYPASS_STALE_THRESHOLD_DAYS,
) -> List[AlertTrigger]:
    """Fire when a break-glass tracking issue has been open > ``threshold_days``."""
    age = now - opened_at
    if age.days <= threshold_days:
        return []
    subject = f"{slug}#{issue_number}"
    body = (
        f"Break-glass tracking issue {subject} has been open for "
        f"**{age.days} days** — past the {threshold_days}-day remediation "
        f"SLA. Close the underlying quality regression and this issue."
    )
    return [AlertTrigger(
        alert_type=alerts.AlertType.BYPASS_STALE, subject=subject, body=body,
    )]


def detect_drift_stuck(
    *,
    slug: str, pr_number: int,
    opened_at: dt.datetime, now: dt.datetime,
    threshold_days: int = DRIFT_STUCK_THRESHOLD_DAYS,
) -> List[AlertTrigger]:
    """Fire when a drift-sync PR has been open > ``threshold_days``."""
    age = now - opened_at
    if age.days <= threshold_days:
        return []
    subject = f"{slug}#{pr_number}"
    body = (
        f"Drift-sync PR {subject} has been open for **{age.days} days** — "
        f"past the {threshold_days}-day sync SLA. Merge or close the PR to "
        f"keep the fleet aligned."
    )
    return [AlertTrigger(
        alert_type=alerts.AlertType.DRIFT_STUCK, subject=subject, body=body,
    )]


def detect_fleet_bump_fail(
    *,
    recipe_name: str,
    staging_results: Sequence[Mapping[str, Any]],
) -> List[AlertTrigger]:
    """Fire if any staging-wave result in ``staging_results`` failed CI."""
    failed = [
        str(entry.get("slug", "<unknown>"))
        for entry in staging_results
        if str(entry.get("conclusion", "")) != "success"
    ]
    if not failed:
        return []
    body = (
        f"Bump recipe **{recipe_name}** failed CI on the staging wave. "
        f"Failing staging repos: {', '.join(failed)}. Revert the recipe "
        f"commit and investigate before rolling out to the rest of the fleet."
    )
    return [AlertTrigger(
        alert_type=alerts.AlertType.FLEET_BUMP_FAIL,
        subject=recipe_name, body=body,
    )]


def detect_flag_missing(
    *, slug: str,
    declared_flags: Iterable[str],
    reported_flags: Iterable[str],
) -> List[AlertTrigger]:
    """One alert per declared flag without a matching Codecov report."""
    reported_set = {str(f).strip() for f in reported_flags if str(f).strip()}
    missing = [
        str(flag).strip()
        for flag in declared_flags
        if str(flag).strip() and str(flag).strip() not in reported_set
    ]
    triggers: List[AlertTrigger] = []
    for flag in missing:
        subject = f"{slug}:{flag}"
        body = (
            f"Codecov flag **{flag}** is declared in `{slug}`'s profile "
            f"coverage inputs but did not appear in the latest commit's "
            f"Codecov totals. Check the upload step of the `{flag}` lane."
        )
        triggers.append(AlertTrigger(
            alert_type=alerts.AlertType.FLAG_MISSING,
            subject=subject, body=body,
        ))
    return triggers


def detect_secret_missing(
    *, slug: str, missing_secrets: Iterable[str],
) -> List[AlertTrigger]:
    """One alert per severity:block scanner that lacks its configured secret."""
    missing = [str(s).strip() for s in missing_secrets if str(s).strip()]
    triggers: List[AlertTrigger] = []
    for secret in missing:
        subject = f"{slug}:{secret}"
        body = (
            f"Severity-block scanner on `{slug}` is missing its required "
            f"secret **{secret}**. Add the secret to the repo's Actions "
            f"secrets (or the org-level secret for shared scanners). Until "
            f"the secret lands, the corresponding quality lane will fail."
        )
        triggers.append(AlertTrigger(
            alert_type=alerts.AlertType.SECRET_MISSING,
            subject=subject, body=body,
        ))
    return triggers


if __name__ == "__main__":  # pragma: no cover — no ad-hoc CLI yet
    import json
    print(json.dumps({"detectors": [
        "coverage_regression", "deadline_missed", "escalation",
        "bypass_stale", "drift_stuck", "fleet_bump_fail", "flag_missing",
        "secret_missing",
    ]}))
