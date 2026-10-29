"""Tests for the Lane-8 gate-status additions to build_admin_dashboard."""

from __future__ import absolute_import

import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError

from scripts.quality import build_admin_dashboard


def _contexts_payload(*runs: Dict[str, str]) -> Dict[str, Any]:
    return {
        "check_runs": [{"name": name, "status": status, "conclusion": conclusion} for name, status, conclusion in runs]
    }


class DegradedFetchTests(unittest.TestCase):
    """The gate panel's 404/403-tolerant fetcher (stale row, never a failed build)."""

    def test_404_and_403_degrade_to_none_while_other_errors_propagate(self) -> None:
        module = build_admin_dashboard
        with mock.patch.object(module, "load_json_https", return_value=({"ok": True}, {})):
            self.assertEqual(module._github_payload_or_degraded("https://api.github.com/x", "t"), {"ok": True})
        for code in (403, 404):
            with (
                mock.patch.object(
                    module,
                    "load_json_https",
                    side_effect=HTTPError("https://api.github.com/x", code, "denied", None, None),
                ),
            ):
                self.assertIsNone(
                    module._github_payload_or_degraded("https://api.github.com/x", "t"), f"HTTP {code} must degrade"
                )
        with (
            mock.patch.object(
                module,
                "load_json_https",
                side_effect=HTTPError("https://api.github.com/x", 502, "bad", None, None),
            ) as fetch,
            mock.patch.object(module.time, "sleep") as sleep,
            self.assertRaises(HTTPError),
        ):
            module._github_payload_or_degraded("https://api.github.com/x", "t")
        self.assertEqual(fetch.call_count, 3)  # a persistent 502 still propagates, after the retries
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 5.0])

    def test_a_transient_5xx_is_retried_and_other_errors_are_not(self) -> None:
        """One 502 failed the whole hourly publish on 2026-10-26; the next run was green."""
        module = build_admin_dashboard
        bad = HTTPError("https://api.github.com/x", 503, "busy", None, None)
        with (
            mock.patch.object(module, "load_json_https", side_effect=[bad, ({"ok": True}, {"link": ""})]) as fetch,
            mock.patch.object(module.time, "sleep") as sleep,
        ):
            self.assertEqual(module._github_payload("https://api.github.com/x", "t"), {"ok": True})
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(2.0)
        for code in (500, 401):
            with (
                mock.patch.object(
                    module, "load_json_https", side_effect=HTTPError("https://api.github.com/x", code, "no", None, None)
                ) as fetch,
                mock.patch.object(module.time, "sleep") as sleep,
                self.assertRaises(HTTPError),
            ):
                module._github_payload("https://api.github.com/x", "t")
            self.assertEqual((fetch.call_count, sleep.call_count), (1, 0))


class NextPageUrlTests(unittest.TestCase):
    """Link-header pagination parsing."""

    def test_extracts_next_and_ignores_other_rels(self) -> None:
        module = build_admin_dashboard
        header = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=5>; rel="last"'
        self.assertEqual(module._next_page_url({"link": header}), "https://api.github.com/x?page=2")
        self.assertEqual(module._next_page_url({"link": '<https://api.github.com/x?page=5>; rel="last"'}), "")
        self.assertEqual(module._next_page_url({}), "")


