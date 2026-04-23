"""Tests for ``scripts.quality.alert_dispatch``.

The dispatcher iterates every detector in ``alert_triggers`` over the
appropriate inputs and calls ``alerts.open_alert_issue`` for each
trigger that fires. This is the last wiring step of Phase 5 inc-5.
"""

from __future__ import absolute_import

import datetime as dt
import unittest
from unittest.mock import MagicMock

from scripts.quality import alert_dispatch
from scripts.quality import alert_triggers as at
from scripts.quality import alerts


class DispatchDetectedTriggersTests(unittest.TestCase):
    """``dispatch_detected_triggers`` hands each trigger to the opener."""

    def test_each_trigger_opens_one_issue(self) -> None:
        """Two triggers → two calls to ``open_alert_issue``."""
        triggers = [
            at.AlertTrigger(
                alert_type=alerts.AlertType.REGRESSION,
                subject="org/repo", body="body-a",
            ),
            at.AlertTrigger(
                alert_type=alerts.AlertType.FLAG_MISSING,
                subject="org/repo:ui", body="body-b",
            ),
        ]
        opener = MagicMock(side_effect=[
            {"number": 1, "title": "x", "created": True},
            {"number": 2, "title": "y", "created": True},
        ])
        results = alert_dispatch.dispatch_detected_triggers(
            platform_slug="Prekzursil/quality-zero-platform",
            triggers=triggers,
            opener=opener,
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(opener.call_count, 2)
        first_call = opener.call_args_list[0]
        self.assertEqual(first_call.kwargs["alert_type"], alerts.AlertType.REGRESSION)
        self.assertEqual(first_call.kwargs["subject"], "org/repo")
        self.assertEqual(first_call.kwargs["body"], "body-a")

    def test_dry_run_does_not_invoke_opener(self) -> None:
        """``dry_run=True`` yields stub records and never calls the opener."""
        trigger = at.AlertTrigger(
            alert_type=alerts.AlertType.REGRESSION,
            subject="org/repo", body="body",
        )
        opener = MagicMock()
        results = alert_dispatch.dispatch_detected_triggers(
            platform_slug="Prekzursil/quality-zero-platform",
            triggers=[trigger],
            opener=opener,
            dry_run=True,
        )
        opener.assert_not_called()
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["created"])
        self.assertEqual(results[0]["title"], "[alert:regression] org/repo")

    def test_no_triggers_no_opener_calls(self) -> None:
        """Empty triggers list → no opener calls, empty results list."""
        opener = MagicMock()
        results = alert_dispatch.dispatch_detected_triggers(
            platform_slug="Prekzursil/quality-zero-platform",
            triggers=[],
            opener=opener,
        )
        self.assertEqual(results, [])
        opener.assert_not_called()


class BuildTriggersFromFleetStateTests(unittest.TestCase):
    """``build_triggers_from_fleet_state`` aggregates every detector's output."""

    def test_all_detectors_run_and_concatenated(self) -> None:
        """Coverage regression + deadline missed + flag missing all fire."""
        state = {
            "today": dt.date(2026, 4, 23),
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
            "profiles": [
                {
                    "slug": "org/cov-drop",
                    "profile": {"mode": {"phase": "absolute"}},
                    "baseline_coverage": 100.0,
                    "current_coverage": 98.0,
                    "declared_flags": ["backend", "ui"],
                    "reported_flags": ["backend"],
                },
                {
                    "slug": "org/deadline-past",
                    "profile": {
                        "mode": {
                            "phase": "ratchet",
                            "ratchet": {"target_date": "2026-01-01"},
                        },
                    },
                    "baseline_coverage": 100.0,
                    "current_coverage": 100.0,
                    "declared_flags": [],
                    "reported_flags": [],
                },
            ],
            "bypass_issues": [
                {
                    "slug": "org/stale",
                    "issue_number": 7,
                    "opened_at": dt.datetime(2026, 4, 10, tzinfo=dt.timezone.utc),
                },
            ],
            "drift_prs": [],
            "staging_results": [],
            "recipe_name": "",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        kinds = {t.alert_type for t in triggers}
        self.assertIn(alerts.AlertType.REGRESSION, kinds)
        self.assertIn(alerts.AlertType.DEADLINE_MISSED, kinds)
        self.assertIn(alerts.AlertType.FLAG_MISSING, kinds)
        self.assertIn(alerts.AlertType.BYPASS_STALE, kinds)

    def test_empty_state_yields_no_triggers(self) -> None:
        """All-empty inputs → zero triggers."""
        state = {
            "today": dt.date(2026, 4, 23),
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
            "profiles": [],
            "bypass_issues": [],
            "drift_prs": [],
            "staging_results": [],
            "recipe_name": "",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        self.assertEqual(triggers, [])

    def test_fleet_bump_only_fires_when_recipe_and_staging_present(self) -> None:
        """Empty staging_results → no fleet-bump alert (no wave happened)."""
        state = {
            "today": dt.date(2026, 4, 23),
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
            "profiles": [],
            "bypass_issues": [],
            "drift_prs": [],
            "staging_results": [],
            "recipe_name": "Node 20 -> 24",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        self.assertEqual(
            [t for t in triggers if t.alert_type == alerts.AlertType.FLEET_BUMP_FAIL],
            [],
        )

    def test_drift_stuck_fires_from_state(self) -> None:
        """Drift PR open > 3 days in state → drift-stuck trigger."""
        state = {
            "today": dt.date(2026, 4, 23),
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
            "profiles": [],
            "bypass_issues": [],
            "drift_prs": [
                {
                    "slug": "org/slow-repo",
                    "pr_number": 77,
                    "opened_at": dt.datetime(2026, 4, 18, tzinfo=dt.timezone.utc),
                },
            ],
            "staging_results": [],
            "recipe_name": "",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        drift_triggers = [
            t for t in triggers
            if t.alert_type == alerts.AlertType.DRIFT_STUCK
        ]
        self.assertEqual(len(drift_triggers), 1)
        self.assertEqual(drift_triggers[0].subject, "org/slow-repo#77")

    def test_fleet_bump_fires_when_staging_failure(self) -> None:
        """Staging failures + recipe name → bump alert fires."""
        state = {
            "today": dt.date(2026, 4, 23),
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.timezone.utc),
            "profiles": [],
            "bypass_issues": [],
            "drift_prs": [],
            "staging_results": [
                {"slug": "org/staging-a", "conclusion": "failure"},
            ],
            "recipe_name": "Node 20 -> 24",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        bump = [
            t for t in triggers
            if t.alert_type == alerts.AlertType.FLEET_BUMP_FAIL
        ]
        self.assertEqual(len(bump), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
