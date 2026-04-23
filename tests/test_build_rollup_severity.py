"""Tests for the Phase 4 severity integration in ``build_quality_rollup``."""

from __future__ import absolute_import

import unittest

from scripts.quality import build_quality_rollup as br


class LaneStatusesFromRowsTests(unittest.TestCase):
    """``_lane_statuses_from_rows`` prepares the input for ``classify_lanes``."""

    def test_known_contexts_map_to_lane_ids(self) -> None:
        """Standard LANE_CONTEXTS entries resolve to their lane id."""
        reverse_map = {ctx: lane for lane, ctx in br.LANE_CONTEXTS.items()}
        rows = [
            {"context": "Codacy Zero", "status": "fail", "detail": ""},
            {"context": "Coverage 100 Gate", "status": "pass", "detail": ""},
        ]
        statuses = br._lane_statuses_from_rows(rows, reverse_map)
        self.assertEqual(statuses["codacy"], "fail")
        self.assertEqual(statuses["coverage"], "pass")

    def test_unknown_context_passes_through_under_context_name(self) -> None:
        """Contexts not in LANE_CONTEXTS keep their full name as key."""
        reverse_map = {ctx: lane for lane, ctx in br.LANE_CONTEXTS.items()}
        rows = [
            {"context": "SonarCloud Code Analysis", "status": "fail", "detail": ""},
        ]
        statuses = br._lane_statuses_from_rows(rows, reverse_map)
        self.assertEqual(statuses["SonarCloud Code Analysis"], "fail")


class BuildRollupSeverityTests(unittest.TestCase):
    """``build_rollup`` emits a severity payload + honours profile severities."""

    def _profile(self) -> dict:
        """Minimal profile with one fail-lane of each severity."""
        return {
            "slug": "Prekzursil/example",
            "active_required_contexts": [
                "Codacy Zero",
                "Coverage 100 Gate",
                "Sentry Zero",
            ],
            "scanners": {
                "codacy": {"severity": "block"},
                "coverage": {"severity": "warn"},
                "sentry": {"severity": "info"},
            },
        }

    def test_warn_severity_fail_softens_overall(self) -> None:
        """Only warn/info failures → overall softens to ``warn`` or ``pass``."""
        profile = self._profile()
        contexts = {
            "Codacy Zero": {"source": "check_run", "state": "completed", "conclusion": "success"},
            "Coverage 100 Gate": {"source": "check_run", "state": "completed", "conclusion": "failure"},
            "Sentry Zero": {"source": "check_run", "state": "completed", "conclusion": "failure"},
        }
        # lane_payloads left empty → rows fall back to context statuses
        payload = br.build_rollup(
            profile=profile, lane_payloads={}, contexts=contexts, sha="abc",
        )
        # One warn-severity failure + one info-severity failure → overall warn
        self.assertEqual(payload["status"], "warn")
        self.assertEqual(payload["severity"]["verdict"], "warn")
        self.assertEqual(payload["severity"]["warnings"], ["coverage"])
        self.assertEqual(payload["severity"]["infos"], ["sentry"])
        self.assertEqual(payload["severity"]["blockers"], [])

    def test_block_severity_fail_still_fails(self) -> None:
        """One block-severity failure still yields overall=fail."""
        profile = self._profile()
        contexts = {
            "Codacy Zero": {"source": "check_run", "state": "completed", "conclusion": "failure"},
            "Coverage 100 Gate": {"source": "check_run", "state": "completed", "conclusion": "success"},
            "Sentry Zero": {"source": "check_run", "state": "completed", "conclusion": "success"},
        }
        payload = br.build_rollup(
            profile=profile, lane_payloads={}, contexts=contexts, sha="abc",
        )
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["severity"]["verdict"], "fail")
        self.assertEqual(payload["severity"]["blockers"], ["codacy"])

    def test_info_only_failures_pass(self) -> None:
        """Info-only failures → overall=pass (gate unchanged)."""
        profile = self._profile()
        contexts = {
            "Codacy Zero": {"source": "check_run", "state": "completed", "conclusion": "success"},
            "Coverage 100 Gate": {"source": "check_run", "state": "completed", "conclusion": "success"},
            "Sentry Zero": {"source": "check_run", "state": "completed", "conclusion": "failure"},
        }
        payload = br.build_rollup(
            profile=profile, lane_payloads={}, contexts=contexts, sha="abc",
        )
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["severity"]["infos"], ["sentry"])

    def test_payload_includes_severity_block(self) -> None:
        """Every rollup has a ``severity`` sub-dict with the expected keys."""
        payload = br.build_rollup(
            profile={"slug": "x", "active_required_contexts": []},
            lane_payloads={}, contexts={}, sha="s",
        )
        self.assertIn("severity", payload)
        self.assertIn("verdict", payload["severity"])
        self.assertIn("blockers", payload["severity"])
        self.assertIn("warnings", payload["severity"])
        self.assertIn("infos", payload["severity"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