class CollectAllCheckRunsTests(unittest.TestCase):
    """The multi-page check-runs walk (re-run-flooded commits hide contexts on page 2)."""

    def test_walks_all_pages_until_no_next_link(self) -> None:
        pages = {
            "page=1": (
                {"check_runs": [{"name": "a"}, {"name": "b"}]},
                {"link": '<https://api.github.com/x?page=2>; rel="next"'},
            ),
            "page=2": ({"check_runs": [{"name": "c"}]}, {}),
        }

        def fake(url: str, token: str):
            for needle, result in pages.items():
                if needle in url:
                    return result
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            runs, partial = build_admin_dashboard._collect_all_check_runs("https://api.github.com/x?page=1", "t")
        self.assertEqual([run["name"] for run in runs], ["a", "b", "c"])
        self.assertFalse(partial)

    def test_denial_mid_walk_marks_partial_and_keeps_prior_pages(self) -> None:
        pages = {
            "page=1": ({"check_runs": [{"name": "a"}]}, {"link": '<https://api.github.com/x?page=2>; rel="next"'}),
            "page=2": (None, None),
        }

        def fake(url: str, token: str):
            for needle, result in pages.items():
                if needle in url:
                    return result
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            runs, partial = build_admin_dashboard._collect_all_check_runs("https://api.github.com/x?page=1", "t")
        self.assertEqual([run["name"] for run in runs], ["a"])
        self.assertTrue(partial)

    def test_first_page_denial_returns_nothing_partial(self) -> None:
        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", return_value=(None, None)):
            runs, partial = build_admin_dashboard._collect_all_check_runs("https://api.github.com/x", "t")
        self.assertEqual(runs, [])
        self.assertTrue(partial)

    def test_page_cap_marks_partial(self) -> None:
        # Every page points at the next one: the walk must stop at the cap.
        linked = ({"check_runs": [{"name": "a"}]}, {"link": '<https://api.github.com/x?page=next>; rel="next"'})

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", return_value=linked):
            runs, partial = build_admin_dashboard._collect_all_check_runs("https://api.github.com/x", "t")
        self.assertEqual(len(runs), build_admin_dashboard.MAX_CHECK_RUN_PAGES)
        self.assertTrue(partial)


class GateStatusFromContextTests(unittest.TestCase):
    """Per-context verdict semantics (check-runs + legacy statuses)."""

    def test_missing_pr_only_pending_fail_and_pass(self) -> None:
        contexts = {
            "quality / quality": {"state": "completed", "conclusion": "success", "source": "check_run"},
            "test (ubuntu-latest, 3.12)": {"state": "completed", "conclusion": "failure", "source": "check_run"},
            "Dependency Alerts": {"state": "pending", "conclusion": "pending", "source": "status"},
            "coverage / Coverage 100 Gate": {"state": "queued", "conclusion": "", "source": "check_run"},
        }
        module = build_admin_dashboard
        self.assertEqual(module._gate_status_from_context("quality", contexts), "pass")  # unique suffix match
        self.assertEqual(module._gate_status_from_context("test (ubuntu-latest, 3.12)", contexts), "fail")
        self.assertEqual(module._gate_status_from_context("Dependency Alerts", contexts), "pending")
        self.assertEqual(module._gate_status_from_context("Coverage 100 Gate", contexts), "pending")
        self.assertEqual(module._gate_status_from_context("Never Emitted", contexts), "missing")
        self.assertEqual(module._gate_status_from_context("CodeQL", contexts), "pr_only")

    def test_legacy_status_failure(self) -> None:
        contexts = {"codecov/project": {"state": "failure", "conclusion": "failure", "source": "status"}}
        self.assertEqual(build_admin_dashboard._gate_status_from_context("codecov/project", contexts), "fail")


class AggregateGateStatusTests(unittest.TestCase):
    """fail > pending > missing > pass reduction."""

    def test_precedence_and_empty(self) -> None:
        module = build_admin_dashboard
        self.assertEqual(module._aggregate_gate_status([]), "unknown")
        self.assertEqual(module._aggregate_gate_status([{"status": "pass"}]), "pass")
        self.assertEqual(module._aggregate_gate_status([{"status": "pass"}, {"status": "missing"}]), "missing")
        self.assertEqual(module._aggregate_gate_status([{"status": "missing"}, {"status": "pending"}]), "pending")
        self.assertEqual(module._aggregate_gate_status([{"status": "pending"}, {"status": "fail"}]), "fail")
        self.assertEqual(module._aggregate_gate_status([{"status": "weird"}]), "unknown")


PROFILE_WITH_REQUIRED = {
    "required_contexts": {
        "target": ["quality / quality", "test (ubuntu-latest, 3.12)", "CodeQL"],
    }
}


def _head_payload() -> Dict[str, Any]:
    return {
        "sha": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",
        "commit": {"committer": {"date": "2026-10-21T08:30:00Z"}},
    }


