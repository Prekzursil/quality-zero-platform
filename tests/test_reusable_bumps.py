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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
