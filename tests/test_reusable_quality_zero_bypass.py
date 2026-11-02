"""Contract tests for ``reusable-quality-zero-bypass.yml``.

Phase 4 §5.2 workflow that wraps the break-glass + skip label handlers.
The tests pin the inputs/secrets/jobs shape so future edits can't
silently drop the audit path or the env-var indirection that blocks
Semgrep CWE-78.
"""

from __future__ import absolute_import

import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "reusable-quality-zero-bypass.yml"


class ReusableBypassWorkflowTests(unittest.TestCase):
    """Invariants on the reusable-quality-zero-bypass workflow."""

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the YAML once per test class."""
        cls.text = _WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)

    def test_workflow_is_workflow_call(self) -> None:
        """Other repos invoke it via ``uses: ...``."""
        # ``on:`` parses as YAML boolean True — access via that key.
        self.assertIn("workflow_call", self.doc[True])

    def test_required_inputs_are_declared(self) -> None:
        """Every field the Python evaluator reads must be a declared input."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        for required in (
            "label",
            "pr_slug",
            "pr_number",
            "head_sha",
            "actor",
        ):
            with self.subTest(input=required):
                self.assertIn(required, inputs)
                self.assertTrue(inputs[required].get("required"))

    def test_pr_body_input_default_empty(self) -> None:
        """pr_body is optional + defaults to '' so skip-label works."""
        inputs = self.doc[True]["workflow_call"]["inputs"]
        self.assertIn("pr_body", inputs)
        self.assertEqual(inputs["pr_body"].get("default", ""), "")

    def test_bypass_audit_pat_secret_declared(self) -> None:
        """Audit writes route through a fine-grained PAT."""
        secrets = self.doc[True]["workflow_call"]["secrets"]
        self.assertIn("BYPASS_AUDIT_PAT", secrets)

    def test_env_var_indirection_for_github_context_values(self) -> None:
        """Inputs reach the Python evaluator via env: not inline run: interpolation.

        Blocks Semgrep CWE-78 run-shell-injection the same way the
        Phase 2 / 3 workflows do.
        """
        self.assertIn("QZ_LABEL:", self.text)
        self.assertIn("QZ_PR_SLUG:", self.text)
        self.assertIn("QZ_PR_BODY:", self.text)
        # Sanity: the evaluator actually reads from os.environ, not from
        # an inline interpolation.
        self.assertIn('os.environ["QZ_LABEL"]', self.text)
        self.assertIn('os.environ["QZ_PR_SLUG"]', self.text)

    def test_workflow_imports_bypass_labels_helper(self) -> None:
        """The evaluator uses the Phase 4 ``bypass_labels`` module."""
        self.assertIn("from scripts.quality import bypass_labels", self.text)

    def test_commit_step_guarded_by_pat_presence(self) -> None:
        """Audit log is committed only when ``BYPASS_AUDIT_PAT`` is set.

        The ``secrets`` context is not allowed in a step ``if:`` (it triggers
        a workflow startup_failure), so presence is surfaced via a step-level
        ``env`` boolean and the gate reads ``env`` instead.
        """
        self.assertIn(
            "HAS_AUDIT_PAT: ${{ secrets.BYPASS_AUDIT_PAT != '' }}",
            self.text,
        )
        self.assertIn(
            "if: ${{ env.HAS_AUDIT_PAT == 'true' }}",
            self.text,
        )
        # Guard against regressing to the invalid secrets-in-if form.
        self.assertNotIn(
            "if: ${{ secrets.BYPASS_AUDIT_PAT != '' }}",
            self.text,
        )

    def test_a_bypass_without_the_audit_pat_says_it_was_not_recorded(self) -> None:
        """A skipped commit step was silent: the run must say the bypass left no audit record."""
        steps = self.doc["jobs"]["bypass"]["steps"]
        names = [step.get("name", "") for step in steps]
        notice = steps[names.index("Audit record not committed (no BYPASS_AUDIT_PAT)")]
        self.assertEqual(notice["if"], "${{ env.HAS_AUDIT_PAT != 'true' }}")
        self.assertIn("::warning title=Bypass NOT recorded::", notice["run"])
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', notice["run"])
        self.assertEqual(names.index("Commit audit log"), names.index(notice["name"]) + 1)

    def test_the_audit_record_is_appended_without_a_checkout(self) -> None:
        """One read-only code checkout; the record goes through the Contents API with the PAT."""
        steps = self.doc["jobs"]["bypass"]["steps"]
        (code,) = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
        self.assertEqual(code["with"]["repository"], "${{ job.workflow_repository }}")
        self.assertEqual(code["with"]["ref"], "${{ job.workflow_sha }}")
        self.assertIs(code["with"]["persist-credentials"], False)
        self.assertNotIn("token", code["with"])
        (commit,) = [s for s in steps if s.get("name") == "Commit audit log"]
        self.assertEqual(commit["if"], "${{ env.HAS_AUDIT_PAT == 'true' }}")
        self.assertEqual(commit["env"]["GH_TOKEN"], "${{ secrets.BYPASS_AUDIT_PAT }}")
        self.assertEqual(commit["env"]["QZ_PLATFORM_REPO"], "${{ job.workflow_repository }}")
        self.assertIn("python scripts/quality/audit_log_append.py", commit["run"])
        self.assertIn("--branch main", commit["run"])
        self.assertNotIn("git push", self.text)
        self.assertNotIn("audit-store", self.text)
        # The gate's flag lives in the JOB env: a step's own env is invisible to its `if:`.
        self.assertEqual(self.doc["jobs"]["bypass"]["env"]["HAS_AUDIT_PAT"], "${{ secrets.BYPASS_AUDIT_PAT != '' }}")
        self.assertFalse(any("HAS_AUDIT_PAT" in (step.get("env") or {}) for step in steps))
        self.assertIn("QZ_PLATFORM_REPO: ${{ job.workflow_repository }}", self.text)

    def test_tracking_issue_step_runs_only_for_break_glass(self) -> None:
        """Post-merge tracking issue is opened only for break-glass labels."""
        self.assertIn(
            "inputs.label == 'quality-zero:break-glass'",
            self.text,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