class LaneGatesTests(unittest.TestCase):
    """404/403-tolerant measurement of required contexts on the default-branch HEAD."""

    def test_profile_without_required_contexts_notes_the_error(self) -> None:
        gates = build_admin_dashboard._lane_gates("t", "Prekzursil/x", "main", {"required_contexts": None})
        self.assertEqual(gates["gate_status"], "unknown")
        self.assertEqual(gates["gates"], [])
        self.assertIn("required_contexts", gates["gate_note"])

    def test_head_404_degrades_to_stale(self) -> None:
        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", return_value=(None, None)):
            gates = build_admin_dashboard._lane_gates("t", "Prekzursil/private", "main", PROFILE_WITH_REQUIRED)
        self.assertEqual(gates["gate_status"], "stale")
        self.assertIn("default-branch head unavailable", gates["gate_note"])

    def test_denied_feeds_degrade_to_stale_with_head_metadata(self) -> None:
        responses = {
            "/commits/main": (_head_payload(), {}),
            "/check-runs": (None, None),
            "/status": (None, None),
        }

        def fake(url: str, token: str):
            path = url.split("?", 1)[0]
            for needle, result in responses.items():
                if path.endswith(needle):
                    return result
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            gates = build_admin_dashboard._lane_gates("t", "Prekzursil/private-readable", "main", PROFILE_WITH_REQUIRED)
        self.assertEqual(gates["gate_status"], "stale")
        self.assertIn("feeds unavailable", gates["gate_note"])
        self.assertEqual(gates["gate_head_sha"], "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b")

    def test_measured_gates_carry_head_metadata(self) -> None:
        responses = {
            "/commits/main": (_head_payload(), {}),
            "/check-runs": (
                _contexts_payload(
                    ("quality / quality", "completed", "success"),
                    ("test (ubuntu-latest, 3.12)", "completed", "failure"),
                ),
                {},
            ),
            "/status": ({"statuses": []}, {}),
        }

        def fake(url: str, token: str):
            path = url.split("?", 1)[0]
            for needle, result in responses.items():
                if path.endswith(needle):
                    return result
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            gates = build_admin_dashboard._lane_gates("t", "Prekzursil/x", "main", PROFILE_WITH_REQUIRED)
        self.assertEqual(gates["gate_status"], "fail")
        self.assertEqual(gates["gate_head_sha"], "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b")
        self.assertEqual(gates["gate_head_date"], "2026-10-21T08:30:00Z")
        self.assertEqual(gates["gate_note"], "")
        self.assertEqual(
            [(g["context"], g["status"]) for g in gates["gates"]],
            [
                ("quality / quality", "pass"),
                ("test (ubuntu-latest, 3.12)", "fail"),
                ("CodeQL", "pr_only"),
            ],
        )

    def test_partial_feed_sets_note_but_still_evaluates(self) -> None:
        responses = {
            "/commits/main": ({"sha": "abc", "commit": {}}, {}),
            "/check-runs": (
                {"check_runs": [{"name": "quality / quality", "status": "completed", "conclusion": "success"}]},
                {"link": '<https://api.github.com/y?page=2>; rel="next"'},
            ),
            "page=2": (None, None),
            "/status": ({"statuses": []}, {}),
        }

        def fake(url: str, token: str):
            for needle, result in responses.items():
                if needle in url:
                    return result
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            gates = build_admin_dashboard._lane_gates(
                "t", "Prekzursil/x", "main", {"required_contexts": {"target": ["quality / quality"]}}
            )
        self.assertEqual(gates["gate_status"], "pass")
        self.assertIn("partial check-runs feed", gates["gate_note"])

    def test_head_date_missing_when_committer_block_absent(self) -> None:
        responses = {
            "/commits/main": ({"sha": "abc", "commit": {"message": "no committer block"}}, {}),
            "/check-runs": (_contexts_payload(("quality / quality", "completed", "success")), {}),
            "/status": ({"statuses": []}, {}),
        }

        def fake(url: str, token: str):
            path = url.split("?", 1)[0]
            for needle, result in responses.items():
                if path.endswith(needle):
                    return result
            raise AssertionError(f"unexpected url {url}")

        with mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake):
            gates = build_admin_dashboard._lane_gates(
                "t", "Prekzursil/x", "main", {"required_contexts": {"target": ["quality / quality"]}}
            )
        self.assertEqual(gates["gate_head_date"], "")
        self.assertEqual(gates["gate_status"], "pass")


