"""Tests for Phase 5 ``scripts.quality.bootstrap_repo``.

Exercises the two primitives the ``reusable-bootstrap-repo.yml``
workflow relies on:

* ``count_consecutive_green_shadow_runs`` — walks CI history on the
  onboarding repo's default branch from newest to oldest and counts
  how many consecutive runs had ``conclusion == "success"`` for the
  named workflow. Stops at the first non-green or non-existent run.
* ``promote_profile`` — applies the shadow→target mode flip to the
  profile YAML text (preserves everything else, updates only the
  ``mode.phase`` line and clears ``mode.shadow_until``).
"""

from __future__ import absolute_import

import json
import subprocess
import unittest
from unittest.mock import MagicMock

from scripts.quality import bootstrap_repo as br


def _fake_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Helper: lightweight ``CompletedProcess`` double for runner mocks."""
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr="",
    )


class CountConsecutiveGreenShadowRunsTests(unittest.TestCase):
    """``count_consecutive_green_shadow_runs`` walks gh run list output."""

    def test_all_green_returns_full_count(self) -> None:
        """3/3 newest-first green runs → 3."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
        ])))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 3)

    def test_stops_at_first_failure(self) -> None:
        """Greens before a failure → up to the failure only."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "failure", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
        ])))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 2)

    def test_in_progress_run_is_skipped_not_counted(self) -> None:
        """An ``in_progress`` run is neither counted nor a stopper."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"conclusion": "", "status": "in_progress"},
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
        ])))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 2)

    def test_empty_history_returns_zero(self) -> None:
        """``gh run list`` returning ``[]`` → 0 green runs."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([])))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 0)

    def test_empty_stdout_returns_zero(self) -> None:
        """Defensive: empty gh stdout → 0."""
        runner = MagicMock(return_value=_fake_completed(""))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 0)

    def test_non_list_payload_returns_zero(self) -> None:
        """Defensive: gh returns non-list JSON → 0."""
        runner = MagicMock(return_value=_fake_completed('{"error": "nope"}'))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 0)

    def test_non_dict_element_breaks_the_streak(self) -> None:
        """Defensive: a non-mapping element interrupts counting."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"conclusion": "success", "status": "completed"},
            "not-a-dict",
            {"conclusion": "success", "status": "completed"},
        ])))
        count = br.count_consecutive_green_shadow_runs(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            runner=runner,
        )
        self.assertEqual(count, 1)


class ShouldPromoteTests(unittest.TestCase):
    """``should_promote`` is the 3-green-in-a-row gate predicate."""

    def test_three_greens_default_threshold_promotes(self) -> None:
        """3 ≥ default threshold of 3 → True."""
        self.assertTrue(br.should_promote(green_run_count=3))

    def test_more_than_threshold_promotes(self) -> None:
        """Any excess over threshold still promotes."""
        self.assertTrue(br.should_promote(green_run_count=10))

    def test_below_default_threshold_does_not_promote(self) -> None:
        """2/3 → False."""
        self.assertFalse(br.should_promote(green_run_count=2))

    def test_custom_threshold_honoured(self) -> None:
        """Caller can relax/tighten the threshold."""
        self.assertTrue(br.should_promote(green_run_count=5, required=5))
        self.assertFalse(br.should_promote(green_run_count=4, required=5))


class PromoteProfileTests(unittest.TestCase):
    """``promote_profile`` flips the mode.phase + clears shadow_until."""

    def test_shadow_to_absolute_flips_phase_and_clears_shadow_until(self) -> None:
        """``phase: shadow`` + ``shadow_until`` → ``absolute`` + null."""
        src = (
            "slug: Prekzursil/event-link\n"
            "mode:\n"
            "  phase: shadow\n"
            "  shadow_until: 2026-06-30\n"
            "coverage:\n"
            "  min_percent: 100.0\n"
        )
        out = br.promote_profile(src, target_phase="absolute")
        self.assertIn("phase: absolute", out)
        self.assertIn("shadow_until: null", out)
        self.assertNotIn("phase: shadow\n", out)
        self.assertIn("slug: Prekzursil/event-link", out)
        self.assertIn("min_percent: 100.0", out)

    def test_shadow_to_ratchet_also_clears_shadow_until(self) -> None:
        """Target ``ratchet`` clears the shadow deadline field."""
        src = (
            "mode:\n"
            "  phase: shadow\n"
            "  shadow_until: 2026-06-30\n"
        )
        out = br.promote_profile(src, target_phase="ratchet")
        self.assertIn("phase: ratchet", out)
        self.assertIn("shadow_until: null", out)

    def test_rejects_invalid_target_phase(self) -> None:
        """Target phase must be ``ratchet`` or ``absolute``."""
        with self.assertRaises(ValueError):
            br.promote_profile("mode:\n  phase: shadow\n", target_phase="nothing")

    def test_rejects_source_that_is_not_shadow(self) -> None:
        """Promoting an already-absolute profile is a no-op signal → ValueError."""
        with self.assertRaises(ValueError):
            br.promote_profile(
                "mode:\n  phase: absolute\n  shadow_until: null\n",
                target_phase="absolute",
            )

    def test_preserves_lines_other_than_mode(self) -> None:
        """Everything outside ``mode:`` is preserved byte-for-byte."""
        src = (
            "slug: org/repo\n"
            "stack: go\n"
            "mode:\n"
            "  phase: shadow\n"
            "  shadow_until: 2026-12-31\n"
            "scanners:\n"
            "  codeql: { enabled: true, severity: block }\n"
        )
        out = br.promote_profile(src, target_phase="absolute")
        # Non-mode lines unchanged.
        self.assertIn("slug: org/repo\n", out)
        self.assertIn("stack: go\n", out)
        self.assertIn(
            "scanners:\n  codeql: { enabled: true, severity: block }\n", out,
        )


class ComputePromotionPlanTests(unittest.TestCase):
    """``compute_promotion_plan`` combines the primitives into a decision."""

    def test_plan_ready_when_enough_greens(self) -> None:
        """3 greens + target → ``ready=True``, promoted YAML included."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "success", "status": "completed"},
        ])))
        plan = br.compute_promotion_plan(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            profile_yaml=(
                "slug: Prekzursil/event-link\n"
                "mode:\n"
                "  phase: shadow\n"
                "  shadow_until: 2026-06-30\n"
            ),
            target_phase="absolute",
            runner=runner,
        )
        self.assertTrue(plan["ready"])
        self.assertEqual(plan["green_runs"], 3)
        self.assertIn("phase: absolute", plan["promoted_yaml"])

    def test_plan_not_ready_when_insufficient_greens(self) -> None:
        """< 3 greens → ``ready=False``, no promoted YAML."""
        runner = MagicMock(return_value=_fake_completed(json.dumps([
            {"conclusion": "success", "status": "completed"},
            {"conclusion": "failure", "status": "completed"},
        ])))
        plan = br.compute_promotion_plan(
            slug="Prekzursil/event-link",
            workflow="quality-rollup.yml",
            branch="main",
            profile_yaml="mode:\n  phase: shadow\n  shadow_until: null\n",
            target_phase="absolute",
            runner=runner,
        )
        self.assertFalse(plan["ready"])
        self.assertEqual(plan["green_runs"], 1)
        self.assertEqual(plan["promoted_yaml"], "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
