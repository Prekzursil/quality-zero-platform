"""Test admin dashboard."""

from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from scripts.quality import build_admin_dashboard


class AdminDashboardTests(unittest.TestCase):
    """Admin Dashboard Tests."""

    def test_build_dashboard_payload_summarizes_repos_profiles_and_health(self) -> None:
        """Cover build dashboard payload summarizes repos profiles and health."""
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
                    "issue_policy": {"mode": "ratchet", "baseline_ref": "main"},
                    "coverage": {"min_percent": 100.0, "branch_min_percent": 85.0},
                    "deps": {"policy": "zero_critical"},
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
        self.assertEqual(repo["visibility"], "public")
        self.assertEqual(repo["issue_policy_mode"], "ratchet")
        self.assertEqual(repo["issue_policy_baseline_ref"], "main")
        self.assertEqual(repo["branch_min_percent"], 85.0)
        self.assertEqual(repo["deps_policy"], "zero_critical")
        self.assertEqual(repo["default_branch_health"], "partial")
        self.assertFalse(repo["ruleset_present"])

    def test_build_dashboard_payload_redacts_private_repo_slugs(self) -> None:
        """Phase 5 §8 contract: private-repo slugs MUST be masked on the public dashboard.

        Builds a payload with one private repo + one public repo and
        asserts the private slug is replaced by ``<private>`` while the
        public one stays intact. This is the heatmap-side counterpart to
        the redaction already present in admin_dashboard_pages.py's CLI;
        without this wiring, the live ``index.html`` on GitHub Pages
        would expose every private slug under the inventory.
        """
        payload = build_admin_dashboard.build_dashboard_payload(
            inventory={
                "repos": [
                    {"slug": "Prekzursil/private-repo", "profile": "p", "rollout": "p"},
                    {"slug": "Prekzursil/public-repo", "profile": "p", "rollout": "p"},
                ]
            },
            profiles={
                "Prekzursil/private-repo": {},
                "Prekzursil/public-repo": {},
            },
            live={
                "Prekzursil/private-repo": {"visibility": "private"},
                "Prekzursil/public-repo": {"visibility": "public"},
            },
        )
        slugs = [r["slug"] for r in payload["repos"]]
        self.assertIn("<private>", slugs)
        self.assertIn("Prekzursil/public-repo", slugs)
        self.assertNotIn("Prekzursil/private-repo", slugs)

    def test_build_dashboard_payload_defaults_visibility_to_public(self) -> None:
        """Missing visibility defaults to ``public`` (no leak via redaction-skip).

        If live state didn't capture visibility (e.g. token-less run with
        no GitHub API access), the slug is treated as public — explicit
        opt-in to private redaction prevents accidentally redacting
        public repos when the metadata fetch failed.
        """
        payload = build_admin_dashboard.build_dashboard_payload(
            inventory={
                "repos": [
                    {"slug": "Prekzursil/example-repo", "profile": "p", "rollout": "p"},
                ]
            },
            profiles={"Prekzursil/example-repo": {}},
            live={},  # no live state at all
        )
        repo = payload["repos"][0]
        self.assertEqual(repo["slug"], "Prekzursil/example-repo")
        self.assertEqual(repo["visibility"], "public")

    def test_render_dashboard_html_includes_controls_and_repo_rows(self) -> None:
        """Cover render dashboard html includes controls and repo rows."""
        payload = {
            "generated_at": "2026-03-23T00:00:00Z",
            "repo_count": 1,
            "repos": [
                {
                    "slug": "Prekzursil/example-repo",
                    "profile": "example-repo",
                    "rollout": "phase2-wave0",
                    "issue_policy_mode": "ratchet",
                    "issue_policy_baseline_ref": "main",
                    "enabled_scanners": ["coverage", "sonar"],
                    "branch_min_percent": 85.0,
                    "deps_policy": "zero_critical",
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
        self.assertIn("main", html)
        self.assertIn("85.0", html)
        self.assertIn("zero_critical", html)

    def test_write_dashboard_outputs_static_assets_and_data_json(self) -> None:
        """Cover write dashboard outputs static assets and data json."""
        payload = {
            "generated_at": "2026-03-23T00:00:00Z",
            "repo_count": 0,
            "repos": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "assets"
            data_dir = assets_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            (assets_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (assets_dir / "styles.css").write_text("body{}\n", encoding="utf-8")
            (assets_dir / "app.js").write_text("console.log('ok')\n", encoding="utf-8")

            output_dir = root / "site"
            build_admin_dashboard.write_dashboard(
                output_dir, payload, assets_dir=assets_dir
            )

            self.assertTrue((output_dir / "index.html").is_file())
            self.assertTrue((output_dir / "styles.css").is_file())
            self.assertTrue((output_dir / "app.js").is_file())
            self.assertTrue((output_dir / "data" / "dashboard.json").is_file())
