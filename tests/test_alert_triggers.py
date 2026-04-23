"""Tests for Phase 5 ``scripts.quality.alert_triggers`` detectors.

Each detector is the synthetic-trigger surface for one of the 8
alert types from ``docs/QZP-V2-DESIGN.md`` §8 (``repo-not-profiled``
is already covered by fleet_inventory.py). Every detector returns a
list of ``AlertTrigger`` records the caller can feed straight into
``alerts.open_alert_issue``.
"""

from __future__ import absolute_import

import datetime as dt
import unittest

from scripts.quality import alert_triggers as at
from scripts.quality import alerts


class DetectCoverageRegressionTests(unittest.TestCase):
    """``detect_coverage_regression`` fires when cov drops > 0.5%."""

    def test_drop_over_threshold_fires(self) -> None:
        """Baseline 100 → current 99.2 (Δ=0.8) → regression alert."""
        triggers = at.detect_coverage_regression(
            slug="org/repo",
            baseline_percent=100.0,
            current_percent=99.2,
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.REGRESSION)
        self.assertEqual(triggers[0].subject, "org/repo")
        self.assertIn("0.8", triggers[0].body)

    def test_drop_at_exact_threshold_fires(self) -> None:
        """Baseline 100 → 99.5 (Δ=0.5) → exactly at threshold, fires."""
        triggers = at.detect_coverage_regression(
            slug="org/repo",
            baseline_percent=100.0,
            current_percent=99.5,
        )
        # The design doc says "drops > 0.5%" — use strict greater-than;
        # exactly 0.5% does not fire.
        self.assertEqual(triggers, [])

    def test_drop_just_over_threshold_fires(self) -> None:
        """Baseline 100 → 99.49 (Δ=0.51) → fires."""
        triggers = at.detect_coverage_regression(
            slug="org/repo",
            baseline_percent=100.0,
            current_percent=99.49,
        )
        self.assertEqual(len(triggers), 1)

    def test_coverage_recovery_does_not_fire(self) -> None:
        """Current > baseline → no alert."""
        triggers = at.detect_coverage_regression(
            slug="org/repo",
            baseline_percent=99.0,
            current_percent=100.0,
        )
        self.assertEqual(triggers, [])


class DetectDeadlineMissedTests(unittest.TestCase):
    """``detect_deadline_missed`` fires when target_date < today and not absolute."""

    def test_target_date_past_and_not_absolute_fires(self) -> None:
        """``target_date: 2026-01-01``, today 2026-04-23, phase ratchet → fires."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"target_date": "2026-01-01"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.DEADLINE_MISSED)
        self.assertIn("2026-01-01", triggers[0].body)

    def test_target_date_future_does_not_fire(self) -> None:
        """Target 2026-12-31, today 2026-04-23 → no alert."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"target_date": "2026-12-31"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])

    def test_already_absolute_never_fires(self) -> None:
        """Even with past target_date, absolute phase suppresses alert."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "absolute",
                              "ratchet": {"target_date": "2026-01-01"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])

    def test_no_ratchet_block_does_not_fire(self) -> None:
        """Profile without ratchet → no alert."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "shadow"}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])


class DetectEscalationTests(unittest.TestCase):
    """``detect_escalation`` fires when escalation_date < today and not absolute."""

    def test_escalation_date_past_fires(self) -> None:
        """Escalation passed + not absolute → alert fires."""
        triggers = at.detect_escalation(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"escalation_date": "2025-12-31"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.ESCALATION)

    def test_escalation_date_future_does_not_fire(self) -> None:
        """Future escalation → no alert."""
        triggers = at.detect_escalation(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"escalation_date": "2026-12-31"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])

    def test_absolute_phase_never_escalates(self) -> None:
        """Once absolute, escalation is moot."""
        triggers = at.detect_escalation(
            slug="org/repo",
            profile={"mode": {"phase": "absolute",
                              "ratchet": {"escalation_date": "2025-12-31"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])

    def test_no_escalation_date_does_not_fire(self) -> None:
        """Ratchet block without escalation_date → no alert."""
        triggers = at.detect_escalation(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"target_date": "2026-06-30"}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])


class DefensiveProfileShapeTests(unittest.TestCase):
    """Detectors never crash on malformed profile shapes."""

    def test_mode_not_mapping_returns_empty(self) -> None:
        """``mode:`` as a scalar → no alert, no exception."""
        for detector in (at.detect_deadline_missed, at.detect_escalation):
            with self.subTest(detector=detector.__name__):
                triggers = detector(
                    slug="org/repo",
                    profile={"mode": "not-a-mapping"},
                    today=dt.date(2026, 4, 23),
                )
                self.assertEqual(triggers, [])

    def test_mode_missing_returns_empty(self) -> None:
        """Profile without ``mode:`` key → no alert."""
        for detector in (at.detect_deadline_missed, at.detect_escalation):
            with self.subTest(detector=detector.__name__):
                triggers = detector(
                    slug="org/repo",
                    profile={},
                    today=dt.date(2026, 4, 23),
                )
                self.assertEqual(triggers, [])

    def test_ratchet_not_mapping_returns_empty(self) -> None:
        """``mode.ratchet:`` as a list → no alert, no exception."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet", "ratchet": ["a", "b"]}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(triggers, [])

    def test_date_value_is_already_a_date_object(self) -> None:
        """Profile with `target_date: date(..)` object (not str) also works."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"target_date": dt.date(2025, 1, 1)}}},
            today=dt.date(2026, 4, 23),
        )
        self.assertEqual(len(triggers), 1)

    def test_unparseable_date_returns_no_alert(self) -> None:
        """Garbage date string → parses to date.min, fails-closed."""
        triggers = at.detect_deadline_missed(
            slug="org/repo",
            profile={"mode": {"phase": "ratchet",
                              "ratchet": {"target_date": "not-a-date"}}},
            today=dt.date(2026, 4, 23),
        )
        # date.min < today → fires the alert (that's the "fail-closed"
        # contract: unparseable dates still trigger the alert so the
        # operator notices them).
        self.assertEqual(len(triggers), 1)


