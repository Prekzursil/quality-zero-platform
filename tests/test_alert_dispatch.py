"""Tests for ``scripts.quality.alert_dispatch``.

The dispatcher iterates every detector in ``alert_triggers`` over the
appropriate inputs and calls ``alerts.open_alert_issue`` for each
trigger that fires. This is the last wiring step of Phase 5 inc-5.
"""

from __future__ import absolute_import

import datetime as dt
import json
import subprocess
import unittest
from unittest.mock import MagicMock

from scripts.quality import alert_dispatch, alerts
from scripts.quality import alert_triggers as at


class DispatchDetectedTriggersTests(unittest.TestCase):
    """``dispatch_detected_triggers`` hands each trigger to the opener."""

    def test_each_trigger_opens_one_issue(self) -> None:
        """Two triggers → two calls to ``open_alert_issue``."""
        triggers = [
            at.AlertTrigger(
                alert_type=alerts.AlertType.REGRESSION,
                subject="org/repo",
                body="body-a",
            ),
            at.AlertTrigger(
                alert_type=alerts.AlertType.FLAG_MISSING,
                subject="org/repo:ui",
                body="body-b",
            ),
        ]
        opener = MagicMock(
            side_effect=[
                {"number": 1, "title": "x", "created": True},
                {"number": 2, "title": "y", "created": True},
            ]
        )
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
            subject="org/repo",
            body="body",
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
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.UTC),
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
                    "opened_at": dt.datetime(2026, 4, 10, tzinfo=dt.UTC),
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
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.UTC),
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
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.UTC),
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
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.UTC),
            "profiles": [],
            "bypass_issues": [],
            "drift_prs": [
                {
                    "slug": "org/slow-repo",
                    "pr_number": 77,
                    "opened_at": dt.datetime(2026, 4, 18, tzinfo=dt.UTC),
                },
            ],
            "staging_results": [],
            "recipe_name": "",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        drift_triggers = [t for t in triggers if t.alert_type == alerts.AlertType.DRIFT_STUCK]
        self.assertEqual(len(drift_triggers), 1)
        self.assertEqual(drift_triggers[0].subject, "org/slow-repo#77")

    def test_fleet_bump_fires_when_staging_failure(self) -> None:
        """Staging failures + recipe name → bump alert fires."""
        state = {
            "today": dt.date(2026, 4, 23),
            "now": dt.datetime(2026, 4, 23, tzinfo=dt.UTC),
            "profiles": [],
            "bypass_issues": [],
            "drift_prs": [],
            "staging_results": [
                {"slug": "org/staging-a", "conclusion": "failure"},
            ],
            "recipe_name": "Node 20 -> 24",
        }
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        bump = [t for t in triggers if t.alert_type == alerts.AlertType.FLEET_BUMP_FAIL]
        self.assertEqual(len(bump), 1)


