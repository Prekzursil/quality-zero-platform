#!/usr/bin/env python3
"""Date/SLA-based alert-trigger detectors.

Split from :mod:`scripts.quality.alert_triggers` so the time-window
detectors (deadline, escalation, bypass-stale, drift-stuck) and their shared
date helpers live in a cohesive module, keeping each module's file-level
complexity bounded. The detectors are re-exported from ``alert_triggers`` to
preserve the historical import surface.
"""

from __future__ import absolute_import

import datetime as dt
from typing import Any, List, Mapping

from scripts.quality import alerts
from scripts.quality.alert_triggers_base import AlertTrigger

BYPASS_STALE_THRESHOLD_DAYS = 7
DRIFT_STUCK_THRESHOLD_DAYS = 3


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