class LiveHealthGateWiringTests(unittest.TestCase):
    """_live_health merges gate fields when a profile is supplied."""

    def test_live_health_without_profile_has_no_gate_fields(self) -> None:
        with (
            mock.patch.object(build_admin_dashboard, "_github_payload_or_none") as fake,
            mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", return_value=([], {})),
        ):
            fake.return_value = {"workflow_runs": [], "visibility": "public"}
            health = build_admin_dashboard._live_health("t", "Prekzursil/x", "main")
        self.assertNotIn("gate_status", health)
        self.assertEqual(health["default_branch_health"], "unknown")

    def test_live_health_with_profile_measures_gates(self) -> None:
        responses = {
            "/actions/runs": ({"workflow_runs": []}, {}),
            "/rulesets": ([], {}),
            "/repos/Prekzursil/x": ({"visibility": "public"}, {}),
            "/pulls": ([], {}),
            "/commits/main": (_head_payload(), {}),
            "/check-runs": (_contexts_payload(("quality / quality", "completed", "success")), {}),
            "/status": ({"statuses": []}, {}),
        }

        def fake(url: str, token: str):
            path = url.split("?", 1)[0]
            for needle, result in responses.items():
                if path.endswith(needle):
                    return result
            raise AssertionError(f"unexpected url {url}")

        def fake_or_none(url: str, token: str):
            payload, _headers = fake(url, token)
            return payload

        with (
            mock.patch.object(build_admin_dashboard, "_github_fetch_degraded", side_effect=fake),
            mock.patch.object(build_admin_dashboard, "_github_payload_or_none", side_effect=fake_or_none),
        ):
            health = build_admin_dashboard._live_health(
                "t",
                "Prekzursil/x",
                "main",
                profile={"required_contexts": {"target": ["quality / quality"]}},
            )
        self.assertEqual(health["gate_status"], "pass")
        self.assertEqual(health["visibility"], "public")

    def test_payload_rows_carry_gate_defaults_and_measured_values(self) -> None:
        payload = build_admin_dashboard.build_dashboard_payload(
            inventory={"repos": [{"slug": "a", "profile": "p", "rollout": "observe"}]},
            profiles={"a": {}},
            live={},
        )
        self.assertEqual(payload["repos"][0]["gate_status"], "unknown")
        self.assertEqual(payload["repos"][0]["gates"], [])
        self.assertEqual(payload["repos"][0]["gate_head_sha"], "")
        self.assertEqual(payload["repos"][0]["gate_note"], "")

        measured = build_admin_dashboard.build_dashboard_payload(
            inventory={"repos": [{"slug": "a", "profile": "p", "rollout": "observe"}]},
            profiles={"a": {}},
            live={
                "a": {
                    "visibility": "public",
                    "gate_status": "fail",
                    "gates": [{"context": "quality / quality", "status": "fail"}],
                    "gate_head_sha": "s",
                    "gate_head_date": "d",
                    "gate_note": "",
                }
            },
        )
        self.assertEqual(measured["repos"][0]["gate_status"], "fail")
        self.assertEqual(measured["repos"][0]["gates"][0]["context"], "quality / quality")


class PanelsAssetCopyTests(unittest.TestCase):
    """The site build must ship panels.mjs next to app.js (Lane 8)."""

    def test_write_dashboard_copies_panels_module(self) -> None:
        payload = {"generated_at": "now", "repo_count": 0, "repos": []}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            assets = root / "assets"
            assets.mkdir()
            for name in ("index.html", "styles.css", "app.js", "panels.mjs"):
                (assets / name).write_text("/*fixture*/\n", encoding="utf-8")
            out = root / "site"
            build_admin_dashboard.write_dashboard(out, payload, assets_dir=assets)
            for name in ("index.html", "styles.css", "app.js", "panels.mjs"):
                self.assertTrue((out / name).is_file(), f"{name} must ship with the site")
            self.assertTrue((out / "data" / "dashboard.json").is_file())


if __name__ == "__main__":
    unittest.main()
