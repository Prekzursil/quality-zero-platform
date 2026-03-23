from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from scripts.quality import build_admin_dashboard


class AdminDashboardTests(unittest.TestCase):
    def test_build_dashboard_payload_summarizes_repos_profiles_and_health(self) -> None:
        payload = build_admin_dashboard.build_dashboard_payload(
            inventory={
                "repos": [
                    {
                        "slug": "Prekzursil/example-repo",
                        "profile": "example-repo",
                        "rollout": "phase2-wave0",
                        "default_branch": "main",
                    }
                ]
            },
            profiles={
                "Prekzursil/example-repo": {
                    "enabled_scanners": {"coverage": True, "sonar": True},
                    "issue_policy": {"mode": "ratchet"},
                    "coverage": {"min_percent": 100.0},
                }
            },
            live={
                "Prekzursil/example-repo": {
                    "default_branch_health": "partial",
                    "open_pr_health": "success",
                    "ruleset_present": False,
                }
            },
        )

        self.assertEqual(payload["repo_count"], 1)
        repo = payload["repos"][0]
        self.assertEqual(repo["slug"], "Prekzursil/example-repo")
        self.assertEqual(repo["issue_policy_mode"], "ratchet")
        self.assertEqual(repo["default_branch_health"], "partial")
        self.assertFalse(repo["ruleset_present"])

    def test_render_dashboard_html_includes_controls_and_repo_rows(self) -> None:
        payload = {
            "generated_at": "2026-03-23T00:00:00Z",
            "repo_count": 1,
            "repos": [
                {
                    "slug": "Prekzursil/example-repo",
                    "profile": "example-repo",
                    "rollout": "phase2-wave0",
                    "issue_policy_mode": "ratchet",
                    "enabled_scanners": ["coverage", "sonar"],
                    "default_branch_health": "partial",
                    "open_pr_health": "success",
                    "ruleset_present": False,
                }
            ],
        }

        html = build_admin_dashboard.render_dashboard_html(payload)

        self.assertIn("Quality Zero Control Plane", html)
        self.assertIn("Prekzursil/example-repo", html)
        self.assertIn("phase2-wave0", html)
        self.assertIn("ratchet", html)

    def test_write_dashboard_outputs_index_and_data_json(self) -> None:
        payload = {
            "generated_at": "2026-03-23T00:00:00Z",
            "repo_count": 0,
            "repos": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            build_admin_dashboard.write_dashboard(output_dir, payload)

            self.assertTrue((output_dir / "index.html").is_file())
            self.assertTrue((output_dir / "dashboard-data.json").is_file())
