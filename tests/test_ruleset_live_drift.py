"""Tests for the live ruleset drift check (``scripts/quality/ruleset_live_drift.py``).

The old live comparison read ``rules`` from the ruleset LIST endpoint, which
returns none, and assumed the required-checks rule sat at ``rules[1]``. These
tests pin the replacement: the full ruleset is fetched by id, rules are matched
by type, and anything that cannot be established is an error, never "in sync".
"""

from __future__ import absolute_import

import io
import json
import subprocess  # nosec B404 - CompletedProcess builds fake runner results
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List

from scripts.quality import ruleset_live_drift as drift

EXPECTED: Dict[str, Any] = {
    "name": "quality-zero-platform / demo",
    "enforcement": "active",
    "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}],
    "rules": [
        {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "quality / quality"}, {"context": "CodeQL"}]},
        },
        {"type": "deletion"},
        {
            "type": "code_scanning",
            "parameters": {
                "code_scanning_tools": [
                    {"tool": "CodeQL", "alerts_threshold": "all", "security_alerts_threshold": "all"}
                ]
            },
        },
    ],
}


def _live(**changes: Any) -> Dict[str, Any]:
    """A live copy of EXPECTED, as GitHub returns it (rules reordered), with changes."""
    live = json.loads(json.dumps(EXPECTED))
    live["id"] = 42
    live["rules"] = list(reversed(live["rules"]))
    live.update(changes)
    return live


class FakeGh:
    """Answers ``gh api`` calls from a table and records them."""

    def __init__(self, responses: Dict[str, Any]) -> None:
        self.responses = responses
        self.calls: List[List[str]] = []

    def __call__(self, argv: List[str], **_: Any) -> "subprocess.CompletedProcess[str]":
        self.calls.append(argv)
        key = argv[2]
        answer = self.responses.get(key)
        if isinstance(answer, tuple):
            return subprocess.CompletedProcess(argv, answer[0], stdout=answer[1], stderr=answer[2])
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(answer), stderr="")


class ExtractionTests(unittest.TestCase):
    """Rules are read by type, defensively."""

    def test_required_contexts_ignore_malformed_entries(self) -> None:
        """Non-mapping rules, missing parameters and blank contexts are skipped."""
        payload = {
            "rules": [
                "garbage",
                {"type": "required_status_checks"},
                {"type": "required_status_checks", "parameters": "nope"},
                {"type": "required_status_checks", "parameters": {"required_status_checks": ["x", {"context": " "}]}},
                {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "ci"}]}},
            ]
        }
        self.assertEqual(drift.required_contexts(payload), frozenset({"ci"}))
        self.assertEqual(drift.required_contexts({}), frozenset())

    def test_code_scanning_tools_and_bypass_actors(self) -> None:
        """Tuples are built from mappings only; a missing actor list is None."""
        payload = {
            "rules": [{"type": "code_scanning", "parameters": {"code_scanning_tools": ["x", {"tool": "CodeQL"}]}}],
            "bypass_actors": ["x", {"actor_type": "User", "actor_id": 7}],
        }
        self.assertEqual(drift.code_scanning_tools(payload), frozenset({("CodeQL", "", "")}))
        self.assertEqual(drift.bypass_actors(payload), frozenset({("User", "7", "")}))
        self.assertIsNone(drift.bypass_actors({}))


class CompareTests(unittest.TestCase):
    """Every compared dimension reports both directions."""

    def test_reordered_rules_are_in_sync(self) -> None:
        """GitHub's rule order is irrelevant; the old check depended on it."""
        self.assertEqual(drift.compare(EXPECTED, _live()), ([], []))

    def test_every_dimension_drifts(self) -> None:
        """Enforcement, checks, code scanning and bypass all report drift."""
        live = _live(
            enforcement="disabled",
            bypass_actors=[{"actor_id": 54636077, "actor_type": "User", "bypass_mode": "always"}],
            rules=[
                {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "quality"}]}},
            ],
        )
        findings, notes = drift.compare(EXPECTED, live)
        self.assertEqual(notes, [])
        joined = "\n".join(findings)
        self.assertIn("enforcement: declared 'active', live 'disabled'", joined)
        self.assertIn("required checks: declared but not live: ['CodeQL', 'quality / quality']", joined)
        self.assertIn("required checks: live but not declared: ['quality']", joined)
        self.assertIn("code scanning: declared but not live", joined)
        self.assertIn("bypass actors: live but not declared", joined)

    def test_hidden_bypass_actors_are_a_note_not_drift(self) -> None:
        """A token that cannot see bypass actors must not report them as drift."""
        live = _live()
        del live["bypass_actors"]
        self.assertEqual(
            drift.compare(EXPECTED, live),
            ([], ["bypass actors: not visible with this token, so they were not compared"]),
        )

    def test_expected_without_bypass_list_compares_as_empty(self) -> None:
        """A payload with no bypass list declares none."""
        expected = dict(EXPECTED)
        del expected["bypass_actors"]
        findings, _ = drift.compare(expected, _live())
        self.assertEqual(findings, ["bypass actors: live but not declared: [('RepositoryRole', '5', 'pull_request')]"])


