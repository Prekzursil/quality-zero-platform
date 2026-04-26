"""Contract tests for ``reusable-bumps.yml``.

Phase 5 §5.5 workflow that plans the staging + rollout waves for a
bump recipe. Pins the inputs, env-var indirection, and dry_run gate
so future edits can't silently drop the safety net.
"""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]


_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "reusable-bumps.yml"
)


class ReusableBumpsWorkflowTests(unittest.TestCase):
    """Invariants on the reusable-bumps workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_is_workflow_dispatch(self) -> None:
        """Entry is manual dispatch from the platform repo."""
        # ``on:`` parses as YAML boolean True — access via that key.
        self.assertIn("workflow_dispatch", self.doc[True])

    def test_required_recipe_path_input(self) -> None:
        """``recipe_path`` is mandatory."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertIn("recipe_path", inputs)
        self.assertTrue(inputs["recipe_path"].get("required"))

    def test_dry_run_defaults_to_true(self) -> None:
        """Safety net: dry-run is the default."""
        inputs = self.doc[True]["workflow_dispatch"]["inputs"]
        self.assertIn("dry_run", inputs)
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_env_var_indirection_inside_heredoc_body(self) -> None:
        """No ``${{ github.* }}`` / ``${{ inputs.* }}`` inside PY heredoc."""
        heredocs = re.findall(r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL)
        self.assertTrue(heredocs, "expected at least one python heredoc")
        for body in heredocs:
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ github.", body)

    def test_concurrency_key_binds_to_recipe(self) -> None:
        """Prevent two rollouts of the same recipe from racing."""
        concurrency = self.doc.get("concurrency", {})
        self.assertIn("${{ inputs.recipe_path }}", str(concurrency.get("group", "")))

    def test_plan_outputs_declared(self) -> None:
        """Downstream jobs need the plan's staging / rollout / recipe_name."""
        plan_job = self.doc["jobs"]["plan"]
        outputs = plan_job.get("outputs", {})
        for key in ("staging", "rollout", "recipe_name"):
            with self.subTest(output=key):
                self.assertIn(key, outputs)

    def test_permissions_are_narrow(self) -> None:
        """Top-level permissions empty; plan job has read-only contents."""
        self.assertEqual(self.doc.get("permissions"), {})
        plan_perms = self.doc["jobs"]["plan"].get("permissions", {})
        self.assertEqual(plan_perms.get("contents"), "read")

    def test_stage_1_calls_reusable_bump_apply_per_staging_repo(self) -> None:
        """Stage 1 fans out via matrix calling ``reusable-bump-apply.yml``.

        Closes the gap between the plan step (PR #115) and an
        actually-executable rollout: now staging_repos receive PRs
        per the recipe via the per-repo applier (PR #138).
        """
        jobs = self.doc["jobs"]
        self.assertIn("stage-1", jobs)
        stage_1 = jobs["stage-1"]
        # Matrix strategy fanning out per staging slug.
        strategy = stage_1.get("strategy", {})
        matrix = strategy.get("matrix", {})
        self.assertIn("slug", matrix)
        self.assertIs(strategy.get("fail-fast"), False,
            "fail-fast: false so one bad staging repo can't abort the wave")
        # Calls the per-repo applier reusable workflow.
        self.assertIn("reusable-bump-apply.yml", stage_1.get("uses", ""))
        # Forwards DRIFT_SYNC_PAT secret.
        self.assertIn("DRIFT_SYNC_PAT", stage_1.get("secrets", {}))
        # Skipped when staging is empty (nothing to fan out to).
        self.assertIn("staging", stage_1.get("if", ""))

    def test_stage_1_forwards_recipe_path_and_dry_run(self) -> None:
        """The stage-1 matrix passes recipe_path + dry_run through to the applier."""
        stage_1 = self.doc["jobs"]["stage-1"]
        with_block = stage_1.get("with", {})
        self.assertIn("recipe_path", with_block)
        self.assertIn("dry_run", with_block)
        self.assertIn("matrix.slug", str(with_block.get("repo_slug", "")))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
