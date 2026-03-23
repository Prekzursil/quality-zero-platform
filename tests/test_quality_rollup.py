from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from scripts.quality import build_quality_rollup, post_pr_quality_comment


class QualityRollupTests(unittest.TestCase):
    def test_build_rollup_combines_expected_contexts_lane_artifacts_and_check_results(self) -> None:
        profile = {
            "slug": "Prekzursil/example-repo",
            "active_required_contexts": [
                "Coverage 100 Gate",
                "Sonar Zero",
                "Semgrep Zero",
            ],
        }
        lane_payloads = {
            "coverage": {"status": "pass", "findings": [], "summary": "100.00%"},
            "sonar": {"status": "fail", "findings": ["Sonar reports 2 open issues (expected 0)."]},
        }
        contexts = {
            "Coverage 100 Gate": {"state": "completed", "conclusion": "success", "source": "check_run"},
            "Sonar Zero": {"state": "completed", "conclusion": "failure", "source": "check_run"},
            "Semgrep Zero": {"state": "completed", "conclusion": "success", "source": "check_run"},
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
            ["Coverage 100 Gate", "Semgrep Zero", "Sonar Zero"],
        )
        self.assertEqual(rollup["contexts"][2]["detail"], "Sonar reports 2 open issues (expected 0).")
        self.assertEqual(rollup["contexts"][1]["detail"], "No findings.")

    def test_render_rollup_markdown_and_comment_body_include_marker(self) -> None:
        payload = {
            "repo": "Prekzursil/example-repo",
            "sha": "abc123",
            "status": "fail",
            "contexts": [
                {"context": "Coverage 100 Gate", "status": "pass", "detail": "100.00%"},
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

            payloads = build_quality_rollup.load_lane_payloads(root)

        self.assertEqual(sorted(payloads), ["coverage", "sonar"])
        self.assertEqual(payloads["coverage"]["status"], "pass")
        self.assertEqual(payloads["sonar"]["findings"], ["bad"])
