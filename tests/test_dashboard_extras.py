"""Tests for the Lane-8 dashboard extras (ruleset drift + rollup gaps)."""

from __future__ import absolute_import

import argparse
import contextlib
import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

from tests.control_plane_support import ROOT

from scripts.quality import build_dashboard_extras, generate_ruleset_payload


def _fake_fetch(responses: Dict[str, Any]):
    """Build a ``Fetcher`` keyed by URL path suffix (query stripped); 404 (None) when unmatched."""

    def fetch(url: str, token: str):
        path = url.split("?", 1)[0]
        for needle, payload in responses.items():
            if path.endswith(needle):
                return payload
        return None

    return fetch


@contextlib.contextmanager
def _tmp_dirs() -> Iterator[Tuple[Path, Path]]:
    """Yield two throwaway directories (committed, regenerated)."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        committed = base / "committed"
        regenerated = base / "regenerated"
        committed.mkdir()
        regenerated.mkdir()
        yield committed, regenerated


class CompareRulesetDirsTests(unittest.TestCase):
    """Unit coverage for the pure directory comparator."""

    def test_in_sync_drift_and_missing_on_both_sides(self) -> None:
        with _tmp_dirs() as (committed, regenerated):
            (committed / "same.json").write_text('{"a": 1}\n', encoding="utf-8")
            (regenerated / "same.json").write_text('{"a": 1}\n', encoding="utf-8")
            (committed / "changed.json").write_text('{"a": 1}\n', encoding="utf-8")
            (regenerated / "changed.json").write_text('{"a": 2}\n', encoding="utf-8")
            (committed / "retired.json").write_text("{}\n", encoding="utf-8")
            (regenerated / "newly.json").write_text("{}\n", encoding="utf-8")

            report = build_dashboard_extras.compare_ruleset_dirs(committed, regenerated)

        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["summary"], {"in_sync": 1, "drift": 1, "missing": 2})
        by_file = {entry["file"]: entry for entry in report["entries"]}
        self.assertEqual(by_file["same.json"]["status"], "in_sync")
        self.assertEqual(by_file["same.json"]["side"], "both")
        self.assertEqual(by_file["changed.json"]["status"], "drift")
        self.assertEqual(by_file["retired.json"]["side"], "committed_only")
        self.assertEqual(by_file["newly.json"]["side"], "generated_only")

    def test_all_in_sync_and_absent_directories(self) -> None:
        with _tmp_dirs() as (committed, regenerated):
            (committed / "x.json").write_text("{}\n", encoding="utf-8")
            (regenerated / "x.json").write_text("{}\n", encoding="utf-8")
            report = build_dashboard_extras.compare_ruleset_dirs(committed, regenerated)
            self.assertEqual(report["status"], "in_sync")

            empty_report = build_dashboard_extras.compare_ruleset_dirs(
                Path("does-not-exist-a"), Path("does-not-exist-b")
            )
        self.assertEqual(empty_report["summary"], {"in_sync": 0, "drift": 0, "missing": 0})
        self.assertEqual(empty_report["status"], "in_sync")


class BuildRulesetDriftPayloadTests(unittest.TestCase):
    """The extras builder regenerates from the real control plane and diffs."""

    def test_real_checkout_is_in_sync(self) -> None:
        report = build_dashboard_extras.build_ruleset_drift_payload(
            inventory_path=ROOT / "inventory" / "repos.yml",
            committed_dir=ROOT / "generated" / "rulesets",
        )
        self.assertEqual(report["status"], "in_sync")
        self.assertGreater(report["summary"]["in_sync"], 0)
        self.assertEqual(report["summary"]["drift"], 0)
        self.assertEqual(report["summary"]["missing"], 0)
        self.assertTrue(report["committed_dir"].replace("\\", "/").endswith("generated/rulesets"))
        self.assertIn("generated_at", report)

    def test_tampered_committed_copy_reports_drift_and_missing(self) -> None:
        with _tmp_dirs() as (committed, _regenerated):
            real = ROOT / "generated" / "rulesets"
            names = sorted(path.name for path in real.glob("*.json"))
            self.assertGreaterEqual(len(names), 3)
            for name in names:
                shutil.copy2(real / name, committed / name)
            # drift: mutate one artifact; missing: delete another
            (committed / names[0]).write_text('{"tampered": true}\n', encoding="utf-8")
            (committed / names[1]).unlink()
            report = build_dashboard_extras.build_ruleset_drift_payload(
                inventory_path=ROOT / "inventory" / "repos.yml",
                committed_dir=committed,
            )
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["summary"]["drift"], 1)
        self.assertEqual(report["summary"]["missing"], 1)


class RollupGapTests(unittest.TestCase):
    """Marker detection + per-repo gap collection (fake REST fetcher)."""

    def test_fetch_or_none_degrades_on_404_and_403_only(self) -> None:
        from urllib.error import HTTPError

        module = build_dashboard_extras
        with mock.patch.object(module, "_github_payload", return_value={"ok": 1}):
            self.assertEqual(module._fetch_or_none("u", "t"), {"ok": 1})
        for code in (403, 404):
            with mock.patch.object(module, "_github_payload", side_effect=HTTPError("u", code, "denied", None, None)):
                self.assertIsNone(module._fetch_or_none("u", "t"), f"HTTP {code} must degrade")
        with (
            mock.patch.object(module, "_github_payload", side_effect=HTTPError("u", 500, "boom", None, None)),
            self.assertRaises(HTTPError),
        ):
            module._fetch_or_none("u", "t")

    def test_pr_has_rollup_marker(self) -> None:
        marker = build_dashboard_extras.ROLLUP_MARKER
        self.assertTrue(build_dashboard_extras.pr_has_rollup([{"body": f"{marker}\n\n| ctx |"}]))
        self.assertFalse(build_dashboard_extras.pr_has_rollup([{"body": "just a review"}]))
        self.assertFalse(build_dashboard_extras.pr_has_rollup([]))

    def test_collect_rollup_gaps_public_repo_with_and_without_comment(self) -> None:
        fetch = _fake_fetch(
            {
                "/repos/Prekzursil/example-full": {"visibility": "public"},
                "/pulls": [
                    {"number": 41, "title": "with marker", "html_url": "u41"},
                    {"number": 42, "title": "without", "html_url": "u42"},
                    "not-a-mapping",
                ],
                "/issues/41/comments": [{"body": f"{build_dashboard_extras.ROLLUP_MARKER} table"}],
                "/issues/42/comments": [{"body": "review"}],
            }
        )
        payload = build_dashboard_extras.collect_rollup_gaps(
            [{"slug": "Prekzursil/example-full"}], "token", fetch=fetch
        )
        self.assertEqual(payload["marker"], build_dashboard_extras.ROLLUP_MARKER)
        row = payload["repos"][0]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["open_count"], 2)
        self.assertEqual(row["gaps"], 1)
        self.assertFalse(row["truncated"])
        self.assertEqual([pr["rollup_comment"] for pr in row["prs"]], ["yes", "no"])

    def test_collect_rollup_gaps_comments_404_marks_unknown(self) -> None:
        fetch = _fake_fetch(
            {
                "/repos/Prekzursil/example": {"visibility": "public"},
                "/pulls": [{"number": 7, "title": "t", "html_url": "u"}],
            }
        )
        payload = build_dashboard_extras.collect_rollup_gaps([{"slug": "Prekzursil/example"}], "t", fetch=fetch)
        self.assertEqual(payload["repos"][0]["prs"][0]["rollup_comment"], "unknown")

    def test_collect_rollup_gaps_truncation_flag(self) -> None:
        pulls = [{"number": i, "title": f"pr{i}", "html_url": "u"} for i in range(3)]
        fetch = _fake_fetch(
            {
                "/repos/Prekzursil/example": {"visibility": "public"},
                "/pulls": pulls,
            }
        )
        payload = build_dashboard_extras.collect_rollup_gaps(
            [{"slug": "Prekzursil/example"}], "t", fetch=fetch, pr_limit=3
        )
        self.assertTrue(payload["repos"][0]["truncated"])

    def test_collect_rollup_gaps_degrades_private_missing_and_bad_pulls(self) -> None:
        fetch = _fake_fetch(
            {
                "/repos/Prekzursil/private": None,  # 404 → unreadable
                "/repos/Prekzursil/internal": {"visibility": "internal"},
                "/repos/Prekzursil/pulls-gone": {"visibility": "public"},
            }
        )
        payload = build_dashboard_extras.collect_rollup_gaps(
            [
                {"slug": "Prekzursil/private"},
                {"slug": "Prekzursil/internal"},
                {"slug": "Prekzursil/pulls-gone"},
                {"slug": ""},
            ],
            "t",
            fetch=fetch,
        )
        rows = payload["repos"]
        self.assertEqual(len(rows), 3)  # empty slug skipped
        self.assertEqual(rows[0]["slug"], "<private>")
        self.assertEqual(rows[0]["status"], "stale")
        self.assertIn("unreadable", rows[0]["reason"])
        self.assertEqual(rows[1]["slug"], "<private>")
        self.assertEqual(rows[2]["slug"], "Prekzursil/pulls-gone")
        self.assertEqual(rows[2]["status"], "stale")
        self.assertIn("404", rows[2]["reason"])

    def test_redact_gaps_payload_masks_pr_titles_and_urls(self) -> None:
        payload = {
            "generated_at": "now",
            "marker": "m",
            "repos": [
                {
                    "slug": "<private>",
                    "visibility": "private",
                    "prs": [{"number": 1, "title": "secret title", "url": "https://secret", "rollup_comment": "no"}],
                },
                {"slug": "Prekzursil/open", "visibility": "public", "prs": []},
            ],
        }
        redacted = build_dashboard_extras._redact_gaps_payload(payload)
        private_pr = redacted["repos"][0]["prs"][0]
        self.assertEqual(private_pr["title"], "<private>")
        self.assertEqual(private_pr["url"], "")
        self.assertEqual(redacted["repos"][1]["slug"], "Prekzursil/open")


class ExtrasCliTests(unittest.TestCase):
    """CLI writes both datasets; token-less runs degrade, never crash."""

    @staticmethod
    def _args(inventory: str, committed: str, output_dir: str, slug_filter: str = "") -> argparse.Namespace:
        return argparse.Namespace(
            inventory=inventory,
            committed_rulesets=committed,
            output_dir=output_dir,
            slug_filter=slug_filter,
        )

    def test_main_writes_both_datasets_from_real_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            committed = Path(tmp) / "committed"
            committed.mkdir()
            for path in (ROOT / "generated" / "rulesets").glob("*.json"):
                shutil.copy2(path, committed / path.name)
            gaps_canned = {
                "generated_at": "now",
                "marker": "m",
                "repos": [
                    {"slug": "Prekzursil/fractal-darkroom", "visibility": "public", "status": "ok", "prs": []},
                    {"slug": "Prekzursil/llm-anthology", "visibility": "public", "status": "ok", "prs": []},
                ],
            }
            output = Path(tmp) / "site-data"
            with (
                mock.patch.dict("os.environ", {"GITHUB_TOKEN": "t", "GH_TOKEN": ""}),
                mock.patch.object(build_dashboard_extras, "collect_rollup_gaps", return_value=gaps_canned) as gaps_mock,
                mock.patch.object(
                    build_dashboard_extras,
                    "parse_args",
                    return_value=self._args(str(ROOT / "inventory" / "repos.yml"), str(committed), str(output)),
                ),
            ):
                build_dashboard_extras.main()
            self.assertEqual(gaps_mock.call_count, 1)
            drift = json.loads((output / "ruleset-drift.json").read_text(encoding="utf-8"))
            self.assertEqual(drift["status"], "in_sync")
            gaps = json.loads((output / "rollup-gaps.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [row["slug"] for row in gaps["repos"]],
                ["Prekzursil/fractal-darkroom", "Prekzursil/llm-anthology"],
            )

    def test_main_slug_filter_narrows_rows_and_no_token_degrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            committed = Path(tmp) / "committed"
            committed.mkdir()
            output = Path(tmp) / "out"
            with (
                mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}),
                mock.patch.object(
                    build_dashboard_extras,
                    "parse_args",
                    return_value=self._args(
                        str(ROOT / "inventory" / "repos.yml"), str(committed), str(output), "fractal-darkroom"
                    ),
                ),
            ):
                build_dashboard_extras.main()
            gaps = json.loads((output / "rollup-gaps.json").read_text(encoding="utf-8"))
            slugs = [row["slug"] for row in gaps["repos"]]
            self.assertEqual(slugs, ["Prekzursil/fractal-darkroom", "Prekzursil/fractal-darkroom-research"])
            self.assertTrue(all(row["status"] == "stale" for row in gaps["repos"]))
            self.assertTrue(all(row["reason"] == "no token" for row in gaps["repos"]))
            # The drift dataset is still computed from the real inventory
            # (empty committed dir → every generated artifact is missing).
            drift = json.loads((output / "ruleset-drift.json").read_text(encoding="utf-8"))
            self.assertEqual(drift["status"], "drift")
            self.assertGreater(drift["summary"]["missing"], 0)
            self.assertEqual(drift["summary"]["in_sync"], 0)


class GenerateRulesetsTests(unittest.TestCase):
    """The shared regeneration entrypoint the drift panel depends on."""

    def test_fleet_regeneration_reproduces_committed_artifacts(self) -> None:
        inventory = generate_ruleset_payload.load_inventory(ROOT / "inventory" / "repos.yml")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rulesets"
            written = generate_ruleset_payload.generate_rulesets(inventory, out, verbose=False)
            committed = ROOT / "generated" / "rulesets"
            self.assertEqual(len(written), len(list(committed.glob("*.json"))))
            for path in written:
                self.assertEqual(
                    path.read_bytes(),
                    (committed / path.name).read_bytes(),
                    f"regenerated {path.name} diverges from the committed artifact",
                )

    def test_subset_regeneration_and_cli_default_inventory(self) -> None:
        inventory = generate_ruleset_payload.load_inventory(ROOT / "inventory" / "repos.yml")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "subset"
            written = generate_ruleset_payload.generate_rulesets(
                inventory, out, repo_slugs=["Prekzursil/fractal-darkroom"], verbose=True
            )
            self.assertEqual([path.name for path in written], ["fractal-darkroom.json"])

            cli_out = Path(tmp) / "cli"
            argv = [
                "generate_ruleset_payload",
                "--inventory",
                str(ROOT / "inventory" / "repos.yml"),
                "--repo-slug",
                "Prekzursil/fractal-darkroom",
                "--output-dir",
                str(cli_out),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(generate_ruleset_payload.main(), 0)
            self.assertTrue((cli_out / "fractal-darkroom.json").is_file())

            # No --inventory → the repo-root default (same invariants as scripts/verify).
            default_out = Path(tmp) / "default"
            with mock.patch.object(sys, "argv", ["generate_ruleset_payload", "--output-dir", str(default_out)]):
                self.assertEqual(generate_ruleset_payload.main(), 0)
            self.assertGreater(len(list(default_out.glob("*.json"))), 1)


class ExtrasCliArgparseTests(unittest.TestCase):
    """The real argparse surface + repo-relative default path resolution."""

    def test_main_with_real_argv_and_relative_defaults(self) -> None:
        from tests.workspace_isolation import isolated_cwd

        with (
            isolated_cwd() as cwd,
            mock.patch.dict("os.environ", {"GITHUB_TOKEN": "", "GH_TOKEN": ""}),
            mock.patch.object(
                sys,
                "argv",
                ["build_dashboard_extras", "--slug-filter", "fractal-darkroom", "--output-dir", "out"],
            ),
        ):
            build_dashboard_extras.main()
            # Read INSIDE the context: isolated_cwd deletes the directory on exit.
            gaps = json.loads((cwd / "out" / "rollup-gaps.json").read_text(encoding="utf-8"))
            drift = json.loads((cwd / "out" / "ruleset-drift.json").read_text(encoding="utf-8"))
        self.assertEqual(
                [row["slug"] for row in gaps["repos"]],
                ["Prekzursil/fractal-darkroom", "Prekzursil/fractal-darkroom-research"],
            )
        self.assertEqual(drift["status"], "in_sync")


if __name__ == "__main__":
    unittest.main()
