from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_reusable_mutation_workflows_do_not_reference_openai_api_key_lane(self) -> None:
        workflow_paths = [
            ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml",
            ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("openai/codex-action", text, path.name)
            self.assertNotIn("OPENAI_API_KEY", text, path.name)
            self.assertIn("run_codex_exec.py", text, path.name)
            self.assertIn("CODEX_AUTH_JSON", text, path.name)
            self.assertIn("codex_runner_labels_json", text, path.name)

    def test_self_wrapper_workflows_do_not_inherit_all_secrets(self) -> None:
        workflow_paths = [
            ROOT / ".github" / "workflows" / "quality-zero-platform.yml",
            ROOT / ".github" / "workflows" / "quality-zero-gate.yml",
            ROOT / ".github" / "workflows" / "quality-zero-remediation.yml",
            ROOT / ".github" / "workflows" / "quality-zero-backlog.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("secrets: inherit", text, path.name)

    def test_mutation_workflows_sanitize_user_controlled_inputs_in_run_blocks(self) -> None:
        workflow_paths = [
            ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml",
            ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml",
        ]

        forbidden_snippets = [
            '${{ inputs.repo_slug }}',
            '${{ inputs.failure_context }}',
            '${{ inputs.lane }}',
            '${{ inputs.sha || github.sha }}',
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                self.assertNotIn(f'--repo-slug "{snippet}"', text, path.name)
                self.assertNotIn(f'--failure-context "{snippet}"', text, path.name)
                self.assertNotIn(f'--artifact "Target SHA: {snippet}"', text, path.name)

    def test_scanner_matrix_pins_qlty_coverage_action_to_full_sha(self) -> None:
        text = (ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml").read_text(encoding="utf-8")
        self.assertIn("qltysh/qlty-action/coverage@a19242102d17e497f437d7466aa01b528537e899", text)


if __name__ == "__main__":
    unittest.main()