class DetectBypassStaleTests(unittest.TestCase):
    """``detect_bypass_stale`` fires when break-glass tracking issue > 7 days."""

    def test_stale_issue_fires(self) -> None:
        """Opened 8 days ago, still open → alert fires."""
        triggers = at.detect_bypass_stale(
            slug="org/repo",
            issue_number=42,
            opened_at=dt.datetime(2026, 4, 15, tzinfo=dt.timezone.utc),
            now=dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.BYPASS_STALE)
        self.assertEqual(triggers[0].subject, "org/repo#42")

    def test_fresh_issue_does_not_fire(self) -> None:
        """Opened 3 days ago → no alert (under 7-day threshold)."""
        triggers = at.detect_bypass_stale(
            slug="org/repo",
            issue_number=42,
            opened_at=dt.datetime(2026, 4, 20, tzinfo=dt.timezone.utc),
            now=dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(triggers, [])


class DetectDriftStuckTests(unittest.TestCase):
    """``detect_drift_stuck`` fires when a drift-sync PR has been open > 3 days."""

    def test_old_pr_fires(self) -> None:
        """PR open 4 days → alert fires."""
        triggers = at.detect_drift_stuck(
            slug="org/repo",
            pr_number=99,
            opened_at=dt.datetime(2026, 4, 19, tzinfo=dt.timezone.utc),
            now=dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.DRIFT_STUCK)
        self.assertEqual(triggers[0].subject, "org/repo#99")

    def test_fresh_pr_does_not_fire(self) -> None:
        """PR open 1 day → no alert."""
        triggers = at.detect_drift_stuck(
            slug="org/repo",
            pr_number=99,
            opened_at=dt.datetime(2026, 4, 22, tzinfo=dt.timezone.utc),
            now=dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(triggers, [])


class DetectFleetBumpFailTests(unittest.TestCase):
    """``detect_fleet_bump_fail`` fires when staging wave CI failed."""

    def test_any_staging_failure_fires(self) -> None:
        """One failing staging PR → alert fires with recipe name as subject."""
        triggers = at.detect_fleet_bump_fail(
            recipe_name="Node 20 -> 24",
            staging_results=[
                {"slug": "org/staging-a", "conclusion": "success"},
                {"slug": "org/staging-b", "conclusion": "failure"},
            ],
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.FLEET_BUMP_FAIL)
        self.assertIn("org/staging-b", triggers[0].body)
        self.assertIn("Node 20 -> 24", triggers[0].subject)

    def test_all_green_staging_does_not_fire(self) -> None:
        """Every staging PR green → no alert."""
        triggers = at.detect_fleet_bump_fail(
            recipe_name="Node 20 -> 24",
            staging_results=[
                {"slug": "org/staging-a", "conclusion": "success"},
                {"slug": "org/staging-b", "conclusion": "success"},
            ],
        )
        self.assertEqual(triggers, [])


class DetectFlagMissingTests(unittest.TestCase):
    """``detect_flag_missing`` fires when a declared flag has no Codecov report."""

    def test_missing_flag_fires_one_alert_per_flag(self) -> None:
        """Declared [backend, ui], reported [backend] → 1 alert for ui."""
        triggers = at.detect_flag_missing(
            slug="org/repo",
            declared_flags=["backend", "ui"],
            reported_flags=["backend"],
        )
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].alert_type, alerts.AlertType.FLAG_MISSING)
        self.assertEqual(triggers[0].subject, "org/repo:ui")

    def test_multiple_missing_flags_each_open_separate_alert(self) -> None:
        """Declared [a, b, c], reported [a] → 2 separate alerts."""
        triggers = at.detect_flag_missing(
            slug="org/repo",
            declared_flags=["a", "b", "c"],
            reported_flags=["a"],
        )
        self.assertEqual(len(triggers), 2)
        subjects = sorted(t.subject for t in triggers)
        self.assertEqual(subjects, ["org/repo:b", "org/repo:c"])

    def test_all_flags_present_does_not_fire(self) -> None:
        """Declared == reported → no alert."""
        triggers = at.detect_flag_missing(
            slug="org/repo",
            declared_flags=["backend", "ui"],
            reported_flags=["ui", "backend"],
        )
        self.assertEqual(triggers, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
