from __future__ import absolute_import

import json
import re
import unittest
from pathlib import Path

from scripts.quality.control_plane import (
    build_ruleset_payload,
    load_inventory,
    load_repo_profile,
)
from scripts.quality.profile_normalization import normalize_ruleset

ROOT = Path(__file__).resolve().parents[1]

# Contexts of the retired SaaS lane. Nothing emits them any more, so a
# generated ruleset requiring one would block every pull request if applied.
RETIRED_CONTEXT = re.compile(
    r"shared-scanner-matrix /|shared-codecov-analytics /|Sonar|Codacy|qlty|QLTY|DeepSource|DeepScan|Sentry"
    r"|Chromatic|Applitools|Percy|Snyk|Semgrep Zero|Coverage 100 Gate"
)

ANY_SEVERITY_CODEQL = {"tool": "CodeQL", "alerts_threshold": "all", "security_alerts_threshold": "all"}
ADMIN_VIA_PULL_REQUEST = {"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}


def _payload(slug: str) -> dict:
    inventory = load_inventory(ROOT / "inventory" / "repos.yml")
    return build_ruleset_payload(load_repo_profile(inventory, slug))


def _contexts(payload: dict) -> list:
    rule = next(rule for rule in payload["rules"] if rule["type"] == "required_status_checks")
    return [entry["context"] for entry in rule["parameters"]["required_status_checks"]]


class RulesetPayloadTests(unittest.TestCase):
    """Validate emitted ruleset payloads for representative repos."""

    def test_no_generated_ruleset_requires_a_retired_saas_context(self) -> None:
        """The SaaS lane was retired; requiring its contexts deadlocks every PR."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        for entry in inventory["repos"]:
            with self.subTest(repo=entry["slug"]):
                contexts = _contexts(build_ruleset_payload(load_repo_profile(inventory, entry["slug"])))
                self.assertTrue(contexts, "a ruleset must require at least one check")
                self.assertEqual([name for name in contexts if RETIRED_CONTEXT.search(name)], [])

    def test_pbinfo_fixture_ruleset_payload_matches_declared_contexts(self) -> None:
        """The lean contract: the gate caller plus the code-scanning aggregate."""
        payload = _payload("Prekzursil/pbinfo-get-unsolved")
        required = next(rule for rule in payload["rules"] if rule["type"] == "required_status_checks")

        self.assertEqual(payload["name"], "quality-zero-platform / pbinfo-get-unsolved")
        self.assertEqual(_contexts(payload), ["quality / quality", "CodeQL"])
        self.assertTrue(
            all(entry == {"context": entry["context"]} for entry in required["parameters"]["required_status_checks"])
        )
        self.assertEqual(payload["rules"][0]["parameters"]["required_approving_review_count"], 0)
        self.assertFalse(payload["rules"][0]["parameters"]["required_review_thread_resolution"])

    def test_reframe_requires_its_own_quality_job(self) -> None:
        """Reframe runs the original lean gate as a job named `quality`."""
        self.assertEqual(_contexts(_payload("Prekzursil/Reframe")), ["quality", "CodeQL"])

    def test_webcoder_requires_only_what_it_emits(self) -> None:
        """No lean caller yet, so `quality / quality` would be a phantom."""
        self.assertEqual(_contexts(_payload("Prekzursil/WebCoder")), ["CodeQL"])

    def test_legacy_profiles_use_the_lean_pair(self) -> None:
        """Every other migrated SaaS-era profile requires exactly the lean pair."""
        for slug in (
            "Prekzursil/Airline-Reservations-System",
            "Prekzursil/DevExtreme-Filter-Go-Language",
            "Prekzursil/event-link",
            "Prekzursil/Personal-Finance-Management",
            "Prekzursil/momentstudio",
            "Prekzursil/SWFOC-Mod-Menu",
            "Prekzursil/Star-Wars-Galactic-Battlegrounds-Save-Game-Editor",
            "Prekzursil/TanksFlashMobile",
            "Prekzursil/env-inspector",
            "Prekzursil/codeblocks-pretty-prints-stable",
        ):
            with self.subTest(repo=slug):
                self.assertEqual(_contexts(_payload(slug)), ["quality / quality", "CodeQL"])

    def test_quality_zero_platform_holds_itself_to_its_own_gates(self) -> None:
        """Verify, its own ruff + opengrep lanes, and the live `CodeQL` aggregate."""
        self.assertEqual(
            _contexts(_payload("Prekzursil/quality-zero-platform")),
            ["Control Plane Verify", "gate-lint-format (ruff)", "gate-sast (opengrep)", "CodeQL"],
        )

    def test_every_ruleset_blocks_codeql_alerts_of_any_severity(self) -> None:
        """Owner directive: CodeQL breaks a PR on any alert, not only high/medium."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        for entry in inventory["repos"]:
            with self.subTest(repo=entry["slug"]):
                payload = build_ruleset_payload(load_repo_profile(inventory, entry["slug"]))
                rule = next(rule for rule in payload["rules"] if rule["type"] == "code_scanning")
                self.assertEqual(rule["parameters"]["code_scanning_tools"], [ANY_SEVERITY_CODEQL])

    def test_every_ruleset_allows_only_an_audited_admin_bypass(self) -> None:
        """Admins may merge past a red check through a PR; nobody may push past it."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        for entry in inventory["repos"]:
            with self.subTest(repo=entry["slug"]):
                payload = build_ruleset_payload(load_repo_profile(inventory, entry["slug"]))
                self.assertEqual(payload["bypass_actors"], [ADMIN_VIA_PULL_REQUEST])
                self.assertEqual(payload["enforcement"], "active")

    def test_phantom_validator_reads_what_the_generator_requires(self) -> None:
        """The phantom validator reads raw profile YAML; the generator resolves the stack.

        They agree only while every profile declares its contexts explicitly
        (`required_contexts_mode: replace`). This pins that agreement, so a
        profile that starts inheriting contexts fails here instead of the
        validator silently measuring a different list than the ruleset enforces.
        """
        from scripts.quality.validate_required_contexts import collect_required, load_profile

        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        for entry in inventory["repos"]:
            with self.subTest(repo=entry["slug"]):
                raw, profile_error = load_profile(ROOT / "profiles" / "repos", entry["profile"])
                required, required_error = collect_required(raw)
                self.assertEqual((profile_error, required_error), ("", ""))
                payload = build_ruleset_payload(load_repo_profile(inventory, entry["slug"]))
                self.assertEqual(required, _contexts(payload))

    def test_committed_payloads_are_the_generator_output(self) -> None:
        """generated/rulesets is exactly what the profiles produce."""
        inventory = load_inventory(ROOT / "inventory" / "repos.yml")
        for entry in inventory["repos"]:
            profile = load_repo_profile(inventory, entry["slug"])
            committed = ROOT / "generated" / "rulesets" / f"{profile['profile_id']}.json"
            with self.subTest(repo=entry["slug"]):
                self.assertEqual(json.loads(committed.read_text(encoding="utf-8")), build_ruleset_payload(profile))


class RulesetPayloadShapeTests(unittest.TestCase):
    """The payload follows the profile's ``ruleset`` block."""

    def _profile(self, **ruleset) -> dict:
        return {
            "profile_id": "demo",
            "slug": "o/demo",
            "repo_name": "demo",
            "default_branch": "main",
            "required_contexts": {"always": ["quality / quality"]},
            "ruleset": ruleset,
        }

    def test_without_code_scanning_no_rule_is_emitted(self) -> None:
        """An empty tool list means no merge protection rule at all."""
        payload = build_ruleset_payload(self._profile(code_scanning=[]))
        self.assertNotIn("code_scanning", [rule["type"] for rule in payload["rules"]])
        self.assertEqual(payload["bypass_actors"], [])

    def test_enforcement_and_thresholds_are_carried_through(self) -> None:
        """A profile may stage a ruleset and pick its own thresholds."""
        payload = build_ruleset_payload(
            self._profile(
                enforcement="evaluate",
                code_scanning=[{"tool": "CodeQL", "security_alerts_threshold": "high_or_higher"}],
            )
        )
        self.assertEqual(payload["enforcement"], "evaluate")
        self.assertEqual(
            payload["rules"][-1],
            {
                "type": "code_scanning",
                "parameters": {
                    "code_scanning_tools": [
                        {"tool": "CodeQL", "alerts_threshold": "all", "security_alerts_threshold": "high_or_higher"}
                    ]
                },
            },
        )

    def test_a_profile_without_a_ruleset_block_gets_safe_defaults(self) -> None:
        """Hand-built profiles (tests, tools) still produce a valid payload."""
        profile = self._profile()
        del profile["ruleset"]
        payload = build_ruleset_payload(profile)
        self.assertEqual(payload["enforcement"], "active")
        self.assertEqual(payload["bypass_actors"], [])


class NormalizeRulesetTests(unittest.TestCase):
    """Values the GitHub API would reject fail while the profile renders."""

    def test_defaults(self) -> None:
        """No block, or a non-mapping, normalises to an empty active ruleset."""
        expected = {"enforcement": "active", "bypass_actors": [], "code_scanning": []}
        self.assertEqual(normalize_ruleset(None), expected)
        self.assertEqual(normalize_ruleset(["not", "a", "mapping"]), expected)
        self.assertEqual(normalize_ruleset({"enforcement": "  "}), expected)

    def test_bypass_actor_forms(self) -> None:
        """Integer or null ids; the mode defaults to always."""
        result = normalize_ruleset(
            {
                "bypass_actors": [
                    {"actor_type": "OrganizationAdmin", "actor_id": None},
                    {"actor_type": "User", "actor_id": 7, "bypass_mode": "pull_request"},
                ]
            }
        )
        self.assertEqual(
            result["bypass_actors"],
            [
                {"actor_id": None, "actor_type": "OrganizationAdmin", "bypass_mode": "always"},
                {"actor_id": 7, "actor_type": "User", "bypass_mode": "pull_request"},
            ],
        )

    def test_rejections(self) -> None:
        """Every invalid value raises with the offending field named."""
        cases = [
            ({"enforcement": "on"}, "enforcement"),
            ({"bypass_actors": ["admin"]}, "bypass_actors entries must be mappings"),
            ({"bypass_actors": [{"actor_type": "Admin", "actor_id": 5}]}, "actor_type"),
            ({"bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": "5"}]}, "actor_id"),
            ({"bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": True}]}, "actor_id"),
            (
                {"bypass_actors": [{"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "never"}]},
                "bypass_mode",
            ),
            ({"code_scanning": ["CodeQL"]}, "code_scanning entries must be mappings"),
            ({"code_scanning": [{"tool": " "}]}, "need a tool name"),
            ({"code_scanning": [{"tool": "CodeQL", "alerts_threshold": "warnings"}]}, "alerts_threshold"),
            ({"code_scanning": [{"tool": "CodeQL", "security_alerts_threshold": "low"}]}, "security_alerts_threshold"),
        ]
        for raw, message in cases:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, message):
                normalize_ruleset(raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