class WorkflowRunTests(unittest.TestCase):
    """The platform's own workflow history: fetched, grouped, alerted on, recovered."""

    RUNS = [
        {"workflowName": "Wave", "status": "completed", "conclusion": "failure", "createdAt": "2026-10-22T00:00:00Z"},
        {"workflowName": "Wave", "status": "completed", "conclusion": "failure", "createdAt": "2026-10-24T00:00:00Z"},
        {"workflowName": "Wave", "status": "completed", "conclusion": "cancelled", "createdAt": "2026-10-24T06:00:00Z"},
        {"workflowName": "Wave", "status": "completed", "conclusion": "failure", "createdAt": "2026-10-23T00:00:00Z"},
        {"workflowName": "Verify", "status": "in_progress", "conclusion": "", "createdAt": "2026-10-24T09:00:00Z"},
        {"workflowName": "Verify", "status": "completed", "conclusion": "success", "createdAt": "2026-10-24T08:00:00Z"},
        {"workflowName": "Verify", "status": "completed", "conclusion": None, "createdAt": "2026-10-24T07:00:00Z"},
        "not-a-mapping",
    ]

    def test_conclusions_are_grouped_newest_first_without_neutral_or_running_runs(self) -> None:
        """Cancelled / running / blank outcomes never break or end a streak."""
        self.assertEqual(
            alert_dispatch.workflow_conclusions(self.RUNS),
            {"Wave": ["failure", "failure", "failure"], "Verify": ["success"]},
        )

    def test_the_failing_workflow_fires_from_fleet_state(self) -> None:
        """build_triggers_from_fleet_state runs the detector per workflow."""
        state = {"workflow_runs": alert_dispatch.workflow_conclusions(self.RUNS)}
        triggers = alert_dispatch.build_triggers_from_fleet_state(state)
        self.assertEqual([(t.alert_type, t.subject) for t in triggers], [(alerts.AlertType.WORKFLOW_FAILING, "Wave")])

    def test_recovered_workflows_get_their_alert_closed(self) -> None:
        """Only a workflow whose newest completed run succeeded is closed; dry-run is passed through."""
        closer = MagicMock(return_value={"number": 9, "closed": True})
        results = alert_dispatch.close_recovered_workflow_alerts(
            platform_slug="o/r",
            workflow_runs={"Wave": ["failure"], "Verify": ["success", "failure"], "Empty": []},
            closer=closer,
            dry_run=True,
        )
        self.assertEqual(results, [{"number": 9, "closed": True}])
        (call,) = closer.call_args_list
        self.assertEqual(call.args, ("o/r",))
        self.assertEqual(call.kwargs["alert_type"], alerts.AlertType.WORKFLOW_FAILING)
        self.assertEqual(call.kwargs["subject"], "Verify")
        self.assertTrue(call.kwargs["dry_run"])
        self.assertIn("succeeded", call.kwargs["close_comment"])

    @staticmethod
    def _completed(stdout: str, returncode: int = 0) -> "subprocess.CompletedProcess[str]":
        return subprocess.CompletedProcess(["gh"], returncode, stdout, "boom\n")

    def _runner(self, *, workflows: str, runs: str = "[]", runs_rc: int = 0, workflows_rc: int = 0) -> MagicMock:
        """A fake gh answering `workflow list` and `run list` separately."""

        def answer(argv, **_kwargs):
            if argv[1:3] == ["workflow", "list"]:
                return self._completed(workflows, workflows_rc)
            return self._completed(runs, runs_rc)

        return MagicMock(side_effect=answer)

    def test_history_is_fetched_per_active_workflow(self) -> None:
        """THE review finding: one shared --limit let hourly runs crowd out a weekly workflow."""
        workflows = json.dumps([{"id": 11, "state": "active"}, {"id": 12, "state": "disabled_manually"}, "junk"])
        runner = self._runner(workflows=workflows, runs=json.dumps(self.RUNS))
        runs = alert_dispatch.fetch_workflow_runs("o/r", runner=runner)
        self.assertEqual(len(runs), 7)
        run_lists = [call.args[0] for call in runner.call_args_list if call.args[0][1:3] == ["run", "list"]]
        self.assertEqual(len(run_lists), 1)
        argv = run_lists[0]
        self.assertEqual(argv[argv.index("--workflow") + 1], "11")
        self.assertEqual(argv[argv.index("--branch") + 1], "main")
        self.assertEqual(argv[argv.index("--limit") + 1], "10")

    def test_non_list_payloads_are_empty_and_failures_raise(self) -> None:
        """No workflows means no runs; a failed listing is never read as "all green"."""
        self.assertEqual(alert_dispatch.fetch_workflow_runs("o/r", runner=self._runner(workflows="{}")), [])
        self.assertEqual(alert_dispatch.fetch_workflow_runs("o/r", runner=self._runner(workflows="")), [])
        with self.assertRaisesRegex(alerts.AlertCommandError, "gh workflow list"):
            alert_dispatch.fetch_workflow_runs("o/r", runner=self._runner(workflows="", workflows_rc=1))
        active = json.dumps([{"id": 1, "state": "active"}])
        with self.assertRaisesRegex(alerts.AlertCommandError, "gh run list"):
            alert_dispatch.fetch_workflow_runs("o/r", runner=self._runner(workflows=active, runs_rc=1))


class VanishedWorkflowTests(unittest.TestCase):
    """An alert for a workflow that no longer exists can never be closed by a success."""

    def test_only_active_workflows_are_listed(self) -> None:
        """Disabled workflows and junk rows are dropped."""
        listed = json.dumps(
            [{"id": 1, "name": "A", "state": "active"}, {"id": 2, "name": "B", "state": "disabled_fork"}, 3]
        )
        runner = MagicMock(return_value=subprocess.CompletedProcess(["gh"], 0, listed, ""))
        self.assertEqual(
            alert_dispatch.fetch_active_workflows("o/r", runner=runner), [{"id": 1, "name": "A", "state": "active"}]
        )

    def test_given_workflows_are_not_listed_again(self) -> None:
        """The scheduled job lists the workflows once and reuses them."""
        runner = MagicMock(return_value=subprocess.CompletedProcess(["gh"], 0, "[]", ""))
        alert_dispatch.fetch_workflow_runs("o/r", workflows=[{"id": 5}], runner=runner)
        (call,) = runner.call_args_list
        self.assertEqual(call.args[0][1:3], ["run", "list"])
        self.assertIn("5", call.args[0])

    def test_alerts_of_vanished_workflows_close(self) -> None:
        """THE review finding: deleted / renamed / disabled workflows left their alert open forever."""
        lister = MagicMock(
            return_value=[
                {"number": 1, "title": "[alert:workflow-failing] Old Workflow"},
                {"number": 2, "title": "[alert:workflow-failing] Wave"},
                {"number": 3, "title": "[alert:regression] Old Workflow"},
            ]
        )
        closer = MagicMock(return_value={"number": 1, "closed": True})
        results = alert_dispatch.close_recovered_workflow_alerts(
            platform_slug="o/r",
            workflow_runs={"Wave": ["failure"]},
            active_workflows={"Wave"},
            closer=closer,
            lister=lister,
        )
        self.assertEqual(results, [{"number": 1, "closed": True}])
        (call,) = closer.call_args_list
        self.assertEqual(call.kwargs["subject"], "Old Workflow")
        self.assertIn("no longer an active workflow", call.kwargs["close_comment"])
        self.assertEqual(lister.call_args.kwargs["alert_type"], alerts.AlertType.WORKFLOW_FAILING)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
