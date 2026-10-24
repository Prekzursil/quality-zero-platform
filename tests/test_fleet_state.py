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
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from scripts.quality import alert_dispatch, fleet_state

NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC)
DRIFT_HEAD = f"{fleet_state.DRIFT_BRANCH_PREFIX}/18276616"
PRIVATE = "Prekzursil/PrimariRo"
EXPIRED = subprocess.CompletedProcess(["gh"], 1, "", "gh: Bad credentials (HTTP 401)\n")


def _gh(stdout: Any, returncode: int = 0) -> MagicMock:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return MagicMock(return_value=subprocess.CompletedProcess(["gh"], returncode, text, "boom\n"))


def _done(payload: Any) -> subprocess.CompletedProcess:
    """A ``gh pr list`` answer: a payload, or an int exit code (failure)."""
    if isinstance(payload, subprocess.CompletedProcess):
        return payload
    if isinstance(payload, int):
        return subprocess.CompletedProcess(["gh"], payload, "", "boom\n")
    return subprocess.CompletedProcess(["gh"], 0, json.dumps(payload), "")


class FakeGh:
    """Answers ``gh api repos/X --jq .private`` from ``visibility`` and ``gh pr list --repo X`` from ``prs``.

    ``visibility[slug]`` is "false"/"true" or an int exit code (default "false": public).
    ``prs[slug]`` is a list of answers consumed in order, one per ``gh pr list`` call.
    """

    def __init__(self, prs: Dict[str, List[Any]], visibility: Optional[Dict[str, Any]] = None) -> None:
        self.prs = {slug: list(answers) for slug, answers in prs.items()}
        self.visibility = visibility or {}
        self.calls: List[tuple] = []

    def __call__(self, argv: List[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append((list(argv), kwargs))
        if argv[1] == "api":
            slug = argv[2].removeprefix("repos/")
            answer = self.visibility.get(slug, "false")
            if isinstance(answer, int):
                return subprocess.CompletedProcess(argv, answer, "", "HTTP 404\n")
            return subprocess.CompletedProcess(argv, 0, f"{answer}\n", "")
        slug = argv[argv.index("--repo") + 1]
        return _done(self.prs[slug].pop(0))

    def listings(self) -> List[tuple]:
        """``(slug, env-or-None)`` of every ``gh pr list`` call, in order."""
        return [(argv[argv.index("--repo") + 1], kwargs.get("env")) for argv, kwargs in self.calls if argv[1] == "pr"]


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

    def test_an_empty_roster_is_an_error_never_no_drift(self) -> None:
        """Review finding: zero declared repos read as 'no drift' and closed every drift alert."""
        with tempfile.TemporaryDirectory() as tmp:
            inventory = Path(tmp) / "repos.yml"
            inventory.write_text("version: 1\nrepos:\n  - profile: no-slug\n", encoding="utf-8")
            with self.assertRaisesRegex(fleet_state.FleetStateError, "declares no fleet repository"):
                fleet_state.fleet_slugs(inventory)


class PrivacyTests(unittest.TestCase):
    """A private repository is never published by name: the check fails closed."""

    def test_the_known_list_is_case_insensitive_and_the_tag_is_stable(self) -> None:
        """Review finding: 'prekzursil/primariro' must be as private as 'Prekzursil/PrimariRo'."""
        runner = MagicMock()
        self.assertTrue(fleet_state.repo_is_private("prekzursil/PRIMARIRO", runner=runner))
        runner.assert_not_called()  # a known member costs no call
        self.assertEqual(fleet_state.public_slug("prekzursil/primariro"), fleet_state.public_slug(PRIVATE))
        self.assertTrue(fleet_state.public_slug(PRIVATE).startswith(fleet_state.PRIVATE_SUBJECT_PREFIX))
        self.assertEqual(fleet_state.public_slug("o/r"), "o/r")
        self.assertEqual(fleet_state.public_slug("o/r", private=True), fleet_state.public_slug("O/R", private=True))

    def test_visibility_is_read_live_and_anything_unproven_is_private(self) -> None:
        """Review finding: a repository whose visibility flipped to private was published as public."""
        fake = FakeGh({}, visibility={"o/public": "false", "o/flipped": "true", "o/unseen": 1, "o/odd": "null"})
        self.assertFalse(fleet_state.repo_is_private("o/public", runner=fake))
        self.assertTrue(fleet_state.repo_is_private("o/flipped", runner=fake))
        self.assertTrue(fleet_state.repo_is_private("o/unseen", runner=fake))  # 404 to this token: fail closed
        self.assertTrue(fleet_state.repo_is_private("o/odd", runner=fake))
        argv, kwargs = fake.calls[0]
        self.assertEqual(argv, ["gh", "api", "repos/o/public", "--jq", ".private"])
        self.assertNotIn("env", kwargs)  # always the workflow token, never the fleet-read PAT


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
        fake = FakeGh({"o/r": [prs]})
        records, unread = fleet_state.drift_sync_prs(["o/r"], runner=fake, environ={})
        self.assertEqual([r["pr_number"] for r in records], [12, 15])
        self.assertEqual(unread, {})
        argv = [a for a, _k in fake.calls if a[1] == "pr"][0]
        self.assertEqual(argv[1:5], ["pr", "list", "--repo", "o/r"])
        self.assertEqual(argv[argv.index("--state") + 1], "open")
        self.assertIn("isCrossRepository", argv[argv.index("--json") + 1])
        self.assertEqual(fleet_state.DRIFT_BRANCH_PREFIX, "quality-zero/drift-sync")
        self.assertEqual((records[0]["slug"], records[0]["visibility"]), ("o/r", "public"))
        triggers = alert_dispatch._drift_pr_triggers(records[:1], now=NOW)
        self.assertEqual([t.subject for t in triggers], ["o/r#12"])

    def test_every_declared_repo_is_read_and_nothing_else(self) -> None:
        """Review finding: an owner-wide search also reported forks and de-enrolled repositories."""
        fake = FakeGh({"o/a": [[_pr(1, repo="o/a")]], "o/b": [[_pr(2, repo="o/b")]]})
        records, unread = fleet_state.drift_sync_prs(["o/a", "o/b"], runner=fake, environ={})
        self.assertEqual([(r["slug"], r["pr_number"]) for r in records], [("o/a", 1), ("o/b", 2)])
        self.assertEqual(unread, {})
        self.assertEqual([slug for slug, _env in fake.listings()], ["o/a", "o/b"])

    def test_a_member_that_cannot_be_read_is_unread_and_the_rest_still_counts(self) -> None:
        """Review findings: one failed or truncated member must neither abort the fleet nor read as 'no drift'."""
        full = [_pr(n, head="feature/x") for n in range(fleet_state.LIST_LIMIT + 1)]
        fake = FakeGh({"o/a": [1], "o/b": [full], "o/c": [[_pr(3, repo="o/c")]]})
        records, unread = fleet_state.drift_sync_prs(["o/a", "o/b", "o/c"], runner=fake, environ={})
        self.assertEqual([(r["slug"], r["pr_number"]) for r in records], [("o/c", 3)])
        self.assertEqual(list(unread), ["o/a", "o/b"])
        self.assertIn("exited 1: boom", unread["o/a"])
        self.assertIn("may be truncated", unread["o/b"])

    def test_a_repository_that_turned_private_is_masked(self) -> None:
        """Review finding: privacy came only from a static list; a flipped repo was published by name."""
        flipped = fleet_state.public_slug("o/secret", private=True)
        fake = FakeGh({"o/secret": [[_pr(4, repo="o/secret")]]}, visibility={"o/secret": "true"})
        records, unread = fleet_state.drift_sync_prs(["o/secret"], runner=fake, environ={})
        self.assertEqual((records, unread), ([], {flipped: "private, and no fleet-read token"}))
        self.assertEqual(fake.listings(), [])  # not read without the PAT
        fake = FakeGh({"o/secret": [[_pr(4, repo="o/secret")]]}, visibility={"o/secret": "true"})
        (record,), _unread = fleet_state.drift_sync_prs(
            ["o/secret"], runner=fake, environ={fleet_state.FLEET_READ_PAT_ENV: "pat"}
        )
        self.assertEqual((record["slug"], record["pr_url"], record["visibility"]), (flipped, "", "private"))
        self.assertNotIn("o/secret", fleet_state.to_jsonl([record]))

    def test_the_fleet_read_pat_widens_the_read_and_is_named_in_the_scope(self) -> None:
        """Private members' PRs are only visible to the fleet-read PAT; GH_TOKEN is swapped for these calls alone."""
        fake = FakeGh({"o/r": [[_pr(12)]]})
        environ = {"GH_TOKEN": "workflow", fleet_state.FLEET_READ_PAT_ENV: " pat ", "PATH": "/bin"}
        fleet_state.drift_sync_prs(["o/r"], runner=fake, environ=environ)
        self.assertEqual(fake.listings()[0][1]["GH_TOKEN"], "pat")
        self.assertEqual(environ["GH_TOKEN"], "workflow")
        self.assertEqual(fleet_state.drift_search_scope(environ), "fleet-wide (fleet-read token)")
        for bare in ({}, {fleet_state.FLEET_READ_PAT_ENV: "  "}):
            with self.subTest(environ=bare):
                fake = FakeGh({"o/r": [[_pr(12)]]})
                records, unread = fleet_state.drift_sync_prs(["o/r", PRIVATE], runner=fake, environ=bare)
                self.assertEqual([r["pr_number"] for r in records], [12])
                self.assertEqual(fake.listings(), [("o/r", None)])  # the private member is not read without the PAT
                self.assertEqual(unread, {fleet_state.public_slug(PRIVATE): "private, and no fleet-read token"})
                self.assertIn("public repositories only", fleet_state.drift_search_scope(bare))
        self.assertIn(PRIVATE.lower(), fleet_state.PRIVATE_SLUGS)

    def test_a_rejected_fleet_read_token_falls_back_for_public_members(self) -> None:
        """Review finding: an expired PAT blinded every read, public members included."""
        environ = {"GH_TOKEN": "workflow", fleet_state.FLEET_READ_PAT_ENV: "expired-pat"}
        fake = FakeGh({"o/a": [EXPIRED, [_pr(1, repo="o/a")]], "o/b": [[_pr(2, repo="o/b")]]})
        records, unread = fleet_state.drift_sync_prs(["o/a", "o/b", PRIVATE], runner=fake, environ=environ)
        self.assertEqual([(r["slug"], r["pr_number"]) for r in records], [("o/a", 1), ("o/b", 2)])
        self.assertEqual(unread, {fleet_state.public_slug(PRIVATE): "private, and the fleet-read token was rejected"})
        envs = [env for _slug, env in fake.listings()]
        self.assertEqual(envs[0]["GH_TOKEN"], "expired-pat")
        self.assertEqual(envs[1:], [None, None])
        # A private member that meets the rejection first is unread with a name-free reason; the next public
        # member is read with the workflow token straight away.
        fake = FakeGh({PRIVATE: [EXPIRED], "o/a": [[_pr(3, repo="o/a")]]})
        records, unread = fleet_state.drift_sync_prs([PRIVATE, "o/a"], runner=fake, environ=environ)
        self.assertEqual([r["pr_number"] for r in records], [3])
        self.assertEqual(unread, {fleet_state.public_slug(PRIVATE): "private, and the fleet-read token was rejected"})
        self.assertEqual(fake.listings()[1], ("o/a", None))
        # The workflow-token retry can fail too: that member is unread with its own reason.
        fake = FakeGh({"o/a": [EXPIRED, 1]})
        records, unread = fleet_state.drift_sync_prs(["o/a"], runner=fake, environ=environ)
        self.assertEqual(records, [])
        self.assertIn("exited 1: boom", unread["o/a"])

    def test_a_private_members_drift_pr_never_reaches_a_public_surface_by_name(self) -> None:
        """Dashboard rows, public alert subjects and failure reasons get a stable tag, never the private slug."""
        environ = {fleet_state.FLEET_READ_PAT_ENV: "pat"}
        fake = FakeGh({"o/r": [[_pr(12)]], PRIVATE: [[_pr(3, repo=PRIVATE)]]})
        (public, private), unread = fleet_state.drift_sync_prs(["o/r", PRIVATE], runner=fake, environ=environ)
        self.assertEqual(unread, {})  # with the PAT, the private member is read
        self.assertEqual((public["slug"], public["visibility"]), ("o/r", "public"))
        self.assertTrue(private["slug"].startswith(fleet_state.PRIVATE_SUBJECT_PREFIX))
        self.assertEqual(private["slug"], fleet_state.public_slug(PRIVATE))  # stable: alerts dedupe
        self.assertEqual((private["pr_url"], private["visibility"]), ("", "private"))
        self.assertNotIn("PrimariRo", fleet_state.to_jsonl([private]))
        (trigger,) = alert_dispatch._drift_pr_triggers([private], now=NOW)
        self.assertNotIn("PrimariRo", trigger.subject + trigger.body)
        failing = FakeGh({PRIVATE: [subprocess.CompletedProcess(["gh"], 1, "", f"Could not resolve {PRIVATE}\n")]})
        records, unread = fleet_state.drift_sync_prs([PRIVATE], runner=failing, environ=environ)
        self.assertEqual((records, unread), ([], {fleet_state.public_slug(PRIVATE): "the read failed"}))
        self.assertNotIn("PrimariRo", json.dumps(unread))

    def test_jsonl_carries_iso_timestamps_for_the_drift_page(self) -> None:
        """The dashboard page reads the same records from a JSONL file."""
        (record,), unread = fleet_state.drift_sync_prs(["o/r"], runner=FakeGh({"o/r": [[_pr(12)]]}), environ={})
        self.assertEqual(unread, {})
        (line,) = fleet_state.to_jsonl([record]).splitlines()
        self.assertEqual(json.loads(line)["opened_at"], "2026-10-10T00:00:00+00:00")
        self.assertEqual(json.loads(line)["status"], "open")


class CliTests(unittest.TestCase):
    """The dashboard build writes the drift JSONL; a partial read says so; nothing read writes nothing."""

    def _inventory(self, tmp: str, *slugs: str) -> str:
        path = Path(tmp) / "repos.yml"
        body = "".join(f"  - slug: {slug}\n" for slug in slugs) or "  - profile: none\n"
        path.write_text("version: 1\nrepos:\n" + body, encoding="utf-8")
        return str(path)

    def _main(self, tmp: str, runner: Any, *slugs: str) -> tuple:
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
            code, lines, out, _err = self._main(tmp, FakeGh({"o/r": [[_pr(12)]]}), "o/r")
        self.assertEqual(code, 0)
        self.assertEqual([line["pr_number"] for line in lines], [12])
        self.assertIn("1 open drift-sync PR(s) in 1 of 1 repositories", out)
        self.assertIn("scope: public repositories only", out)

    def test_a_partial_read_is_written_with_its_marker_and_warned(self) -> None:
        """Review finding: skipped or failed members made the file look complete ("Fleet is in sync")."""
        with tempfile.TemporaryDirectory() as tmp:
            code, lines, out, _err = self._main(tmp, FakeGh({"o/r": [[_pr(12)]], "o/s": [1]}), "o/r", "o/s", PRIVATE)
        self.assertEqual(code, 0)
        self.assertEqual(lines[-1], fleet_state.partial_marker(read=1, unread=2))
        self.assertEqual(lines[-1], {"kind": "partial", "read": 1, "unread": 2})
        self.assertIn("::warning::fleet_state: o/s was not read", out)
        self.assertIn(f"::warning::fleet_state: {fleet_state.public_slug(PRIVATE)} was not read", out)
        self.assertNotIn("PrimariRo", out)

    def test_nothing_read_exits_1_and_writes_nothing(self) -> None:
        """No file means the drift page says "No data", never "No drift entries"."""
        with tempfile.TemporaryDirectory() as tmp:
            code, lines, _out, err = self._main(tmp, FakeGh({"o/r": [1]}), "o/r")
        self.assertEqual(code, 1)
        self.assertIsNone(lines)
        self.assertIn("no fleet repository could be read; writing nothing", err)

    def test_an_empty_inventory_exits_1_and_writes_nothing(self) -> None:
        """Review finding: an empty roster wrote an empty file - 'No drift entries' - instead of No data."""
        with tempfile.TemporaryDirectory() as tmp:
            code, lines, _out, err = self._main(tmp, FakeGh({}))
        self.assertEqual(code, 1)
        self.assertIsNone(lines)
        self.assertIn("declares no fleet repository", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
