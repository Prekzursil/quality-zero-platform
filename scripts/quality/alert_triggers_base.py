#!/usr/bin/env python3
"""Shared dataclass for alert-trigger detectors.

Lives in its own module so the trigger detectors can be split across cohesive
files (``alert_triggers``, ``alert_triggers_sla``) without a circular import.
"""

from __future__ import absolute_import

from dataclasses import dataclass

from scripts.quality import alerts


@dataclass(frozen=True)
class AlertTrigger:
    """One alert-eligible event; callers hand this to ``alerts.open_alert_issue``."""

    alert_type: alerts.AlertType
    subject: str
    body: str
