"""Test quality rollup."""

from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quality import build_quality_rollup, post_pr_quality_comment

FAKE_GITHUB_CREDENTIAL = "gh-auth-placeholder"


def pending_then_success_contexts():
    """Return one minimal pending-then-success context sequence."""
    return [
        {
            "Coverage 100 Gate": {
                "state": "in_progress",
                "conclusion": "",
                "source": "check_run",
            },
        },
        {
            "Coverage 100 Gate": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
        },
    ]


def exercise_wait_for_contexts(responses):
    """Handle exercise wait for contexts."""
    with patch.object(
        build_quality_rollup,
        "load_check_contexts",
        side_effect=responses,
    ), patch("scripts.quality.build_quality_rollup.time.sleep") as sleep_mock:
        contexts = build_quality_rollup._wait_for_contexts(
            build_quality_rollup.ContextWaitRequest(
                repo="owner/repo",
                sha="abc123",
                token=FAKE_GITHUB_CREDENTIAL,
                required_contexts=["Coverage 100 Gate"],
                timeout_seconds=2,
                poll_seconds=0,
            )
        )
    return contexts, sleep_mock


class QualityRollupTests(unittest.TestCase):
    """Quality Rollup Tests."""

    def test_build_rollup_combines_expected_contexts_lane_artifacts_and_check_results(
        self,
    ) -> None:
        """Cover build rollup combines expected contexts lane artifacts and check results."""
        profile = {
            "slug": "Prekzursil/example-repo",
            "active_required_contexts": [
                "Coverage 100 Gate",
                "DeepSource Visible Zero",
                "Sonar Zero",
                "Semgrep Zero",
            ],
        }
        lane_payloads = {
            "coverage": {"status": "pass", "findings": [], "summary": "100.00%"},
            "deepsource_visible": {"status": "pass", "findings": [], "open_issues": 0},
            "sonar": {
                "status": "fail",
                "findings": ["Sonar reports 2 open issues (expected 0)."],
            },
        }
        contexts = {
            "Coverage 100 Gate": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
            "DeepSource Visible Zero": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
            "Sonar Zero": {
                "state": "completed",
                "conclusion": "failure",
                "source": "check_run",
            },
            "Semgrep Zero": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
        }

        rollup = build_quality_rollup.build_rollup(
            profile=profile,
            lane_payloads=lane_payloads,
            contexts=contexts,
            sha="abc123",
        )

        self.assertEqual(rollup["status"], "fail")
        self.assertEqual(rollup["repo"], "Prekzursil/example-repo")
        self.assertEqual(rollup["sha"], "abc123")
        self.assertEqual(
            [item["context"] for item in rollup["contexts"]],
            [
                "Coverage 100 Gate",
                "DeepSource Visible Zero",
                "Semgrep Zero",
                "Sonar Zero",
            ],
        )
        self.assertEqual(
            rollup["contexts"][3]["detail"], "Sonar reports 2 open issues (expected 0)."
        )
        self.assertEqual(rollup["contexts"][1]["detail"], "Open issues: 0")

    def test_render_rollup_markdown_and_comment_body_include_marker(self) -> None:
        """Cover render rollup markdown and comment body include marker."""
        payload = {
            "repo": "Prekzursil/example-repo",
            "sha": "abc123",
            "status": "fail",
            "contexts": [
                {"context": "Coverage 100 Gate", "status": "pass", "detail": "100.00%"},
                {
                    "context": "DeepSource Visible Zero",
                    "status": "pass",
                    "detail": "Open issues: 0",
                },
                {"context": "Sonar Zero", "status": "fail", "detail": "2 issues"},
            ],
        }

        markdown = build_quality_rollup.render_markdown(payload)
        self.assertIn("# Quality Rollup", markdown)
        self.assertIn("Coverage 100 Gate", markdown)
        self.assertIn("Sonar Zero", markdown)

        comment = post_pr_quality_comment.render_comment_body(markdown)
        self.assertIn("<!-- quality-zero-rollup -->", comment)
        self.assertIn("# Quality Rollup", comment)

    def test_load_lane_payloads_reads_known_json_artifacts(self) -> None:
        """Cover load lane payloads reads known json artifacts."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "coverage-artifacts" / "coverage-100").mkdir(parents=True)
            (root / "coverage-artifacts" / "coverage-100" / "coverage.json").write_text(
                '{"status":"pass","findings":[]}',
                encoding="utf-8",
            )
            (root / "sonar-artifacts" / "sonar-zero").mkdir(parents=True)
            (root / "sonar-artifacts" / "sonar-zero" / "sonar.json").write_text(
                '{"status":"fail","findings":["bad"]}',
                encoding="utf-8",
            )
            (root / "deepsource_visible-artifacts" / "deepsource-visible-zero").mkdir(
                parents=True
            )
            (
                root
                / "deepsource_visible-artifacts"
                / "deepsource-visible-zero"
                / "deepsource.json"
            ).write_text(
                '{"status":"pass","open_issues":0,"findings":[]}',
                encoding="utf-8",
            )

            payloads = build_quality_rollup.load_lane_payloads(root)

        self.assertEqual(sorted(payloads), ["coverage", "deepsource_visible", "sonar"])
        self.assertEqual(payloads["coverage"]["status"], "pass")
        self.assertEqual(payloads["deepsource_visible"]["open_issues"], 0)
        self.assertEqual(payloads["sonar"]["findings"], ["bad"])

    def test_status_resolution_handles_suffix_matches_and_status_contexts(self) -> None:
        """Cover status resolution handles suffix matches and status contexts."""
        contexts = {
            "shared-scanner-matrix / Semgrep Zero": {
                "state": "completed",
                "conclusion": "success",
                "source": "check_run",
            },
            "DeepScan": {
                "state": "success",
                "conclusion": "success",
                "source": "status",
            },
            "Pending": {
                "state": "in_progress",
                "conclusion": "",
                "source": "check_run",
            },
        }
        self.assertEqual(
            build_quality_rollup._status_from_context("Semgrep Zero", contexts), "pass"
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("DeepScan", contexts), "pass"
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("Pending", contexts), "pending"
        )
        self.assertEqual(
            build_quality_rollup._status_from_context("Missing", contexts), "missing"
        )

    def test_load_check_contexts_merges_check_runs_and_statuses(self) -> None:
        """Cover load check contexts merges check runs and statuses."""
        responses = [
            {
                "check_runs": [
                    {
                        "name": "shared-scanner-matrix / QLTY Zero",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            },
            {"statuses": [{"context": "DeepScan", "state": "success"}]},
        ]

        with patch.object(
            build_quality_rollup, "_github_payload", side_effect=responses
        ):
            contexts = build_quality_rollup.load_check_contexts(
                "owner/repo", "sha", "token"
            )

        self.assertEqual(
            contexts["shared-scanner-matrix / QLTY Zero"]["conclusion"], "success"
        )
        self.assertEqual(contexts["DeepScan"]["source"], "status")

    def test_wait_for_contexts_polls_until_pending_contexts_settle(self) -> None:
        """Cover wait for contexts polls until pending contexts settle."""
        contexts, sleep_mock = exercise_wait_for_contexts(
            pending_then_success_contexts()
        )

        self.assertEqual(contexts["Coverage 100 Gate"]["conclusion"], "success")
        sleep_mock.assert_called_once()

    def test_wait_for_contexts_also_retries_missing_contexts(self) -> None:
        """Cover wait for contexts also retries missing contexts."""
        responses = [
            {},
            {
                "Coverage 100 Gate": {
                    "state": "completed",
                    "conclusion": "success",
                    "source": "check_run",
                }
            },
        ]

        contexts, sleep_mock = exercise_wait_for_contexts(responses)

        self.assertEqual(contexts["Coverage 100 Gate"]["conclusion"], "success")
        sleep_mock.assert_called_once()

    def test_wait_for_contexts_returns_empty_when_timeout_expires_before_first_poll(
        self,
    ) -> None:
        """Cover wait for contexts returns empty when timeout expires before first poll."""
        with patch.object(
            build_quality_rollup,
            "load_check_contexts",
            return_value={
                "Coverage 100 Gate": {
                    "state": "in_progress",
                    "conclusion": "",
                    "source": "check_run",
                }
            },
        ), patch(
            "scripts.quality.build_quality_rollup.time.sleep"
        ) as sleep_mock, patch(
            "scripts.quality.build_quality_rollup.time.time",
            side_effect=[10, 12],
        ):
            contexts = build_quality_rollup._wait_for_contexts(
                build_quality_rollup.ContextWaitRequest(
                    repo="owner/repo",
                    sha="abc123",
                    token=FAKE_GITHUB_CREDENTIAL,
                    required_contexts=["Coverage 100 Gate"],
                    timeout_seconds=1,
                    poll_seconds=0,
                )
            )

        self.assertEqual(contexts, {})
        sleep_mock.assert_not_called()