class FetchTests(unittest.TestCase):
    """The full ruleset is fetched by id; every failure raises."""

    def test_fetches_the_full_ruleset_by_name_then_id(self) -> None:
        """List, match by name, then GET /rulesets/{id}."""
        runner = FakeGh(
            {
                "repos/o/r/rulesets": [{"id": 1, "name": "other"}, "x", {"id": 42, "name": EXPECTED["name"]}],
                "repos/o/r/rulesets/42": _live(),
            }
        )
        self.assertEqual(drift.fetch_live_ruleset("o/r", EXPECTED["name"], runner=runner)["id"], 42)
        self.assertEqual(runner.calls[0][2:], ["repos/o/r/rulesets", "--paginate"])

    def test_failures_raise(self) -> None:
        """Missing, malformed or unreachable live state is an error."""
        cases = [
            ({"repos/o/r/rulesets": (1, "", "HTTP 403\nforbidden")}, "exited 1: forbidden"),
            ({"repos/o/r/rulesets": (0, "{not json", "")}, "unparseable JSON"),
            ({"repos/o/r/rulesets": {"id": 1}}, "is not a list"),
            ({"repos/o/r/rulesets": []}, "no live ruleset named"),
            (
                {"repos/o/r/rulesets": [{"id": 9, "name": EXPECTED["name"]}], "repos/o/r/rulesets/9": []},
                "not an object",
            ),
        ]
        for responses, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(drift.LiveRulesetError, message):
                drift.fetch_live_ruleset("o/r", EXPECTED["name"], runner=FakeGh(responses))

    def test_empty_output_is_null(self) -> None:
        """An empty body parses as null, which is then rejected as not a list."""
        with self.assertRaisesRegex(drift.LiveRulesetError, "is not a list"):
            drift.fetch_live_ruleset("o/r", "n", runner=FakeGh({"repos/o/r/rulesets": (0, "", "")}))


class MainTests(unittest.TestCase):
    """The CLI's exit codes: 0 in sync, 1 drift, 2 unknown."""

    def setUp(self) -> None:
        """Write the expected payload to a scratch file."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.expected = self._write("expected.json", EXPECTED)

    def _write(self, name: str, document: Any) -> str:
        path = self.tmp / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path)

    def _main(self, argv: List[str], runner: Any = None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = drift.main(argv, runner=runner or FakeGh({}))
        return code, out.getvalue(), err.getvalue()

    def test_committed_copy_in_sync(self) -> None:
        """No live comparison requested: the committed copy decides."""
        code, out, _ = self._main(["--expected", self.expected, "--committed", self._write("c.json", EXPECTED)])
        self.assertEqual(code, drift.EXIT_IN_SYNC)
        self.assertIn("PASS ruleset-drift", out)

    def test_committed_copy_drift(self) -> None:
        """A stale committed payload is drift."""
        code, out, _ = self._main(["--expected", self.expected, "--committed", self._write("c.json", {"name": "x"})])
        self.assertEqual(code, drift.EXIT_DRIFT)
        self.assertIn("differs from the regenerated one", out)

    def test_unreadable_or_non_object_input_is_an_error(self) -> None:
        """Bad input is exit 2, never a pass."""
        self.assertEqual(self._main(["--expected", str(self.tmp / "missing.json")])[0], drift.EXIT_ERROR)
        self.assertEqual(self._main(["--expected", self._write("list.json", [1])])[0], drift.EXIT_ERROR)

    def test_live_in_sync_with_a_visibility_note(self) -> None:
        """Hidden bypass actors print a NOTE and still pass."""
        live = _live()
        del live["bypass_actors"]
        runner = FakeGh({"repos/o/r/rulesets": [{"id": 42, "name": EXPECTED["name"]}], "repos/o/r/rulesets/42": live})
        code, out, _ = self._main(["--expected", self.expected, "--repo-slug", "o/r"], runner)
        self.assertEqual(code, drift.EXIT_IN_SYNC)
        self.assertIn("NOTE ruleset-drift: bypass actors", out)

    def test_live_drift(self) -> None:
        """A live difference is exit 1 with each finding printed."""
        runner = FakeGh(
            {
                "repos/o/r/rulesets": [{"id": 42, "name": EXPECTED["name"]}],
                "repos/o/r/rulesets/42": _live(enforcement="disabled"),
            }
        )
        code, out, _ = self._main(["--expected", self.expected, "--repo-slug", "o/r"], runner)
        self.assertEqual(code, drift.EXIT_DRIFT)
        self.assertIn("DRIFT ruleset-drift: enforcement", out)

    def test_unreadable_live_state_is_an_error(self) -> None:
        """A missing live ruleset means drift is UNKNOWN: exit 2."""
        code, _, err = self._main(
            ["--expected", self.expected, "--repo-slug", "o/r"], FakeGh({"repos/o/r/rulesets": []})
        )
        self.assertEqual(code, drift.EXIT_ERROR)
        self.assertIn("drift is UNKNOWN", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
