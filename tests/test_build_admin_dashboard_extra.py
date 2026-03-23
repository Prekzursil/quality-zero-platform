from __future__ import absolute_import

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from scripts.quality import build_admin_dashboard


class BuildAdminDashboardExtraTests(unittest.TestCase):
    def test_helper_rendering_and_health_helpers_cover_non_default_paths(self) -> None:
        item = {
            "slug": "Prekzursil/example",
            "profile": "example",
            "rollout": "phase2",
            "issue_policy_mode": "ratchet",
            "issue_policy_baseline_ref": "main",
            "enabled_scanners": ["coverage", "sonar"],
            "branch_min_percent": 80.0,
            "deps_policy": "zero_critical",
            "default_branch_health": "success",
            "open_pr_health": "partial",
            "ruleset_present": True,
        }
        self.assertEqual(build_admin_dashboard._baseline_text(item), " (main)")
        row = build_admin_dashboard._render_repo_row(item)
        self.assertIn("<td>Prekzursil/example</td>", row)
        page = build_admin_dashboard._render_dashboard_page({"generated_at": "now", "repo_count": 1}, row)
        self.assertIn("Governed repos: 1", page)

        runs = [
            {"event": "push", "conclusion": "success"},
            {"event": "pull_request", "conclusion": "failure"},
        ]
        self.assertEqual(len(build_admin_dashboard._select_runs(runs)), 2)
        self.assertEqual(
            len(
                build_admin_dashboard._select_runs(
                    runs,
                    filter_fn=lambda item: item.get("event") == "pull_request",
                )
            ),
            1,
        )
        self.assertEqual(build_admin_dashboard._run_conclusions(runs), {"success", "failure"})
        self.assertEqual(build_admin_dashboard._compute_health([]), "unknown")
        self.assertEqual(build_admin_dashboard._compute_health(runs), "partial")
        self.assertEqual(
            build_admin_dashboard._compute_health(
                runs,
                filter_fn=lambda item: item.get("event") == "push",
            ),
            "success",
        )

    def test_github_payload_live_health_and_main_cover_token_paths(self) -> None:
        inventory = {"repos": [{"slug": "Prekzursil/example", "profile": "example", "default_branch": "main"}]}
        profile = {
            "enabled_scanners": {"coverage": True},
            "issue_policy": {"mode": "ratchet", "baseline_ref": "main"},
            "coverage": {"min_percent": 100.0, "branch_min_percent": None},
            "deps": {"policy": "zero_critical"},
        }

        with patch.object(
            build_admin_dashboard,
            "load_json_https",
            return_value=({"workflow_runs": [{"event": "pull_request", "conclusion": "success"}]}, {}),
        ) as load_json_mock:
            payload = build_admin_dashboard._github_payload("https://api.github.com/repos/example", "token")
        self.assertEqual(payload["workflow_runs"][0]["event"], "pull_request")
        self.assertEqual(load_json_mock.call_args.kwargs["allowed_hosts"], {"api.github.com"})

        with patch.object(
            build_admin_dashboard,
            "_github_payload",
            side_effect=[
                {"workflow_runs": [{"event": "push", "conclusion": "success"}, {"event": "pull_request", "conclusion": "success"}]},
                [{"id": 1}],
            ],
        ):
            live = build_admin_dashboard._live_health("token", "Prekzursil/example", "main")
        self.assertEqual(live["default_branch_health"], "success")
        self.assertEqual(live["open_pr_health"], "success")
        self.assertTrue(live["ruleset_present"])

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "site"
            args = Namespace(inventory="", output_dir=str(output_dir), assets_dir="")
            with (
                patch.object(build_admin_dashboard, "parse_args", return_value=args),
                patch.object(build_admin_dashboard, "load_inventory", return_value=inventory),
                patch.object(build_admin_dashboard, "load_repo_profile", return_value=profile),
                patch.object(build_admin_dashboard, "write_dashboard") as write_dashboard_mock,
                patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=False),
                patch.object(
                    build_admin_dashboard,
                    "_live_health",
                    return_value={
                        "default_branch_health": "success",
                        "open_pr_health": "unknown",
                        "ruleset_present": False,
                    },
                ),
            ):
                self.assertEqual(build_admin_dashboard.main(), 0)

        payload = write_dashboard_mock.call_args.args[1]
        self.assertEqual(payload["repos"][0]["slug"], "Prekzursil/example")
        self.assertEqual(
            write_dashboard_mock.call_args.kwargs["assets_dir"],
            (Path.cwd() / "docs" / "admin").resolve(),
        )

    def test_main_prefers_explicit_assets_dir_and_fallbacks_without_token(self) -> None:
        inventory = {"repos": [{"slug": "Prekzursil/example", "profile": "example", "default_branch": "main"}]}
        profile = {
            "enabled_scanners": {"coverage": True},
            "issue_policy": {"mode": "ratchet", "baseline_ref": "main"},
            "coverage": {"min_percent": 100.0, "branch_min_percent": None},
            "deps": {"policy": "zero_critical"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets_dir = root / "assets"
            assets_dir.mkdir()
            output_dir = root / "site"
            args = Namespace(inventory="", output_dir=str(output_dir), assets_dir=str(assets_dir))
            with (
                patch.object(build_admin_dashboard, "parse_args", return_value=args),
                patch.object(build_admin_dashboard, "load_inventory", return_value=inventory),
                patch.object(build_admin_dashboard, "load_repo_profile", return_value=profile),
                patch.object(build_admin_dashboard, "write_dashboard") as write_dashboard_mock,
                patch.dict("os.environ", {}, clear=True),
            ):
                self.assertEqual(build_admin_dashboard.main(), 0)

        self.assertEqual(write_dashboard_mock.call_args.kwargs["assets_dir"], assets_dir.resolve())
