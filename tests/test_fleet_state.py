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
from typing import Any
from unittest.mock import MagicMock, patch

from scripts.quality import alert_dispatch, fleet_state

NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC)
DRIFT_HEAD = f"{fleet_state.DRIFT_BRANCH_PREFIX}/18276616"
PRIVATE = "Prekzursil/PrimariRo"


def _gh(stdout: Any, returncode: int = 0) -> MagicMock:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return MagicMock(return_value=subprocess.CompletedProcess(["gh"], returncode, text, "boom\n"))


def _listings(*payloads: Any) -> MagicMock:
    """One ``gh pr list`` result per repository, in order (an int payload is a failing exit code)."""
    return MagicMock(
        side_effect=[
            subprocess.CompletedProcess(["gh"], payload, "", "boom\n")
            if isinstance(payload, int)
            else subprocess.CompletedProcess(["gh"], 0, json.dumps(payload), "")
            for payload in payloads
        ]
    )


def _pr(number: int, head: str = DRIFT_HEAD, repo: str = "o/r", *, fork: bool = False) -> dict:
    return {
        "number": number,
        "headRefName": head,
        "isCrossRepository": fork,
        "createdAt": "2026-10-10T00:00:00Z",
        "url": f"https://github.com/{repo}/pull/{number}",
    }


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
        self.assertEqual(argv[argv.index("--limit") + 1], str(fleet_state.LIST_LIMIT + 1))
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

    def test_a_list_that_reached_its_limit_is_unknown_not_complete(self) -> None:
        """Review finding: reconciling a truncated list would close the alerts of subjects past the cap."""
        issue = {"number": 1, "createdAt": "2026-10-01T09:00:00Z"}
        complete = [issue] * fleet_state.LIST_LIMIT
        self.assertEqual(len(fleet_state.bypass_followups("o/qzp", runner=_gh(complete))), fleet_state.LIST_LIMIT)
        with self.assertRaisesRegex(fleet_state.FleetStateError, "may be truncated"):
            fleet_state.bypass_followups("o/qzp", runner=_gh(complete + [issue]))


