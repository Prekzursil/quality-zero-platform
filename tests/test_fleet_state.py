"""Live fleet state: the bypass-stale and drift-stuck detectors finally get input."""

from __future__ import absolute_import

import datetime as dt
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, List
from unittest.mock import MagicMock, patch

from scripts.quality import alert_dispatch, fleet_state

NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC)


def _gh(stdout: Any, returncode: int = 0) -> MagicMock:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return MagicMock(return_value=subprocess.CompletedProcess(["gh"], returncode, text, "boom\n"))


class BypassFollowupTests(unittest.TestCase):
    """Open break-glass follow-up issues on the platform."""

    def test_open_followups_become_detector_input(self) -> None:
        """Typed for the detector: an aware datetime, never the raw API string."""
        runner = _gh([{"number": 7, "title": "break-glass follow-up: o/r#3", "createdAt": "2026-10-01T09:00:00Z"}, 5])
        (record,) = fleet_state.bypass_followups("o/qzp", runner=runner)
        self.assertEqual(record["slug"], "o/qzp")
        self.assertEqual(record["issue_number"], 7)
        self.assertEqual(record["opened_at"], dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC))
        argv = runner.call_args.args[0]
        self.assertEqual(argv[argv.index("--label") + 1], fleet_state.BYPASS_FOLLOWUP_LABEL)
        self.assertEqual(argv[argv.index("--state") + 1], "open")
        triggers = alert_dispatch._bypass_issue_triggers([record], now=NOW)
        self.assertEqual([t.subject for t in triggers], ["o/qzp#7"])

    def test_a_failed_or_malformed_listing_raises_instead_of_reading_as_none(self) -> None:
        """An unreadable source is UNKNOWN; returning [] would be a silent all-clear."""
        for runner, expected in (
            (_gh("", returncode=1), "exited 1: boom"),
            (_gh("{not json"), "unparseable JSON"),
            (_gh({"items": []}), "returned dict, expected a list"),
        ):
            with self.subTest(expected=expected), self.assertRaisesRegex(fleet_state.FleetStateError, expected):
                fleet_state.bypass_followups("o/qzp", runner=runner)


class DriftSyncPrTests(unittest.TestCase):
    """Open drift-sync PRs across the owner's repositories."""

    def _prs(self) -> List[dict]:
        return [
            {
                "number": 12,
                "repository": {"name": "r", "nameWithOwner": "o/r"},
                "createdAt": "2026-10-10T00:00:00Z",
                "url": "https://github.com/o/r/pull/12",
            }
        ]

    def test_the_search_is_scoped_to_the_drift_branch_prefix(self) -> None:
        """`head:` matches branch names that START with the prefix apply_drift_pr uses."""
        runner = _gh(self._prs())
        (record,) = fleet_state.drift_sync_prs("o", runner=runner)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[1:3], ["search", "prs"])
        self.assertEqual(argv[argv.index("--owner") + 1], "o")
        self.assertEqual(argv[-1], f"head:{fleet_state.DRIFT_BRANCH_PREFIX}")
        self.assertEqual(fleet_state.DRIFT_BRANCH_PREFIX, "quality-zero/drift-sync")
        self.assertEqual(record["slug"], "o/r")
        self.assertEqual(record["pr_number"], 12)
        triggers = alert_dispatch._drift_pr_triggers([record], now=NOW)
        self.assertEqual([t.subject for t in triggers], ["o/r#12"])

    def test_the_fleet_read_pat_widens_the_search_and_is_named_in_the_scope(self) -> None:
        """Private members' drift PRs are only visible to the fleet-read PAT; GH_TOKEN is swapped for this call alone."""
        runner = _gh(self._prs())
        environ = {"GH_TOKEN": "workflow", fleet_state.FLEET_READ_PAT_ENV: " pat ", "PATH": "/bin"}
        fleet_state.drift_sync_prs("o", runner=runner, environ=environ)
        self.assertEqual(runner.call_args.kwargs["env"]["GH_TOKEN"], "pat")
        self.assertEqual(environ["GH_TOKEN"], "workflow")
        self.assertEqual(fleet_state.drift_search_scope(environ), "fleet-wide (fleet-read token)")
        for bare in ({}, {fleet_state.FLEET_READ_PAT_ENV: "  "}):
            with self.subTest(environ=bare):
                runner = _gh(self._prs())
                fleet_state.drift_sync_prs("o", runner=runner, environ=bare)
                self.assertNotIn("env", runner.call_args.kwargs)
                self.assertIn("public repositories only", fleet_state.drift_search_scope(bare))
        self.assertIn("Prekzursil/PrimariRo", fleet_state.PRIVATE_SLUGS)

    def test_a_pr_without_repository_data_keeps_an_empty_slug(self) -> None:
        """Malformed search rows degrade one field, not the whole state."""
        runner = _gh([{"number": 1, "createdAt": "2026-10-23T00:00:00Z"}])
        (record,) = fleet_state.drift_sync_prs("o", runner=runner)
        self.assertEqual((record["slug"], record["pr_url"]), ("", ""))

    def test_jsonl_carries_iso_timestamps_for_the_drift_page(self) -> None:
        """The dashboard page reads the same records from a JSONL file."""
        (record,) = fleet_state.drift_sync_prs("o", runner=_gh(self._prs()))
        (line,) = fleet_state.to_jsonl([record]).splitlines()
        self.assertEqual(json.loads(line)["opened_at"], "2026-10-10T00:00:00+00:00")
        self.assertEqual(json.loads(line)["status"], "open")


class CliTests(unittest.TestCase):
    """The dashboard build writes the drift JSONL; an unreadable source fails it."""

    def test_the_cli_writes_the_live_drift_jsonl(self) -> None:
        """One line per open drift-sync PR, into a directory it creates."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audit" / "drift-sync.jsonl"
            stdout = io.StringIO()
            with redirect_stdout(stdout), patch.dict(fleet_state.os.environ, {fleet_state.FLEET_READ_PAT_ENV: ""}):
                code = fleet_state.main(
                    ["--owner", "o", "--drift-jsonl", str(out)], runner=_gh(DriftSyncPrTests()._prs())
                )
            self.assertEqual(code, 0)
            self.assertEqual(len(out.read_text(encoding="utf-8").splitlines()), 1)
        self.assertIn("1 open drift-sync PR(s)", stdout.getvalue())
        self.assertIn("scope: public repositories only", stdout.getvalue())

    def test_an_unreadable_source_exits_1_and_writes_nothing(self) -> None:
        """No file means the drift page says "No data", never "No drift entries"."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "drift.jsonl"
            err = io.StringIO()
            with redirect_stderr(err):
                code = fleet_state.main(["--owner", "o", "--drift-jsonl", str(out)], runner=_gh("", returncode=1))
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())
        self.assertIn("fleet_state: `gh search prs` exited 1", err.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
