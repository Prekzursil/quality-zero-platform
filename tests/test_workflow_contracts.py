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
            self.assertIn("platform_repository: ${{ github.repository }}", text, path.name)

    def test_self_wrapper_workflows_use_current_ref_for_platform_checkout(self) -> None:
        workflow_expectations = {
            "quality-zero-platform.yml": "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "quality-zero-gate.yml": "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "quality-zero-remediation.yml": "platform_ref: ${{ github.event.workflow_run.head_sha || github.sha }}",
            "quality-zero-backlog.yml": "platform_ref: ${{ github.sha }}",
        }

        for name, expected in workflow_expectations.items():
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn(expected, text, name)

    def test_manual_wrapper_dispatches_do_not_expose_user_supplied_inputs(self) -> None:
        workflow_paths = [
            ROOT / ".github" / "workflows" / "quality-zero-remediation.yml",
            ROOT / ".github" / "workflows" / "quality-zero-backlog.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("workflow_dispatch:\n    inputs:", text, path.name)

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
        self.assertIn("dtolnay/rust-toolchain@631a55b12751854ce901bb631d5902ceb48146f7", text)

        backlog_text = (ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml").read_text(encoding="utf-8")
        remediation_text = (ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml").read_text(encoding="utf-8")
        self.assertIn("peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c", backlog_text)
        self.assertIn("peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c", remediation_text)

    def test_scanner_matrix_exports_provider_credentials_to_lane_runtime(self) -> None:
        text = (ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml").read_text(encoding="utf-8")
        for expected in [
            "SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}",
            "CODACY_API_TOKEN: ${{ secrets.CODACY_API_TOKEN }}",
            "SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}",
            "DEEPSCAN_API_TOKEN: ${{ secrets.DEEPSCAN_API_TOKEN }}",
            "GITHUB_TOKEN: ${{ github.token }}",
            "REPO_SLUG: ${{ inputs.repo_slug }}",
            "TARGET_SHA: ${{ inputs.sha != '' && inputs.sha || github.sha }}",
            "SENTRY_ORG: ${{ vars.SENTRY_ORG }}",
            "SENTRY_PROJECT: ${{ vars.SENTRY_PROJECT }}",
            "DEEPSCAN_POLICY_MODE: ${{ vars.DEEPSCAN_POLICY_MODE }}",
            "DEEPSCAN_OPEN_ISSUES_URL: ${{ vars.DEEPSCAN_OPEN_ISSUES_URL }}",
        ]:
            self.assertIn(expected, text)

    def test_semgrep_lane_uses_supported_cli_invocation(self) -> None:
        text = (ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml").read_text(encoding="utf-8")
        self.assertIn('run(["semgrep", "ci"], cwd=repo_dir)', text)
        self.assertNotIn('run(["semgrep", "ci", "--error"], cwd=repo_dir)', text)

    def test_reusable_workflows_do_not_inline_inputs_inside_run_blocks(self) -> None:
        workflow_expectations = {
            "reusable-quality-zero-gate.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                '--event-name "${{ inputs.event_name }}"',
            ],
            "reusable-ruleset-sync.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                'f"repos/${{ inputs.repo_slug }}/rulesets"',
            ],
            "reusable-scanner-matrix.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                '--event-name "${{ inputs.event_name }}"',
            ],
        }

        for name, forbidden_snippets in workflow_expectations.items():
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                self.assertNotIn(snippet, text, f"{name}: {snippet}")

    def test_quality_zero_gate_exports_github_token_to_required_check_probe(self) -> None:
        text = (ROOT / ".github" / "workflows" / "reusable-quality-zero-gate.yml").read_text(encoding="utf-8")
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", text)


if __name__ == "__main__":
    unittest.main()
