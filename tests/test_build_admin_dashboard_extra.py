"""Test build admin dashboard extra."""

from __future__ import absolute_import

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from typing import Any, List, Mapping
from unittest.mock import patch
from urllib.error import HTTPError

from scripts.quality import build_admin_dashboard


def _http_error(code: int, reason: str) -> HTTPError:
    """Build an ``HTTPError`` like ``security_helpers._read_json_response`` raises."""
    return HTTPError(
        "https://api.github.com/repos/Prekzursil/agent-skills-toolchain",
        code,
        reason,
        hdrs=None,
        fp=None,
    )


class BuildAdminDashboardExtraTests(unittest.TestCase):
    """Build Admin Dashboard Extra Tests."""

    def test_helper_rendering_and_health_helpers_cover_non_default_paths(self) -> None:
        """Cover helper rendering and health helpers cover non default paths."""
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
        self.assertIn("Governed repos:", page)
        self.assertIn("\n    1.\n", page)

        runs: List[Mapping[str, Any]] = [
            {"event": "push", "name": "CI", "conclusion": "success"},
            {"event": "pull_request", "name": "Lint", "conclusion": "failure"},
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
        self.assertEqual(build_admin_dashboard._compute_health([]), "unknown")
        self.assertEqual(build_admin_dashboard._compute_health(runs), "partial")
        self.assertEqual(
            build_admin_dashboard._compute_health(
                runs,
                filter_fn=lambda item: item.get("event") == "push",
            ),
            "success",
        )

    def test_github_payload_and_live_health_cover_token_paths(self) -> None:
        """Cover github payload and live health cover token paths."""
        with patch.object(
            build_admin_dashboard,
            "load_json_https",
            return_value=(
                {"workflow_runs": [{"event": "pull_request", "conclusion": "success"}]},
                {},
            ),
        ) as load_json_mock:
            payload = build_admin_dashboard._github_payload("https://api.github.com/repos/example", "token")
        self.assertEqual(payload["workflow_runs"][0]["event"], "pull_request")
        self.assertEqual(load_json_mock.call_args.kwargs["allowed_hosts"], {"api.github.com"})

        pr_runs = {"workflow_runs": [{"head_sha": "abc", "name": "CI", "status": "completed", "conclusion": "success"}]}
        with (
            patch.object(
                build_admin_dashboard,
                "_github_payload",
                side_effect=[
                    {
                        "workflow_runs": [
                            {"event": "push", "conclusion": "success"},
                            {"event": "pull_request", "conclusion": "success"},
                        ]
                    },
                    [{"id": 1}],
                    # repo metadata fetch added in the Phase-5 redaction wiring
                    # (see _live_health) — third call returns visibility.
                    {"visibility": "public"},
                ],
            ),
            # open PRs, then the pull_request runs on their heads (403/404-tolerant)
            patch.object(
                build_admin_dashboard,
                "_github_payload_or_degraded",
                side_effect=[[{"number": 4, "head": {"sha": "abc"}}], pr_runs],
            ),
        ):
            live = build_admin_dashboard._live_health("token", "Prekzursil/example", "main")
        self.assertEqual(live["default_branch_health"], "success")
        self.assertEqual(live["open_pr_health"], "success")
        self.assertTrue(live["ruleset_present"])
        self.assertEqual(live["visibility"], "public")

    def test_live_health_treats_404_resources_as_absent_and_redacts(self) -> None:
        """A private governed repo with no visible resources must not abort the build.

        Regression test for the Publish Admin Dashboard failure: a
        newly-registered PRIVATE repo (agent-skills-toolchain — no Pages
        site, unreadable by the workflow token) 404s on every per-repo
        GitHub fetch. Each 404 must be treated as "resource absent" —
        health ``unknown``, no ruleset — and the repo marked ``private``
        so ``redact_private_repos`` masks the slug instead of leaking it.
        """
        with patch.object(
            build_admin_dashboard,
            "_github_payload",
            side_effect=[
                _http_error(404, "Not Found"),  # actions/runs
                _http_error(404, "Not Found"),  # rulesets
                _http_error(404, "Not Found"),  # repo metadata
            ],
        ) as payload_mock:
            live = build_admin_dashboard._live_health("token", "Prekzursil/agent-skills-toolchain", "main")
        self.assertEqual(payload_mock.call_count, 3)
        self.assertEqual(live["default_branch_health"], "unknown")
        self.assertEqual(live["open_pr_health"], "unknown")
        self.assertFalse(live["ruleset_present"])
        self.assertEqual(live["visibility"], "private")

    def test_live_health_reraises_non_404_http_errors(self) -> None:
        """Only 404 is tolerated; rate-limits/server errors still abort loudly."""
        with (
            patch.object(
                build_admin_dashboard,
                "_github_payload",
                side_effect=_http_error(403, "rate limited"),
            ),
            self.assertRaises(HTTPError) as caught,
        ):
            build_admin_dashboard._live_health("token", "Prekzursil/example", "main")
        self.assertEqual(caught.exception.code, 403)

    def test_live_health_partial_404_keeps_reachable_data_and_public_default(self) -> None:
        """A 404 on one resource degrades only that field; unusable metadata stays public."""
        runs_payload = {"workflow_runs": [{"event": "push", "conclusion": "success"}]}
        with (
            patch.object(
                build_admin_dashboard,
                "_github_payload",
                side_effect=[
                    runs_payload,  # actions/runs reachable
                    _http_error(404, "Not Found"),  # rulesets absent
                    ["unexpected"],  # metadata reachable but not a dict
                ],
            ),
            patch.object(build_admin_dashboard, "_github_payload_or_degraded", return_value=[]),  # no open PR
        ):
            live = build_admin_dashboard._live_health("token", "Prekzursil/tracelines", "main")
        self.assertEqual(live["default_branch_health"], "success")
        self.assertEqual(live["open_pr_health"], "none")
        self.assertFalse(live["ruleset_present"])
        self.assertEqual(live["visibility"], "public")

        with (
            patch.object(
                build_admin_dashboard,
                "_github_payload",
                side_effect=[
                    _http_error(404, "Not Found"),  # actions/runs absent
                    [{"id": 1}],  # rulesets reachable
                    {"visibility": "   "},  # metadata dict with blank visibility
                ],
            ),
            patch.object(build_admin_dashboard, "_github_payload_or_degraded", return_value=None),  # pulls denied
        ):
            live = build_admin_dashboard._live_health("token", "Prekzursil/tracelines", "main")
        self.assertEqual(live["default_branch_health"], "unknown")
        self.assertEqual(live["open_pr_health"], "unknown")
        self.assertTrue(live["ruleset_present"])
        self.assertEqual(live["visibility"], "public")

    def test_health_reads_the_newest_decisive_run_of_each_workflow(self) -> None:
        """A fixed failure, a cancelled run, or a run in flight no longer paints a green branch red."""

        def run(name: str, conclusion: str, status: str = "completed", minute: int = 0, sha: str = "a") -> dict:
            return {
                "name": name,
                "conclusion": conclusion,
                "status": status,
                "created_at": f"2026-10-24T10:{minute:02d}:00Z",
                "head_sha": sha,
            }

        health = build_admin_dashboard._compute_health
        recovered = [run("CI", "failure", minute=1), run("CI", "success", minute=2)]
        self.assertEqual(health(recovered), "success")
        cancelled = [run("CI", "success", minute=1), run("CI", "cancelled", minute=2), run("Lint", "skipped")]
        self.assertEqual(health(cancelled), "success")
        broken = [run("CI", "success", minute=2), run("Lint", "failure", minute=1)]
        self.assertEqual(health(broken), "partial")
        in_flight = [run("CI", "", status="in_progress", minute=2), run("CI", "success", minute=1)]
        self.assertEqual(health(in_flight), "success")
        self.assertEqual(health([run("CI", "", status="queued")]), "pending")
        self.assertEqual(health([run("CI", "neutral")]), "unknown")
        per_head = [run("CI", "success", minute=2, sha="new"), run("CI", "failure", minute=1, sha="old")]
        self.assertEqual(health(per_head), "success")
        self.assertEqual(health(per_head, per_commit=True), "partial")

    def test_open_pr_health_reads_each_open_heads_runs(self) -> None:
        """THE bug: PR health filtered default-branch runs for PR events, so every repo read unknown."""

        def live(*responses: Any) -> str:
            with patch.object(build_admin_dashboard, "_github_payload_or_degraded", side_effect=list(responses)):
                return build_admin_dashboard._open_pr_health("token", "Prekzursil/example")

        pulls = [{"number": 1, "head": {"sha": "h1"}}, {"number": 2, "head": {"sha": "h2"}}, {"head": None}]
        green = {"workflow_runs": [{"head_sha": "h1", "name": "CI", "conclusion": "success"}]}
        self.assertEqual(live(pulls, green), "success")
        red = {
            "workflow_runs": [
                {"head_sha": "h2", "name": "CI", "conclusion": "failure"},
                {"head_sha": "closed-pr", "name": "CI", "conclusion": "success"},
            ]
        }
        self.assertEqual(live(pulls, red), "partial")
        self.assertEqual(live(pulls, {"workflow_runs": []}), "unknown")
        self.assertEqual(live(pulls, ["not", "a", "dict"]), "unknown")
        self.assertEqual(live([]), "none")
        self.assertEqual(live({"message": "not a list"}), "unknown")
        self.assertEqual(live(None), "unknown")  # 403/404: degraded to this field only

    def test_a_denied_pull_request_feed_degrades_only_open_pr_health(self) -> None:
        """A 403 on /pulls (metadata readable, PRs denied) must not fail the whole dashboard build."""
        with patch.object(build_admin_dashboard, "load_json_https", side_effect=_http_error(403, "Forbidden")):
            self.assertEqual(build_admin_dashboard._open_pr_health("token", "Prekzursil/example"), "unknown")

    def test_main_uses_live_health_when_token_is_present(self) -> None:
        """Cover main uses live health when token is present."""
        inventory = {
            "repos": [
                {
                    "slug": "Prekzursil/example",
                    "profile": "example",
                    "default_branch": "main",
                }
            ]
        }
        profile = {
            "enabled_scanners": {"coverage": True},
            "issue_policy": {"mode": "ratchet", "baseline_ref": "main"},
            "coverage": {"min_percent": 100.0, "branch_min_percent": None},
            "deps": {"policy": "zero_critical"},
        }
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
        """Cover main prefers explicit assets dir and fallbacks without token."""
        inventory = {
            "repos": [
                {
                    "slug": "Prekzursil/example",
                    "profile": "example",
                    "default_branch": "main",
                }
            ]
        }
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
