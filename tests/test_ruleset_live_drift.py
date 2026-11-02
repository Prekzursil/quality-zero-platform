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

    def test_an_organization_admin_id_is_not_compared(self) -> None:
        """The API ignores that id, so 1 declared and null live is the same actor."""
        expected = dict(
            EXPECTED, bypass_actors=[{"actor_id": 1, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}]
        )
        live = _live(bypass_actors=[{"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "always"}])
        self.assertEqual(drift.compare(expected, live), ([], []))
        self.assertEqual(drift.bypass_actors(live), frozenset({("OrganizationAdmin", "", "always")}))

    def test_expected_without_bypass_list_compares_as_empty(self) -> None:
        """A payload with no bypass list declares none."""
        expected = dict(EXPECTED)
        del expected["bypass_actors"]
        findings, _ = drift.compare(expected, _live())
        self.assertEqual(findings, ["bypass actors: live but not declared: [('RepositoryRole', '5', 'pull_request')]"])


#: The shape generate_ruleset_payload.py emits (control_plane.build_ruleset_payload).
GENERATED: Dict[str, Any] = {
    "name": "quality-zero-platform / demo",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
    "bypass_actors": [],
    "rules": [
        {
            "type": "pull_request",
            "parameters": {"required_approving_review_count": 0, "required_review_thread_resolution": False},
        },
        {
            "type": "required_status_checks",
            "parameters": {
                "strict_required_status_checks_policy": True,
                "do_not_enforce_on_create": False,
                "required_status_checks": [{"context": "quality / quality"}, {"context": "CodeQL"}],
            },
        },
        {"type": "non_fast_forward"},
        {"type": "deletion"},
    ],
}


def _live_generated(**changes: Any) -> Dict[str, Any]:
    """GENERATED as GitHub returns it: extra fields, extra default parameters, lists reordered."""
    live = json.loads(json.dumps(GENERATED))
    live.update({"id": 7, "source_type": "Repository", "current_user_can_bypass": "never"})
    live["conditions"] = {"ref_name": {"exclude": [], "include": ["refs/heads/main"]}}
    pull_request, checks, non_ff, deletion = live["rules"]
    pull_request["parameters"]["allowed_merge_methods"] = ["squash", "merge", "rebase"]
    checks["parameters"]["required_status_checks"].reverse()
    live["rules"] = [deletion, checks, non_ff, pull_request]
    live.update(changes)
    return live


class FullConfigurationTests(unittest.TestCase):
    """A weaker live policy with the same contexts is still drift."""

    def test_github_shape_of_the_generated_payload_is_in_sync(self) -> None:
        """Extra API fields, undeclared default parameters and list order are not drift."""
        self.assertEqual(drift.compare(GENERATED, _live_generated()), ([], []))

    def test_a_weakened_rule_parameter_is_drift(self) -> None:
        """THE review finding: strict policy off, same contexts, used to PASS."""
        live = _live_generated()
        live["rules"][1]["parameters"]["strict_required_status_checks_policy"] = False
        del live["rules"][3]["parameters"]["required_review_thread_resolution"]
        findings, _ = drift.compare(GENERATED, live)
        self.assertEqual(
            findings,
            [
                "rule pull_request: required_review_thread_resolution declared False, live None",
                "rule required_status_checks: strict_required_status_checks_policy declared True, live False",
            ],
        )

    def test_missing_and_extra_rule_types_are_drift(self) -> None:
        """Dropping `deletion` / `non_fast_forward`, or adding an undeclared rule."""
        live = _live_generated()
        live["rules"] = [rule for rule in live["rules"] if rule["type"] not in {"deletion", "non_fast_forward"}]
        live["rules"].append({"type": "required_linear_history"})
        findings, _ = drift.compare(GENERATED, live)
        self.assertEqual(
            findings,
            [
                "rules: declared but not live: ['deletion', 'non_fast_forward']",
                "rules: live but not declared: ['required_linear_history']",
            ],
        )

    def test_target_and_conditions_are_compared(self) -> None:
        """A ruleset moved to another ref, or to tags, enforces nothing on the default branch."""
        live = _live_generated(target="tag", conditions={"ref_name": {"include": ["refs/heads/dev"], "exclude": []}})
        findings, _ = drift.compare(GENERATED, live)
        self.assertEqual(len(findings), 2)
        self.assertTrue(findings[0].startswith("target: declared 'branch', live 'tag'"))
        self.assertTrue(findings[1].startswith("conditions: declared"))
        self.assertIn("refs/heads/dev", findings[1])

    def test_duplicate_rules_of_one_type_merge_their_parameters(self) -> None:
        """Parameters are read across every rule of a type, defensively."""
        payload = {"rules": ["x", {"type": "t", "parameters": {"a": 1}}, {"type": "t", "parameters": {"b": 2}}, {}]}
        self.assertEqual(drift.rule_types(payload), frozenset({"t"}))
        self.assertEqual(drift.merged_parameters(payload, "t"), {"a": 1, "b": 2})

    def test_canonical_ignores_key_and_list_order_only(self) -> None:
        """Order-insensitive, but values still count."""
        self.assertEqual(drift.canonical({"b": [2, 1], "a": None}), drift.canonical({"a": None, "b": [1, 2]}))
        self.assertNotEqual(drift.canonical({"a": [1]}), drift.canonical({"a": [1, 1]}))
        self.assertEqual(drift.canonical("x"), '"x"')


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

    def test_empty_output_is_an_error(self) -> None:
        """No JSON at all is never read as "no rulesets"."""
        with self.assertRaisesRegex(drift.LiveRulesetError, "returned no JSON"):
            drift.fetch_live_ruleset("o/r", "n", runner=FakeGh({"repos/o/r/rulesets": (0, " \n", "")}))

    def test_every_page_of_the_listing_is_read(self) -> None:
        """`gh api --paginate` prints one JSON array per page; the match may be on page 2."""
        pages = json.dumps([{"id": 1, "name": "other"}]) + "\n" + json.dumps([{"id": 42, "name": EXPECTED["name"]}])
        runner = FakeGh({"repos/o/r/rulesets": (0, pages, ""), "repos/o/r/rulesets/42": _live()})
        self.assertEqual(drift.fetch_live_ruleset("o/r", EXPECTED["name"], runner=runner)["id"], 42)

    def test_a_page_that_is_not_a_list_is_an_error(self) -> None:
        """Every page must be an array, not only the first."""
        pages = json.dumps([{"id": 1, "name": "other"}]) + json.dumps({"message": "boom"})
        with self.assertRaisesRegex(drift.LiveRulesetError, "is not a list"):
            drift.fetch_live_ruleset("o/r", "n", runner=FakeGh({"repos/o/r/rulesets": (0, pages, "")}))

    def test_the_ruleset_itself_must_be_one_object(self) -> None:
        """Two documents for a single ruleset are as unusable as none."""
        twice = json.dumps(_live()) + json.dumps(_live())
        runner = FakeGh(
            {"repos/o/r/rulesets": [{"id": 42, "name": EXPECTED["name"]}], "repos/o/r/rulesets/42": (0, twice, "")}
        )
        with self.assertRaisesRegex(drift.LiveRulesetError, "is not an object"):
            drift.fetch_live_ruleset("o/r", EXPECTED["name"], runner=runner)


class DecodeTests(unittest.TestCase):
    """Concatenated JSON documents, as `gh api --paginate` prints them."""

    def test_documents_with_and_without_separators(self) -> None:
        """Whitespace between documents is optional."""
        self.assertEqual(drift.decode_documents(' [1] \n[2]{"a": 3}\n'), [[1], [2], {"a": 3}])
        self.assertEqual(drift.decode_documents(""), [])

    def test_garbage_raises(self) -> None:
        """Trailing garbage is a ValueError, never silently dropped."""
        with self.assertRaises(ValueError):
            drift.decode_documents("[1] nope")


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
