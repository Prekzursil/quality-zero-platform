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


if __name__ == "__main__":
    unittest.main()
