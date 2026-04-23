"""Coverage for the Phase 4 severity-aware rollup helper."""

from __future__ import absolute_import

import unittest

from scripts.quality import severity_rollup as sr


class SeverityMapTests(unittest.TestCase):
    """``severity_map`` flattens ``profile['scanners']`` to ``{id: severity}``."""

    def test_empty_profile_yields_empty_map(self) -> None:
        """No ``scanners`` key → empty mapping."""
        self.assertEqual(sr.severity_map({}), {})

    def test_profile_scanners_extracted_with_severity(self) -> None:
        """Each declared scanner's severity surfaces in the map."""
        profile = {
            "scanners": {
                "codecov": {"severity": "block"},
                "socket_project_report": {"severity": "info"},
                "sentry_zero": {"severity": "warn"},
            },
        }
        self.assertEqual(
            sr.severity_map(profile),
            {
                "codecov": "block",
                "socket_project_report": "info",
                "sentry_zero": "warn",
            },
        )

    def test_missing_severity_defaults_to_block(self) -> None:
        """Per platform zero-tolerance: unknown = strict."""
        profile = {
            "scanners": {
                "codecov": {},  # no severity
                "sonar_zero": {"severity": "unknown-value"},
            },
        }
        result = sr.severity_map(profile)
        self.assertEqual(result["codecov"], "block")
        self.assertEqual(result["sonar_zero"], "block")

    def test_non_string_scanner_names_skipped(self) -> None:
        """Keys that aren't strings don't crash the loader."""
        profile = {"scanners": {123: {"severity": "block"}, "ok": {"severity": "info"}}}
        self.assertEqual(sr.severity_map(profile), {"ok": "info"})

    def test_non_mapping_scanners_yields_empty(self) -> None:
        """Lists/strings under ``scanners`` return empty map."""
        self.assertEqual(sr.severity_map({"scanners": []}), {})
        self.assertEqual(sr.severity_map({"scanners": "nope"}), {})

    def test_non_mapping_entry_defaults_to_block(self) -> None:
        """Scanner entries that aren't mappings still map to ``block`` default."""
        profile = {"scanners": {"legacy": "legacy-string", "ok": None}}
        result = sr.severity_map(profile)
        self.assertEqual(result["legacy"], "block")
        self.assertEqual(result["ok"], "block")


class ClassifyLanesTests(unittest.TestCase):
    """``classify_lanes`` buckets failing lanes by profile-declared severity."""

    def _profile(self) -> dict:
        """A sample profile with one scanner of each severity."""
        return {
            "scanners": {
                "codecov": {"severity": "block"},
                "sentry_zero": {"severity": "warn"},
                "socket_project_report": {"severity": "info"},
            },
        }

    def test_all_pass_yields_pass_verdict(self) -> None:
        """Pass-only statuses produce verdict=pass with empty buckets."""
        verdict = sr.classify_lanes(
            self._profile(),
            {"codecov": "pass", "sentry_zero": "pass"},
        )
        self.assertEqual(verdict.verdict, "pass")
        self.assertEqual(verdict.blockers, [])
        self.assertEqual(verdict.warnings, [])
        self.assertEqual(verdict.infos, [])

    def test_block_severity_failure_yields_fail(self) -> None:
        """One block-severity failure → verdict=fail."""
        verdict = sr.classify_lanes(
            self._profile(),
            {"codecov": "fail", "sentry_zero": "pass"},
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertEqual(verdict.blockers, ["codecov"])

    def test_warn_severity_failure_yields_warn(self) -> None:
        """Only warn-severity failures → verdict=warn."""
        verdict = sr.classify_lanes(
            self._profile(),
            {"codecov": "pass", "sentry_zero": "fail"},
        )
        self.assertEqual(verdict.verdict, "warn")
        self.assertEqual(verdict.warnings, ["sentry_zero"])

    def test_info_severity_failure_does_not_escalate(self) -> None:
        """Info-only failures stay info, verdict remains pass."""
        verdict = sr.classify_lanes(
            self._profile(),
            {"socket_project_report": "fail"},
        )
        self.assertEqual(verdict.verdict, "pass")
        self.assertEqual(verdict.infos, ["socket_project_report"])

    def test_mixed_severity_failures_use_highest(self) -> None:
        """Block trumps warn trumps info in the aggregate verdict."""
        verdict = sr.classify_lanes(
            self._profile(),
            {
                "codecov": "fail",
                "sentry_zero": "fail",
                "socket_project_report": "fail",
            },
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertEqual(verdict.blockers, ["codecov"])
        self.assertEqual(verdict.warnings, ["sentry_zero"])
        self.assertEqual(verdict.infos, ["socket_project_report"])

    def test_unknown_lane_defaults_to_block(self) -> None:
        """Lanes not in the profile's severity map default to block."""
        verdict = sr.classify_lanes(
            self._profile(),
            {"brand_new_scanner": "fail"},
        )
        self.assertEqual(verdict.verdict, "fail")
        self.assertEqual(verdict.blockers, ["brand_new_scanner"])

    def test_various_failure_strings_recognised(self) -> None:
        """``fail`` / ``failure`` / ``error`` / ``red`` are all failures."""
        for status in ("fail", "failure", "error", "red", "FAIL", " Failure "):
            with self.subTest(status=status):
                verdict = sr.classify_lanes(
                    self._profile(), {"codecov": status}
                )
                self.assertEqual(verdict.verdict, "fail")


class SerialisationTests(unittest.TestCase):
    """``failing_lanes_to_gate_output`` produces JSON-safe dicts."""

    def test_serialises_verdict_and_buckets(self) -> None:
        """The dict has verdict + blockers + warnings + infos keys."""
        verdict = sr.RollupVerdict(
            verdict="fail",
            blockers=["a"],
            warnings=["b"],
            infos=["c"],
        )
        out = sr.failing_lanes_to_gate_output(verdict)
        self.assertEqual(out["verdict"], "fail")
        self.assertEqual(out["blockers"], ["a"])
        self.assertEqual(out["warnings"], ["b"])
        self.assertEqual(out["infos"], ["c"])


class IterSeverityEntriesTests(unittest.TestCase):
    """``iter_severity_entries`` yields deterministic (id, severity) pairs."""

    def test_yields_sorted_by_id(self) -> None:
        """Alphabetical id order so downstream docs/dashboards are stable."""
        profile = {
            "scanners": {
                "zebra": {"severity": "info"},
                "apple": {"severity": "block"},
                "mango": {"severity": "warn"},
            },
        }
        pairs = list(sr.iter_severity_entries(profile))
        self.assertEqual(
            pairs,
            [("apple", "block"), ("mango", "warn"), ("zebra", "info")],
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
