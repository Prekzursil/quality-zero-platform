"""Tests for shared SARIF normalizer base (per §9.1 §9.2 §A.2.5)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, List

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.normalizers._sarif import (
    MAX_SARIF_BYTES,
    SarifTooLargeError,
    check_sarif_size,
    parse_sarif,
)
from scripts.quality.rollup_v2.types.finding import Finding


class _StubNormalizer(BaseNormalizer):
    """Stub normalizer for testing parse_sarif."""
    provider = "TestProvider"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        return []


def _minimal_sarif(results: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """Build a minimal valid SARIF 2.1.0 envelope."""
    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "TestTool",
                        "version": "1.0.0",
                        "rules": [],
                    }
                },
                "results": results or [],
            }
        ],
    }


class CheckSarifSizeTests(unittest.TestCase):
    def test_small_data_passes(self):
        check_sarif_size('{"runs": []}')

    def test_exact_limit_passes(self):
        data = "x" * MAX_SARIF_BYTES
        check_sarif_size(data)

    def test_over_limit_raises(self):
        data = "x" * (MAX_SARIF_BYTES + 1)
        with self.assertRaises(SarifTooLargeError):
            check_sarif_size(data)

    def test_bytes_input(self):
        data = b"x" * (MAX_SARIF_BYTES + 1)
        with self.assertRaises(SarifTooLargeError):
            check_sarif_size(data)


class ParseSarifTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("pass", "utf-8")
        self.normalizer = _StubNormalizer()

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_results(self):
        sarif = _minimal_sarif(results=[])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings, [])

    def test_parses_single_result(self):
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "test-rule-1",
                "level": "error",
                "message": {"text": "Something is wrong"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "src/app.py"},
                            "region": {"startLine": 5, "startColumn": 10},
                        }
                    }
                ],
            }
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.file, "src/app.py")
        self.assertEqual(f.line, 5)
        self.assertEqual(f.column, 10)
        self.assertEqual(f.severity, "high")
        self.assertEqual(f.primary_message, "Something is wrong")

    def test_parses_two_results(self):
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "rule-a",
                "level": "warning",
                "message": {"text": "Warning A"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
            },
            {
                "ruleId": "rule-b",
                "level": "note",
                "message": {"text": "Note B"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 2}}}],
            },
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].severity, "medium")
        self.assertEqual(findings[1].severity, "low")

    def test_severity_mapping(self):
        for level, expected_sev in [("error", "high"), ("warning", "medium"), ("note", "low"), ("none", "low")]:
            sarif = _minimal_sarif(results=[
                {
                    "ruleId": "sev-test",
                    "level": level,
                    "message": {"text": "msg"},
                    "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
                }
            ])
            findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
            self.assertEqual(findings[0].severity, expected_sev, f"level={level}")

    def test_missing_locations_defaults(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "no-loc", "level": "warning", "message": {"text": "no location"}}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].file, "unknown")
        self.assertEqual(findings[0].line, 1)

    def test_cwe_extraction_from_properties(self):
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "cwe-test",
                "level": "error",
                "message": {"text": "cwe"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
                "properties": {"cwe": "CWE-79"},
            }
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].cwe, "CWE-79")

    def test_cwe_extraction_from_tags(self):
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "cwe-tag",
                "level": "error",
                "message": {"text": "from tag"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
                "properties": {"tags": ["CWE-89", "security"]},
            }
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].cwe, "CWE-89")

    def test_security_tags_set_category_group(self):
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "sec-rule",
                "level": "error",
                "message": {"text": "security issue"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
                "properties": {"tags": ["security"]},
            }
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].category_group, "security")

    def test_context_snippet_extraction(self):
        snippet_text = "x = dangerous_func(input_val())"
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "snip-test",
                "level": "warning",
                "message": {"text": "msg"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "src/app.py"},
                            "region": {
                                "startLine": 1,
                                "snippet": {"text": snippet_text},
                            },
                        }
                    }
                ],
            }
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].context_snippet, snippet_text)

    def test_rule_url_from_driver_rules(self):
        sarif = {
            "version": "2.1.0",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "TestTool",
                        "version": "1.0.0",
                        "rules": [
                            {"id": "rule-with-help", "helpUri": "https://example.com/rule-with-help"},
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "rule-with-help",
                        "level": "warning",
                        "message": {"text": "found"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
                    }
                ],
            }],
        }
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].corroborators[0].rule_url, "https://example.com/rule-with-help")

    def test_malformed_runs_returns_empty(self):
        findings = parse_sarif({"runs": "not-a-list"}, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings, [])

    def test_malformed_results_in_run(self):
        sarif = {"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "T", "rules": []}}, "results": "not-list"}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings, [])

    def test_end_line_extraction(self):
        sarif = _minimal_sarif(results=[
            {
                "ruleId": "end-line-test",
                "level": "warning",
                "message": {"text": "msg"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 5, "endLine": 10}}}],
            }
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].end_line, 10)

    def test_finding_id_format(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r1", "level": "warning", "message": {"text": "a"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]},
            {"ruleId": "r2", "level": "warning", "message": {"text": "b"}, "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 2}}}]},
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].finding_id, "testprovider-0000")
        self.assertEqual(findings[1].finding_id, "testprovider-0001")


class SarifDefensiveBranchTests(unittest.TestCase):
    """Cover defensive branches for malformed SARIF inputs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("pass", "utf-8")
        self.normalizer = _StubNormalizer()

    def tearDown(self):
        self._tmp.cleanup()

    def test_properties_not_dict_returns_empty_tags(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
             "properties": "not-a-dict"}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_tags_not_list_returns_empty_tags(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
             "properties": {"tags": "not-a-list"}}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_cwe_not_in_props_falls_to_tags(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}],
             "properties": {"cwe": ""}}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertIsNone(findings[0].cwe)

    def test_location_non_dict_returns_defaults(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": ["not-a-dict"]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].file, "unknown")

    def test_region_not_dict_falls_back(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": "not-dict"}}]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].line, 1)

    def test_context_snippet_non_dict_loc(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [42]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].context_snippet, "")

    def test_context_snippet_non_dict_phys(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": "not-dict"}]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].context_snippet, "")

    def test_context_snippet_non_dict_region(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": 42}}]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].context_snippet, "")

    def test_context_snippet_string_snippet(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1, "snippet": "string-not-dict"}}}]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].context_snippet, "")

    def test_run_not_dict_skipped(self):
        sarif = {"runs": ["not-a-dict", {"tool": {"driver": {"name": "T", "rules": []}}, "results": []}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings, [])

    def test_tool_not_dict_no_rules(self):
        sarif = {"runs": [{"tool": "not-dict", "results": [
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ]}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_driver_not_dict_no_rules(self):
        sarif = {"runs": [{"tool": {"driver": "not-dict"}, "results": [
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ]}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_rules_not_list(self):
        sarif = {"runs": [{"tool": {"driver": {"name": "T", "rules": "not-list"}}, "results": [
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ]}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_rule_not_dict_skipped(self):
        sarif = {"runs": [{"tool": {"driver": {"name": "T", "rules": ["not-a-dict"]}}, "results": [
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ]}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_rule_empty_id_skipped(self):
        sarif = {"runs": [{"tool": {"driver": {"name": "T", "rules": [{"id": ""}]}}, "results": [
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ]}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(len(findings), 1)

    def test_result_not_dict_skipped(self):
        sarif = _minimal_sarif(results=["not-a-dict"])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings, [])

    def test_message_as_string(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": "plain string",
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].primary_message, "plain string")

    def test_message_as_int_falls_through(self):
        sarif = _minimal_sarif(results=[
            {"ruleId": "r", "level": "warning", "message": 42,
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ])
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertEqual(findings[0].primary_message, "")

    def test_help_uri_not_string_ignored(self):
        sarif = {"runs": [{"tool": {"driver": {"name": "T", "rules": [{"id": "r", "helpUri": 42}]}}, "results": [
            {"ruleId": "r", "level": "warning", "message": {"text": "m"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "src/app.py"}, "region": {"startLine": 1}}}]}
        ]}]}
        findings = parse_sarif(sarif, "TestProvider", self.root, self.normalizer)
        self.assertIsNone(findings[0].corroborators[0].rule_url)


if __name__ == "__main__":
    unittest.main()
