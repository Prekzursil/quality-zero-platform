"""Tests for Phase 5 ``scripts.quality.bump_rollout`` — rollout planner."""

from __future__ import absolute_import

import unittest

from scripts.quality import bump_rollout


def _recipe(
    staging=("Prekzursil/staging-a",),
    stacks=("fullstack-web",),
    full_rollout_after_staging=True,
) -> dict:
    """Helper: build a minimal valid recipe for planner tests."""
    return {
        "name": "Node 20 -> 24",
        "target": [
            {"file_glob": "**/ci.yml", "yaml_path": "x.y", "value": "24"},
        ],
        "affects_stacks": list(stacks),
        "staging_repos": list(staging),
        "full_rollout_after_staging": full_rollout_after_staging,
        "rollback_on_failure": True,
    }


class PlanRolloutTests(unittest.TestCase):
    """``plan_rollout`` splits affected fleet into staging + rollout waves."""

    def test_staging_repos_excluded_from_rollout(self) -> None:
        """A staging repo never appears in the full-rollout wave."""
        recipe = _recipe(
            staging=("Prekzursil/staging-a",),
            stacks=("fullstack-web",),
        )
        fleet = [
            {"slug": "Prekzursil/staging-a", "stack": "fullstack-web"},
            {"slug": "Prekzursil/repo-b", "stack": "fullstack-web"},
            {"slug": "Prekzursil/repo-c", "stack": "go"},
        ]
        plan = bump_rollout.plan_rollout(recipe=recipe, fleet=fleet)
        self.assertEqual(plan["staging"], ["Prekzursil/staging-a"])
        self.assertEqual(plan["rollout"], ["Prekzursil/repo-b"])

    def test_rollout_filters_by_affected_stacks(self) -> None:
        """Fleet repos outside affects_stacks never enter the rollout wave."""
        recipe = _recipe(stacks=("fullstack-web",), staging=())
        fleet = [
            {"slug": "Prekzursil/go-repo", "stack": "go"},
            {"slug": "Prekzursil/rust-repo", "stack": "rust"},
        ]
        plan = bump_rollout.plan_rollout(recipe=recipe, fleet=fleet)
        self.assertEqual(plan["rollout"], [])

    def test_rollout_suppressed_when_flag_false(self) -> None:
        """``full_rollout_after_staging: false`` → empty rollout wave."""
        recipe = _recipe(full_rollout_after_staging=False)
        fleet = [
            {"slug": "Prekzursil/staging-a", "stack": "fullstack-web"},
            {"slug": "Prekzursil/repo-b", "stack": "fullstack-web"},
        ]
        plan = bump_rollout.plan_rollout(recipe=recipe, fleet=fleet)
        self.assertEqual(plan["staging"], ["Prekzursil/staging-a"])
        self.assertEqual(plan["rollout"], [])

    def test_duplicate_staging_dedup_preserves_order(self) -> None:
        """``staging_repos`` with dupes/whitespace are cleaned + deduped."""
        recipe = _recipe(staging=("Prekzursil/a", "Prekzursil/a"))
        plan = bump_rollout.plan_rollout(recipe=recipe, fleet=[])
        self.assertEqual(plan["staging"], ["Prekzursil/a"])

    def test_multiple_affected_stacks(self) -> None:
        """Repos in any of the affected stacks are included in rollout."""
        recipe = _recipe(stacks=("fullstack-web", "react-vite-vitest"))
        fleet = [
            {"slug": "Prekzursil/a", "stack": "fullstack-web"},
            {"slug": "Prekzursil/b", "stack": "react-vite-vitest"},
            {"slug": "Prekzursil/c", "stack": "go"},
        ]
        plan = bump_rollout.plan_rollout(recipe=recipe, fleet=fleet)
        self.assertEqual(plan["rollout"], ["Prekzursil/a", "Prekzursil/b"])


class ClassifyStagingOutcomeTests(unittest.TestCase):
    """``classify_staging_outcome`` decides rollout vs rollback."""

    def test_all_staging_green_means_rollout(self) -> None:
        """100% staging success → ``proceed_to_rollout=True``."""
        outcome = bump_rollout.classify_staging_outcome(staging_results=[
            {"slug": "a/b", "conclusion": "success"},
            {"slug": "c/d", "conclusion": "success"},
        ])
        self.assertTrue(outcome["proceed_to_rollout"])
        self.assertFalse(outcome["rollback_required"])
        self.assertEqual(outcome["failed_repos"], [])

    def test_any_staging_failure_means_rollback(self) -> None:
        """Any non-success conclusion → rollback path."""
        outcome = bump_rollout.classify_staging_outcome(staging_results=[
            {"slug": "a/b", "conclusion": "success"},
            {"slug": "c/d", "conclusion": "failure"},
        ])
        self.assertFalse(outcome["proceed_to_rollout"])
        self.assertTrue(outcome["rollback_required"])
        self.assertEqual(outcome["failed_repos"], ["c/d"])

    def test_empty_staging_results_is_neither_rollout_nor_rollback(self) -> None:
        """No results yet → wait state (both False)."""
        outcome = bump_rollout.classify_staging_outcome(staging_results=[])
        self.assertFalse(outcome["proceed_to_rollout"])
        self.assertFalse(outcome["rollback_required"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
