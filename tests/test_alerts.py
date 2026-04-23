"""Tests for the Phase 5 ``scripts.quality.alerts`` module.

Covers the 8 alert types from ``docs/QZP-V2-DESIGN.md`` §8:
``regression``, ``deadline-missed``, ``escalation``, ``bypass-stale``,
``drift-stuck``, ``fleet-bump-fail``, ``repo-not-profiled``,
``flag-missing``. Every event opens (or dedupes) a dedicated
GitHub issue — no digest, per the design.
"""

from __future__ import absolute_import

import json
import subprocess
import unittest
from typing import List
from unittest.mock import MagicMock

from scripts.quality import alerts


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Build a lightweight ``CompletedProcess`` for runner doubles."""
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr="",
    )


class AlertTypeRegistryTests(unittest.TestCase):
    """The design spec's 8 alert types are all registered with labels."""

    def test_every_documented_alert_type_has_a_label(self) -> None:
        """All 8 alert:* labels from §8 are exposed as ``AlertType`` members."""
        expected_labels = {
            "alert:regression",
            "alert:deadline-missed",
            "alert:escalation",
            "alert:bypass-stale",
            "alert:drift-stuck",
            "alert:fleet-bump-fail",
            "alert:repo-not-profiled",
            "alert:flag-missing",
        }
        actual_labels = {member.label for member in alerts.AlertType}
        self.assertEqual(actual_labels, expected_labels)

    def test_label_is_stable_string(self) -> None:
        """``AlertType.label`` is the enum's canonical GitHub label string."""
        self.assertEqual(
            alerts.AlertType.REGRESSION.label, "alert:regression",
        )
        self.assertEqual(
            alerts.AlertType.FLAG_MISSING.label, "alert:flag-missing",
        )


class AlertTitleTests(unittest.TestCase):
    """``alert_issue_title`` produces dedupable ``[label] subject`` strings."""

    def test_title_format_is_bracket_label_space_subject(self) -> None:
        """``[alert:drift-stuck] org/repo`` canonical format."""
        title = alerts.alert_issue_title(
            alerts.AlertType.DRIFT_STUCK, "org/repo",
        )
        self.assertEqual(title, "[alert:drift-stuck] org/repo")

    def test_title_preserves_subject_punctuation_for_flag_alerts(self) -> None:
        """``alert:flag-missing`` carries ``slug:flag`` subjects literally."""
        title = alerts.alert_issue_title(
            alerts.AlertType.FLAG_MISSING, "Prekzursil/event-link:frontend",
        )
        self.assertEqual(
            title, "[alert:flag-missing] Prekzursil/event-link:frontend",
        )


class FindExistingAlertIssueTests(unittest.TestCase):
    """``find_existing_alert_issue`` is dedupe-by-title over gh issue list."""

    def test_match_found_returns_the_issue_mapping(self) -> None:
        """When gh returns an issue with matching title, it is returned."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"number": 42, "title": "[alert:drift-stuck] org/repo", "state": "open"},
            {"number": 99, "title": "other-thing", "state": "open"},
        ])))
        issue = alerts.find_existing_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.DRIFT_STUCK,
            subject="org/repo",
            runner=runner,
        )
        self.assertIsNotNone(issue)
        self.assertEqual(issue["number"], 42)

    def test_no_match_returns_none(self) -> None:
        """When no matching title in gh output, return None."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"number": 99, "title": "unrelated", "state": "open"},
        ])))
        issue = alerts.find_existing_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.DRIFT_STUCK,
            subject="org/repo",
            runner=runner,
        )
        self.assertIsNone(issue)

    def test_empty_stdout_returns_none(self) -> None:
        """gh returning empty output is treated as no matches."""
        runner = MagicMock(return_value=_fake_completed(""))
        issue = alerts.find_existing_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.REGRESSION,
            subject="org/repo",
            runner=runner,
        )
        self.assertIsNone(issue)

    def test_non_list_stdout_returns_none(self) -> None:
        """Defensive: if gh returns a non-list JSON, never crash."""
        runner = MagicMock(return_value=_fake_completed('{"error": "boom"}'))
        issue = alerts.find_existing_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.REGRESSION,
            subject="org/repo",
            runner=runner,
        )
        self.assertIsNone(issue)