class FleetRosterTests(unittest.TestCase):
    """Only the declared fleet is fleet state."""

    def test_the_roster_is_the_inventory(self) -> None:
        """Sorted, de-duplicated slugs; rows without one are skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "inventory" / "repos.yml"
            inventory.parent.mkdir()
            inventory.write_text(
                "version: 1\nrepos:\n  - slug: o/b\n  - slug: o/a\n  - slug: o/b\n  - profile: no-slug\n  - junk\n",
                encoding="utf-8",
            )
            self.assertEqual(fleet_state.fleet_slugs(inventory), ["o/a", "o/b"])
        roster = fleet_state.fleet_slugs()
        self.assertIn("Prekzursil/quality-zero-platform", roster)
        self.assertNotIn("Prekzursil/fractal-darkroom-research", roster)


class DriftSyncPrTests(unittest.TestCase):
    """Open drift-sync PRs in the declared fleet, and the members that could not be read."""

    def test_only_same_repo_heads_apply_drift_pr_creates_count(self) -> None:
        """Review findings: match the ACTUAL head (<prefix>/<run-id>), and never a fork's branch of that name."""
        prs = [
            _pr(12),
            _pr(13, head="feature/x"),
            _pr(14, head=f"{fleet_state.DRIFT_BRANCH_PREFIX}-old"),
            _pr(15, head=fleet_state.DRIFT_BRANCH_PREFIX),
            _pr(16, fork=True),  # a fork mimicking the branch name
            {key: value for key, value in _pr(17).items() if key != "isCrossRepository"},  # origin unproven
        ]
        runner = _gh(prs)
        records, unread = fleet_state.drift_sync_prs(["o/r"], runner=runner, environ={})
        self.assertEqual([r["pr_number"] for r in records], [12, 15])
        self.assertEqual(unread, {})
        argv = runner.call_args.args[0]
        self.assertEqual(argv[1:5], ["pr", "list", "--repo", "o/r"])
        self.assertEqual(argv[argv.index("--state") + 1], "open")
        self.assertIn("isCrossRepository", argv[argv.index("--json") + 1])
        self.assertEqual(fleet_state.DRIFT_BRANCH_PREFIX, "quality-zero/drift-sync")
        self.assertEqual((records[0]["slug"], records[0]["visibility"]), ("o/r", "public"))
        triggers = alert_dispatch._drift_pr_triggers(records[:1], now=NOW)
        self.assertEqual([t.subject for t in triggers], ["o/r#12"])

    def test_every_declared_repo_is_read_and_nothing_else(self) -> None:
        """Review finding: an owner-wide search also reported forks and de-enrolled repositories."""
        runner = _listings([_pr(1, repo="o/a")], [_pr(2, repo="o/b")])
        records, unread = fleet_state.drift_sync_prs(["o/a", "o/b"], runner=runner, environ={})
        self.assertEqual([(r["slug"], r["pr_number"]) for r in records], [("o/a", 1), ("o/b", 2)])
        self.assertEqual(unread, {})
        self.assertEqual([call.args[0][4] for call in runner.call_args_list], ["o/a", "o/b"])

    def test_a_member_that_cannot_be_read_is_unread_and_the_rest_still_counts(self) -> None:
        """Review findings: one failed or truncated member must neither abort the fleet nor read as 'no drift'."""
        full = [_pr(n, head="feature/x") for n in range(fleet_state.LIST_LIMIT + 1)]
        runner = _listings(1, full, [_pr(3, repo="o/c")])
        records, unread = fleet_state.drift_sync_prs(["o/a", "o/b", "o/c"], runner=runner, environ={})
        self.assertEqual([(r["slug"], r["pr_number"]) for r in records], [("o/c", 3)])
        self.assertEqual(list(unread), ["o/a", "o/b"])
        self.assertIn("exited 1: boom", unread["o/a"])
        self.assertIn("may be truncated", unread["o/b"])

    def test_the_fleet_read_pat_widens_the_read_and_is_named_in_the_scope(self) -> None:
        """Private members' PRs are only visible to the fleet-read PAT; GH_TOKEN is swapped for these calls alone."""
        runner = _gh([_pr(12)])
        environ = {"GH_TOKEN": "workflow", fleet_state.FLEET_READ_PAT_ENV: " pat ", "PATH": "/bin"}
        fleet_state.drift_sync_prs(["o/r"], runner=runner, environ=environ)
        self.assertEqual(runner.call_args.kwargs["env"]["GH_TOKEN"], "pat")
        self.assertEqual(environ["GH_TOKEN"], "workflow")
        self.assertEqual(fleet_state.drift_search_scope(environ), "fleet-wide (fleet-read token)")
        for bare in ({}, {fleet_state.FLEET_READ_PAT_ENV: "  "}):
            with self.subTest(environ=bare):
                runner = _gh([_pr(12)])
                _records, unread = fleet_state.drift_sync_prs(["o/r", PRIVATE], runner=runner, environ=bare)
                self.assertNotIn("env", runner.call_args.kwargs)
                self.assertEqual(runner.call_count, 1)  # the private member is not read without the PAT...
                self.assertEqual(unread, {fleet_state.public_slug(PRIVATE): "private, and no fleet-read token"})
                self.assertIn("public repositories only", fleet_state.drift_search_scope(bare))
        self.assertIn(PRIVATE, fleet_state.PRIVATE_SLUGS)

    def test_a_private_members_drift_pr_never_reaches_a_public_surface_by_name(self) -> None:
        """Dashboard rows, public alert subjects and failure reasons get a stable tag, never the private slug."""
        environ = {fleet_state.FLEET_READ_PAT_ENV: "pat"}
        runner = _listings([_pr(12)], [_pr(3, repo=PRIVATE)])
        (public, private), _unread = fleet_state.drift_sync_prs(["o/r", PRIVATE], runner=runner, environ=environ)
        self.assertEqual((public["slug"], public["visibility"]), ("o/r", "public"))
        self.assertTrue(private["slug"].startswith(fleet_state.PRIVATE_SUBJECT_PREFIX))
        self.assertEqual(private["slug"], fleet_state.public_slug(PRIVATE))  # stable: alerts dedupe
        self.assertEqual((private["pr_url"], private["visibility"]), ("", "private"))
        self.assertNotIn("PrimariRo", fleet_state.to_jsonl([private]))
        (trigger,) = alert_dispatch._drift_pr_triggers([private], now=NOW)
        self.assertNotIn("PrimariRo", trigger.subject + trigger.body)
        failing = MagicMock(return_value=subprocess.CompletedProcess(["gh"], 1, "", f"Could not resolve {PRIVATE}\n"))
        records, unread = fleet_state.drift_sync_prs([PRIVATE], runner=failing, environ=environ)
        self.assertEqual((records, unread), ([], {fleet_state.public_slug(PRIVATE): "the read failed"}))
        self.assertNotIn("PrimariRo", json.dumps(unread))

    def test_jsonl_carries_iso_timestamps_for_the_drift_page(self) -> None:
        """The dashboard page reads the same records from a JSONL file."""
        (record,), _unread = fleet_state.drift_sync_prs(["o/r"], runner=_gh([_pr(12)]), environ={})
        (line,) = fleet_state.to_jsonl([record]).splitlines()
        self.assertEqual(json.loads(line)["opened_at"], "2026-10-10T00:00:00+00:00")
        self.assertEqual(json.loads(line)["status"], "open")


