"""Test workflow contracts."""

from __future__ import absolute_import, division

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUTATION_TEMPLATE_REF = "@7268fee30f1cf796938d97fe460259f27386a8cd"


class WorkflowContractTests(unittest.TestCase):
    """Protect the platform's workflow wrapper and reusable contract invariants."""

    def test_reusable_mutation_workflows_do_not_reference_openai_api_key_lane(
        self,
    ) -> None:
        """Keep mutation workflows on Codex auth instead of deprecated OpenAI secret lanes."""
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
        """Ensure top-level wrappers pass only the secrets they actually need."""
        workflow_paths = [
            ROOT / ".github" / "workflows" / "quality-zero-platform.yml",
            ROOT / ".github" / "workflows" / "quality-zero-gate.yml",
            ROOT / ".github" / "workflows" / "codecov-analytics.yml",
            ROOT / ".github" / "workflows" / "quality-zero-remediation.yml",
            ROOT / ".github" / "workflows" / "quality-zero-backlog.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("secrets: inherit", text, path.name)
            if path.name in {"quality-zero-platform.yml", "quality-zero-gate.yml"}:
                self.assertIn(
                    "platform_repository: ${{ github.repository }}", text, path.name
                )

    def test_repo_template_parity_wrappers_pin_controller_and_do_not_inherit_all_secrets(
        self,
    ) -> None:
        """Keep repo templates pinned to the platform controller SHA and explicit secrets."""
        workflow_paths = [
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "quality-zero-platform.yml",
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "quality-zero-gate.yml",
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "codecov-analytics.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("secrets: inherit", text, path.name)
            self.assertIn("@0e7482ede8d157d5183d41dfe2b575560fbea222", text, path.name)
            self.assertIn(
                "platform_repository: Prekzursil/quality-zero-platform", text, path.name
            )
            self.assertIn("platform_ref: main", text, path.name)

    def test_repo_template_parity_wrappers_set_explicit_top_level_permissions(
        self,
    ) -> None:
        """Require the published repo templates to declare explicit wrapper permissions."""
        workflow_expectations = {
            "quality-zero-platform.yml": [
                "permissions:",
                "  contents: read",
                "  id-token: write",
                "  pull-requests: write",
                "      pull-requests: write",
            ],
            "quality-zero-gate.yml": [
                "permissions:",
                "  contents: read",
            ],
            "codecov-analytics.yml": [
                "permissions:",
                "  contents: read",
                "  id-token: write",
            ],
        }

        for name, expected_lines in workflow_expectations.items():
            text = (
                ROOT / "templates" / "repo" / ".github" / "workflows" / name
            ).read_text(encoding="utf-8")
            for expected in expected_lines:
                self.assertIn(expected, text, name)

    def test_repo_template_mutation_wrappers_pin_controller_and_pass_only_required_inputs_and_secrets(
        self,
    ) -> None:
        """Verify mutation templates stay pinned and expose only trusted inputs and secrets."""
        workflow_expectations = {
            "quality-zero-backlog.yml": [
                MUTATION_TEMPLATE_REF,
                "permissions:",
                "  contents: write",
                "  pull-requests: write",
                "workflow_dispatch:",
                "lane: quality",
                "CODEX_AUTH_JSON: ${{ secrets.CODEX_AUTH_JSON }}",
            ],
            "quality-zero-remediation.yml": [
                MUTATION_TEMPLATE_REF,
                "permissions:",
                "  contents: write",
                "  pull-requests: write",
                "workflow_dispatch:",
                "failure_context: Quality Zero Gate",
                "CODEX_AUTH_JSON: ${{ secrets.CODEX_AUTH_JSON }}",
                "workflow_run: # zizmor: ignore[dangerous-triggers]",
            ],
        }

        for name, expected_lines in workflow_expectations.items():
            text = (
                ROOT / "templates" / "repo" / ".github" / "workflows" / name
            ).read_text(encoding="utf-8")
            self.assertNotIn("secrets: inherit", text, name)
            self.assertNotIn("workflow_dispatch:\n    inputs:", text, name)
            self.assertNotIn("inputs.tool", text, name)
            self.assertNotIn("inputs.failure_context", text, name)
            for expected in expected_lines:
                self.assertIn(expected, text, name)

    def test_self_remediation_wrapper_documents_trusted_workflow_run_trigger(
        self,
    ) -> None:
        """Document the trusted workflow_run trigger for the self-remediation wrapper."""
        text = (
            ROOT / ".github" / "workflows" / "quality-zero-remediation.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_run: # zizmor: ignore[dangerous-triggers]", text)

    def test_self_wrapper_workflows_use_current_ref_for_platform_checkout(self) -> None:
        """Check self-hosted wrappers out at the current controller ref for the running event."""
        workflow_expectations = {
            "quality-zero-platform.yml": "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "quality-zero-gate.yml": "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "codecov-analytics.yml": "platform_ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            "quality-zero-remediation.yml": "platform_ref: ${{ github.event.workflow_run.head_sha || github.sha }}",
            "quality-zero-backlog.yml": "platform_ref: ${{ github.sha }}",
        }

        for name, expected in workflow_expectations.items():
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn(expected, text, name)

    def test_manual_wrapper_dispatches_do_not_expose_user_supplied_inputs(self) -> None:
        """Keep manual mutation wrappers free of attacker-controlled workflow_dispatch inputs."""
        workflow_paths = [
            ROOT / ".github" / "workflows" / "quality-zero-remediation.yml",
            ROOT / ".github" / "workflows" / "quality-zero-backlog.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("workflow_dispatch:\n    inputs:", text, path.name)

    def test_mutation_workflows_sanitize_user_controlled_inputs_in_run_blocks(
        self,
    ) -> None:
        """Reject direct interpolation of untrusted workflow inputs inside shell commands."""
        workflow_paths = [
            ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml",
            ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml",
        ]

        forbidden_snippets = [
            "${{ inputs.repo_slug }}",
            "${{ inputs.failure_context }}",
            "${{ inputs.lane }}",
            "${{ inputs.sha || github.sha }}",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                self.assertNotIn(f'--repo-slug "{snippet}"', text, path.name)
                self.assertNotIn(f'--failure-context "{snippet}"', text, path.name)
                self.assertNotIn(f'--artifact "Target SHA: {snippet}"', text, path.name)

    def test_scanner_matrix_pins_qlty_actions_to_full_sha(self) -> None:
        """Pin QLTY and PR creation actions to immutable full SHAs."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "qltysh/qlty-action/install@a19242102d17e497f437d7466aa01b528537e899", text
        )
        self.assertIn(
            "matrix.lane == 'qlty_zero' || (matrix.lane == 'coverage' && steps.profile.outputs.qlty_coverage_files != '' && runner.os != 'Windows')",
            text,
        )
        self.assertIn('qlty_executable = shutil.which("qlty")', text)
        self.assertIn("normalize_coverage_for_qlty.py", text)
        self.assertIn(
            "subprocess.run(command, executable=qlty_executable, check=True)", text
        )
        self.assertIn("job_name: QLTY Zero", text)
        self.assertIn("lane: qlty_zero", text)
        self.assertIn("run_qlty_zero.py", text)
        self.assertIn("job_name: DeepSource Visible Zero", text)
        self.assertIn("lane: deepsource_visible", text)
        self.assertIn("check_deepsource_zero.py", text)
        self.assertIn(
            "dtolnay/rust-toolchain@631a55b12751854ce901bb631d5902ceb48146f7", text
        )

        backlog_text = (
            ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml"
        ).read_text(encoding="utf-8")
        remediation_text = (
            ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c",
            backlog_text,
        )
        self.assertIn(
            "peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c",
            remediation_text,
        )

    def test_scanner_matrix_pins_codecov_upload_and_keeps_token_optional(self) -> None:
        """Keep Codecov uploads pinned and compatible with optional tokenless OIDC mode."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("CODECOV_TOKEN:", text)
        self.assertIn("required: false", text)
        self.assertIn(
            "codecov/codecov-action@671740ac38dd9b0130fbe1cec585b89eea48d3de", text
        )
        self.assertIn("use_oidc: ${{ secrets.CODECOV_TOKEN == '' }}", text)
        self.assertIn("token: ${{ secrets.CODECOV_TOKEN }}", text)
        self.assertIn("files: ${{ steps.profile.outputs.coverage_input_files }}", text)

        wrapper_text = (
            ROOT / ".github" / "workflows" / "quality-zero-platform.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}", wrapper_text)

        analytics_text = (
            ROOT / ".github" / "workflows" / "codecov-analytics.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}", analytics_text)

        reusable_text = (
            ROOT / ".github" / "workflows" / "reusable-codecov-analytics.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("required: false", reusable_text)
        self.assertIn(
            "codecov/codecov-action@671740ac38dd9b0130fbe1cec585b89eea48d3de",
            reusable_text,
        )
        self.assertIn("use_oidc: ${{ secrets.CODECOV_TOKEN == '' }}", reusable_text)
        self.assertIn("token: ${{ secrets.CODECOV_TOKEN }}", reusable_text)
        self.assertIn(
            "files: ${{ steps.profile.outputs.coverage_input_files }}", reusable_text
        )
        self.assertEqual(reusable_text.count("persist-credentials: false"), 2)
        self.assertIn(
            'profile_path = Path(os.environ["RUNNER_TEMP"]) / "profile.json"',
            reusable_text,
        )
        self.assertIn(
            'coverage = json.loads(profile_path.read_text(encoding="utf-8")).get("coverage", {})',
            reusable_text,
        )

    def test_scanner_matrix_exports_provider_credentials_to_lane_runtime(self) -> None:
        """Expose the provider credentials and repo metadata required by scanner lanes."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        for expected in [
            "SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}",
            "CODACY_API_TOKEN: ${{ secrets.CODACY_API_TOKEN }}",
            "SENTRY_AUTH_TOKEN: ${{ secrets.SENTRY_AUTH_TOKEN }}",
            "DEEPSCAN_API_TOKEN: ${{ secrets.DEEPSCAN_API_TOKEN }}",
            "GITHUB_TOKEN: ${{ github.token }}",
            "REPO_SLUG: ${{ inputs.repo_slug }}",
            "TARGET_SHA: ${{ inputs.sha != '' && inputs.sha || github.sha }}",
            "BRANCH_NAME: ${{ inputs.branch_name != '' && inputs.branch_name || github.head_ref || github.ref_name }}",
            "PULL_REQUEST_NUMBER: ${{ inputs.pull_request_number }}",
            "SENTRY_ORG: ${{ vars.SENTRY_ORG }}",
            "SENTRY_PROJECT: ${{ vars.SENTRY_PROJECT }}",
            "DEEPSCAN_POLICY_MODE: ${{ vars.DEEPSCAN_POLICY_MODE }}",
            "DEEPSCAN_OPEN_ISSUES_URL: ${{ vars.DEEPSCAN_OPEN_ISSUES_URL }}",
        ]:
            self.assertIn(expected, text)
        self.assertIn("PULL_REQUEST_AUTHOR: ${{ inputs.pull_request_author }}", text)
        self.assertIn(
            "PULL_REQUEST_HEAD_REF: ${{ inputs.pull_request_head_ref != '' && inputs.pull_request_head_ref || github.head_ref || '' }}",
            text,
        )

    def test_scanner_matrix_checks_out_repo_at_requested_sha(self) -> None:
        """Ensure scanner lanes analyze the exact requested commit without persisted credentials."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("ref: ${{ inputs.sha != '' && inputs.sha || github.sha }}", text)
        self.assertEqual(text.count("persist-credentials: false"), 3)

    def test_scanner_matrix_uses_configurable_runner_for_coverage_lane(self) -> None:
        """Allow the coverage lane to select its dedicated runner configuration."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("matrix.lane == 'coverage'", text)
        self.assertIn("coverage_runner", text)
        self.assertIn("windows-latest", text)

    def test_scanner_matrix_scopes_sonar_and_codacy_zero_to_the_current_pull_request(
        self,
    ) -> None:
        """Scope PR-aware scanners to the current PR metadata when that context exists."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'pull_request_number = os.environ.get("PULL_REQUEST_NUMBER", "").strip()',
            text,
        )
        self.assertIn('branch_name = os.environ.get("BRANCH_NAME", "").strip()', text)
        self.assertIn('cmd.extend(["--pull-request", pull_request_number])', text)
        self.assertIn('cmd.extend(["--branch", branch_name])', text)
        wrapper_text = (
            ROOT / ".github" / "workflows" / "quality-zero-platform.yml"
        ).read_text(encoding="utf-8")
        for expected in [
            "branch_name: ${{ github.head_ref || github.ref_name }}",
            "pull_request_number: ${{ github.event.pull_request.number || '' }}",
            "pull_request_author: ${{ github.event.pull_request.user.login || '' }}",
            "pull_request_head_ref: ${{ github.event.pull_request.head.ref || github.head_ref || '' }}",
        ]:
            self.assertIn(expected, wrapper_text)

        template_text = (
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "quality-zero-platform.yml"
        ).read_text(encoding="utf-8")
        for expected in [
            "branch_name: ${{ github.head_ref || github.ref_name }}",
            "pull_request_number: ${{ github.event.pull_request.number || '' }}",
            "pull_request_author: ${{ github.event.pull_request.user.login || '' }}",
            "pull_request_head_ref: ${{ github.event.pull_request.head.ref || github.head_ref || '' }}",
        ]:
            self.assertIn(expected, template_text)

    def test_semgrep_lane_uses_supported_cli_invocation(self) -> None:
        """Keep Semgrep on the supported CLI invocation shape."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('run(["semgrep", "ci"], cwd=repo_dir)', text)
        self.assertNotIn('run(["semgrep", "ci", "--error"], cwd=repo_dir)', text)

    def test_reusable_workflows_do_not_inline_inputs_inside_run_blocks(self) -> None:
        """Reject direct GitHub expression interpolation inside reusable workflow shell blocks."""
        workflow_expectations = {
            "reusable-codecov-analytics.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                '--event-name "${{ inputs.event_name }}"',
            ],
            "reusable-quality-zero-gate.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                '--event-name "${{ inputs.event_name }}"',
            ],
            "reusable-ruleset-sync.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                'f"repos/${{ inputs.repo_slug }}/rulesets"',
                'Path("${{ runner.temp }}/generated")',
            ],
            "reusable-scanner-matrix.yml": [
                '--repo-slug "${{ inputs.repo_slug }}"',
                '--event-name "${{ inputs.event_name }}"',
                '--repo "${{ inputs.repo_slug }}"',
                '--pull-request "${{ inputs.pull_request_number }}"',
            ],
        }

        for name, forbidden_snippets in workflow_expectations.items():
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            for snippet in forbidden_snippets:
                self.assertNotIn(snippet, text, f"{name}: {snippet}")

    def test_quality_zero_gate_exports_github_token_to_required_check_probe(
        self,
    ) -> None:
        """Provide the GitHub token to the required-check probe without inline Python blocks."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-quality-zero-gate.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", text)
        self.assertIn("run_quality_zero_gate.py", text)
        self.assertNotIn("python - <<'PY'", text)

    def test_all_flagged_checkout_steps_disable_persisted_credentials(self) -> None:
        """Require every flagged checkout step to disable persisted credentials."""
        workflow_paths = [
            ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml",
            ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml",
            ROOT / ".github" / "workflows" / "reusable-quality-zero-gate.yml",
            ROOT / ".github" / "workflows" / "reusable-ruleset-sync.yml",
            ROOT / ".github" / "workflows" / "verify.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(
                text.count("persist-credentials: false"),
                text.count("- uses: actions/checkout@v4"),
                path.name,
            )

    def test_mutation_workflows_use_env_indirection_for_codex_auth_file(self) -> None:
        """Route Codex auth file paths through environment variables instead of inline expressions."""
        workflow_paths = [
            ROOT / ".github" / "workflows" / "reusable-backlog-sweep.yml",
            ROOT / ".github" / "workflows" / "reusable-remediation-loop.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(
                '--auth-file "${{ needs.resolve-profile.outputs.codex_auth_file }}"',
                text,
                path.name,
            )
            self.assertIn(
                "CODEX_AUTH_FILE: ${{ needs.resolve-profile.outputs.codex_auth_file }}",
                text,
                path.name,
            )
            self.assertIn('--auth-file "$CODEX_AUTH_FILE"', text, path.name)

    def test_parity_wrappers_list_push_pull_request_and_manual_triggers(self) -> None:
        """Keep self wrappers and published templates aligned on their trigger surfaces."""
        workflow_paths = [
            ROOT / ".github" / "workflows" / "quality-zero-platform.yml",
            ROOT / ".github" / "workflows" / "quality-zero-gate.yml",
            ROOT / ".github" / "workflows" / "codecov-analytics.yml",
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "quality-zero-platform.yml",
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "quality-zero-gate.yml",
            ROOT
            / "templates"
            / "repo"
            / ".github"
            / "workflows"
            / "codecov-analytics.yml",
        ]

        for path in workflow_paths:
            text = path.read_text(encoding="utf-8")
            self.assertIn("push:", text, path.name)
            self.assertIn("pull_request:", text, path.name)
            self.assertIn("workflow_dispatch:", text, path.name)
            self.assertIn("branches: [main, master]", text, path.name)

    def test_scanner_matrix_builds_quality_rollup_and_posts_sticky_pr_comment(
        self,
    ) -> None:
        """Preserve the quality rollup artifact and sticky PR comment publishing steps."""
        text = (
            ROOT / ".github" / "workflows" / "reusable-scanner-matrix.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("quality-rollup", text)
        self.assertIn("build_quality_rollup.py", text)
        self.assertIn("post_pr_quality_comment.py", text)
        self.assertIn("actions/download-artifact@v4", text)
        self.assertIn("job_name: Dependency Alerts", text)
        self.assertIn('lane == "deps"', text)
        self.assertIn("check_dependabot_alerts.py", text)

    def test_admin_dashboard_and_control_plane_admin_workflows_exist_with_expected_triggers(
        self,
    ) -> None:
        """Assert the admin dashboard and control-plane admin workflows keep their expected controls."""
        dashboard_text = (
            ROOT / ".github" / "workflows" / "publish-admin-dashboard.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("schedule:", dashboard_text)
        self.assertIn("workflow_dispatch:", dashboard_text)
        self.assertIn("pages: write", dashboard_text)
        self.assertIn("id-token: write", dashboard_text)
        self.assertIn("build:", dashboard_text)
        self.assertIn("deploy:", dashboard_text)
        self.assertIn("permissions:", dashboard_text)
        self.assertIn("build_admin_dashboard.py", dashboard_text)
        self.assertIn("docs/admin", dashboard_text)
        self.assertIn("enablement: true", dashboard_text)

        admin_text = (
            ROOT / ".github" / "workflows" / "control-plane-admin.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", admin_text)
        self.assertIn("repo_slug:", admin_text)
        self.assertIn("operation:", admin_text)
        self.assertIn("control_plane_admin.py", admin_text)
        self.assertIn("create-pull-request", admin_text)
        self.assertIn("set-required-context", admin_text)
        self.assertIn("baseline_ref", admin_text)
        self.assertIn(
            'options: ["enforce", "evidence_only", "non_regression"]', admin_text
        )
        self.assertIn("ADMIN_OPERATION: ${{ inputs.operation }}", admin_text)
        self.assertIn("ADMIN_REPO_SLUG: ${{ inputs.repo_slug }}", admin_text)
        self.assertIn('--repo-slug "$ADMIN_REPO_SLUG"', admin_text)
        self.assertNotIn('--repo-slug "${{ inputs.repo_slug }}"', admin_text)