class OpenAlertIssueTests(unittest.TestCase):
    """``open_alert_issue`` opens a new issue or reuses an existing one."""

    def test_dry_run_returns_stub_without_calling_gh(self) -> None:
        """Dry run returns a stub record and never invokes the runner."""
        runner = MagicMock()
        result = alerts.open_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.REGRESSION,
            subject="org/repo",
            body="Body text",
            runner=runner,
            dry_run=True,
        )
        self.assertFalse(result["created"])
        self.assertEqual(result["number"], 0)
        runner.assert_not_called()

    def test_existing_open_issue_is_reused(self) -> None:
        """When a matching open issue exists, ``created`` is False."""
        list_result = _fake_completed(json.dumps([
            {"number": 7, "title": "[alert:regression] org/repo", "state": "open"},
        ]))
        runner = MagicMock(return_value=list_result)
        result = alerts.open_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.REGRESSION,
            subject="org/repo",
            body="Cov dropped by 0.7%.",
            runner=runner,
        )
        self.assertEqual(result["number"], 7)
        self.assertFalse(result["created"])
        # Only the list call should happen — never the create call.
        self.assertEqual(runner.call_count, 1)

    def test_create_new_issue_when_none_exists(self) -> None:
        """Happy path: one list call + one create call; parses issue number."""
        responses: List[subprocess.CompletedProcess] = [
            _fake_completed(json.dumps([])),
            _fake_completed(
                "https://github.com/Prekzursil/quality-zero-platform/issues/314\n",
            ),
        ]
        runner = MagicMock(side_effect=responses)
        result = alerts.open_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.REGRESSION,
            subject="org/repo",
            body="Coverage regression detected on main.",
            runner=runner,
        )
        self.assertEqual(result["number"], 314)
        self.assertTrue(result["created"])
        self.assertEqual(runner.call_count, 2)

    def test_create_without_url_tail_returns_zero(self) -> None:
        """Defensive: a non-URL stdout yields number=0 but created=True."""
        responses: List[subprocess.CompletedProcess] = [
            _fake_completed(json.dumps([])),
            _fake_completed("some random gh stdout\n"),
        ]
        runner = MagicMock(side_effect=responses)
        result = alerts.open_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.FLAG_MISSING,
            subject="org/repo:frontend",
            body="Flag 'frontend' declared but no Codecov report.",
            runner=runner,
        )
        self.assertEqual(result["number"], 0)
        self.assertTrue(result["created"])

    def test_create_with_empty_stdout_returns_zero(self) -> None:
        """``gh issue create`` with no stdout gives number=0 but created=True."""
        responses: List[subprocess.CompletedProcess] = [
            _fake_completed(json.dumps([])),
            _fake_completed(""),
        ]
        runner = MagicMock(side_effect=responses)
        result = alerts.open_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.BYPASS_STALE,
            subject="org/repo",
            body="bypass open > 7 days",
            runner=runner,
        )
        self.assertEqual(result["number"], 0)
        self.assertTrue(result["created"])


class CloseAlertIssueTests(unittest.TestCase):
    """``close_alert_issue`` closes a matching open issue, if any."""

    def test_dry_run_returns_stub_without_calling_gh(self) -> None:
        """Dry run never invokes the runner."""
        runner = MagicMock()
        result = alerts.close_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.DRIFT_STUCK,
            subject="org/repo",
            runner=runner,
            dry_run=True,
        )
        self.assertFalse(result["closed"])
        self.assertEqual(result["number"], 0)
        runner.assert_not_called()

    def test_no_matching_issue_returns_unclosed_stub(self) -> None:
        """When list returns no match, closed=False, number=0."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([])))
        result = alerts.close_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.DRIFT_STUCK,
            subject="org/repo",
            runner=runner,
        )
        self.assertFalse(result["closed"])

    def test_closes_matching_open_issue(self) -> None:
        """When a matching issue exists, ``gh issue close`` is called."""
        responses: List[subprocess.CompletedProcess] = [
            _fake_completed(json.dumps([
                {"number": 11, "title": "[alert:drift-stuck] org/repo", "state": "open"},
            ])),
            _fake_completed(""),  # gh close is silent on success.
        ]
        runner = MagicMock(side_effect=responses)
        result = alerts.close_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.DRIFT_STUCK,
            subject="org/repo",
            runner=runner,
            close_comment="sync PR merged — clearing",
        )
        self.assertTrue(result["closed"])
        self.assertEqual(result["number"], 11)
        self.assertEqual(runner.call_count, 2)
        # Truthy close_comment branch → --comment present in close args.
        close_args = runner.call_args_list[1].args[0]
        self.assertIn("--comment", close_args)

    def test_close_without_comment_skips_comment_flag(self) -> None:
        """Empty ``close_comment`` → ``gh issue close`` is called without --comment."""
        responses: List[subprocess.CompletedProcess] = [
            _fake_completed(json.dumps([
                {"number": 22, "title": "[alert:flag-missing] org/repo:frontend", "state": "open"},
            ])),
            _fake_completed(""),
        ]
        runner = MagicMock(side_effect=responses)
        result = alerts.close_alert_issue(
            "Prekzursil/quality-zero-platform",
            alert_type=alerts.AlertType.FLAG_MISSING,
            subject="org/repo:frontend",
            runner=runner,
        )
        self.assertTrue(result["closed"])
        self.assertEqual(result["number"], 22)
        # Falsy close_comment branch → --comment NOT added.
        close_args = runner.call_args_list[1].args[0]
        self.assertNotIn("--comment", close_args)


class ResolveAlertTypeTests(unittest.TestCase):
    """``resolve_alert_type`` parses label strings back to enum members."""

    def test_known_label_returns_enum(self) -> None:
        """``alert:regression`` → ``AlertType.REGRESSION``."""
        self.assertEqual(
            alerts.resolve_alert_type("alert:regression"),
            alerts.AlertType.REGRESSION,
        )

    def test_bare_suffix_also_resolves(self) -> None:
        """``regression`` (no prefix) also resolves for CLI ergonomics."""
        self.assertEqual(
            alerts.resolve_alert_type("regression"),
            alerts.AlertType.REGRESSION,
        )

    def test_unknown_label_raises(self) -> None:
        """``alert:nonexistent`` raises ``ValueError``."""
        with self.assertRaises(ValueError):
            alerts.resolve_alert_type("alert:nope-does-not-exist")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