class CliTests(unittest.TestCase):
    """The dashboard build writes the drift JSONL; a partial read says so; nothing read writes nothing."""

    def _inventory(self, tmp: str, *slugs: str) -> str:
        path = Path(tmp) / "repos.yml"
        path.write_text("version: 1\nrepos:\n" + "".join(f"  - slug: {slug}\n" for slug in slugs), encoding="utf-8")
        return str(path)

    def _main(self, tmp: str, runner: MagicMock, *slugs: str) -> tuple:
        out = Path(tmp) / "audit" / "drift-sync.jsonl"
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            patch.dict(fleet_state.os.environ, {fleet_state.FLEET_READ_PAT_ENV: ""}),
        ):
            code = fleet_state.main(
                ["--inventory", self._inventory(tmp, *slugs), "--drift-jsonl", str(out)], runner=runner
            )
        lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()] if out.exists() else None
        return code, lines, stdout.getvalue(), stderr.getvalue()

    def test_the_cli_writes_the_live_drift_jsonl(self) -> None:
        """One line per open drift-sync PR, into a directory it creates."""
        with tempfile.TemporaryDirectory() as tmp:
            code, lines, out, _err = self._main(tmp, _gh([_pr(12)]), "o/r")
        self.assertEqual(code, 0)
        self.assertEqual([line["pr_number"] for line in lines], [12])
        self.assertIn("1 open drift-sync PR(s) in 1 of 1 repositories", out)
        self.assertIn("scope: public repositories only", out)

    def test_a_partial_read_is_written_with_its_marker_and_warned(self) -> None:
        """Review finding: skipped or failed members made the file look complete ("Fleet is in sync")."""
        with tempfile.TemporaryDirectory() as tmp:
            code, lines, out, _err = self._main(tmp, _listings([_pr(12)], 1), "o/r", "o/s", PRIVATE)
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], fleet_state.partial_marker(read=1, unread=2))
        self.assertEqual(lines[-1], {"kind": "partial", "read": 1, "unread": 2})
        self.assertIn("::warning::fleet_state: o/s was not read", out)
        self.assertIn(f"::warning::fleet_state: {fleet_state.public_slug(PRIVATE)} was not read", out)
        self.assertNotIn("PrimariRo", out)

    def test_nothing_read_exits_1_and_writes_nothing(self) -> None:
        """No file means the drift page says "No data", never "No drift entries"."""
        with tempfile.TemporaryDirectory() as tmp:
            code, lines, _out, err = self._main(tmp, _gh("", returncode=1), "o/r")
        self.assertEqual(code, 1)
        self.assertIsNone(lines)
        self.assertIn("no fleet repository could be read; writing nothing", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
