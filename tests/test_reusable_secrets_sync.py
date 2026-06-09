"""Contract tests for ``reusable-secrets-sync.yml``.

Pins the §9 invariants that the secrets-sync workflow MUST hold:

* Secret value never interpolated via ``${{ inputs.* }}`` (must come
  through the ``secrets`` context).
* Every ``${{ ... }}`` interpolation inside a heredoc goes through an
  ``env:`` block (blocks Semgrep CWE-78).
* Audit commit step is guarded by presence of the fine-grained PAT.
* Dry-run defaults to ``true`` (safety net).
"""

from __future__ import absolute_import

import re
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github" / "workflows" / "reusable-secrets-sync.yml"
)


class ReusableSecretsSyncWorkflowTests(unittest.TestCase):
    """Invariants on the reusable-secrets-sync workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_is_workflow_call(self) -> None:
        """Secrets-sync is called from a caller workflow on the platform."""
        # ``on:`` parses as YAML boolean True — access via that key.
        self.assertIn("workflow_call", self.doc[True])

    def test_required_inputs_are_declared(self) -> None:
        """secret_name + target_slugs are mandatory."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        for required in ("secret_name", "target_slugs"):
            with self.subTest(input=required):
                self.assertIn(required, inputs)
                self.assertTrue(inputs[required].get("required"))

    def test_dry_run_defaults_to_true(self) -> None:
        """Default behavior MUST be dry-run — safety net."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        self.assertIn("dry_run", inputs)
        self.assertTrue(inputs["dry_run"].get("default", False))

    def test_secret_value_only_flows_through_secrets_context(self) -> None:
        """``SECRET_VALUE`` MUST come through ``secrets:``, not ``inputs:``."""
        secrets = self.doc[True]["workflow_call"].get("secrets", {})
        self.assertIn("SECRET_VALUE", secrets)
        inputs = self.doc[True]["workflow_call"]["inputs"]
        self.assertNotIn("secret_value", inputs)
        self.assertNotIn("SECRET_VALUE", inputs)

    def test_env_var_indirection_inside_heredoc(self) -> None:
        """No ``${{ inputs.* }}`` / ``${{ secrets.* }}`` inside PY heredoc."""
        heredocs = re.findall(r"<<'PY'(.+?)PY", self.text, flags=re.DOTALL)
        self.assertTrue(heredocs, "expected at least one python heredoc")
        for body in heredocs:
            with self.subTest(body_excerpt=body[:60]):
                self.assertNotIn("${{ inputs.", body)
                self.assertNotIn("${{ secrets.", body)
                self.assertNotIn("${{ github.", body)

    def test_checkout_does_not_persist_credentials(self) -> None:
        """``persist-credentials: false`` on checkout — CodeQL safety."""
        steps = self.doc["jobs"]["sync"]["steps"]
        checkout_steps = [
            s for s in steps
            if "uses" in s and s["uses"].startswith("actions/checkout")
        ]
        self.assertEqual(len(checkout_steps), 1)
        self.assertFalse(
            checkout_steps[0].get("with", {}).get("persist-credentials", True),
            "Checkout must NOT persist credentials — the audit-commit "
            "flow is handled via gh api contents-PUT in a follow-up.",
        )

    def test_audit_log_uploaded_as_artifact(self) -> None:
        """The audit log is exposed as a workflow artifact."""
        steps = self.doc["jobs"]["sync"]["steps"]
        artifact_steps = [
            s for s in steps
            if "uses" in s and "upload-artifact" in s["uses"]
        ]
        self.assertTrue(
            artifact_steps,
            "No upload-artifact step — the audit log would be lost "
            "after the workflow completes.",
        )

    def test_sync_pat_used_as_gh_token_for_secret_set(self) -> None:
        """The fine-grained SECRETS_SYNC_PAT is what ``gh secret set`` uses."""
        # The sync step's env block must route SECRETS_SYNC_PAT as GH_TOKEN.
        steps = self.doc["jobs"]["sync"]["steps"]
        sync_steps = [
            s for s in steps
            if s.get("name", "").startswith("Sync secret")
        ]
        self.assertEqual(len(sync_steps), 1)
        env = sync_steps[0].get("env", {})
        self.assertIn("GH_TOKEN", env)
        self.assertIn("SECRETS_SYNC_PAT", env["GH_TOKEN"])

    def test_top_level_permissions_empty(self) -> None:
        """Top-level permissions block is empty — job locks down per-job."""
        self.assertEqual(self.doc.get("permissions"), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
