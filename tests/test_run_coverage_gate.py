from __future__ import absolute_import, division

import io
import json
import runpy
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import List
from unittest.mock import patch

from scripts.quality.common import DEFAULT_COVERAGE_JSON, DEFAULT_COVERAGE_MD

from scripts.quality import run_coverage_gate


class RunCoverageGateTests(unittest.TestCase):
    def _assert_run_shell_invocation(
        self,
        *,
        shell_name: str,
        resolved_path: str,
        expected_argv: List[str],
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            with (
                patch(
                    'scripts.quality.run_coverage_gate._path_exists',
                    side_effect=lambda raw_path: raw_path == resolved_path,
                ),
                patch('scripts.quality.run_coverage_gate.subprocess.run', return_value=object()) as mock_run,
            ):
                run_coverage_gate._run_shell('echo coverage', shell_name=shell_name, cwd=cwd)

        mock_run.assert_called_once_with(
            expected_argv,
            cwd=cwd,
            input='echo coverage',
            text=True,
            shell=False,
            check=True,
        )

    def test_coverage_mode_prefers_event_specific_override(self) -> None:
        self.assertEqual(
            run_coverage_gate._coverage_mode(
                {'assert_mode': {'default': 'enforce', 'pull_request': 'evidence_only'}},
                'pull_request',
            ),
            'evidence_only',
        )

    def test_run_shell_ignores_blank_commands(self) -> None:
        with patch('scripts.quality.run_coverage_gate.subprocess.run') as mock_run:
            run_coverage_gate._run_shell('   ', shell_name='bash', cwd=Path.cwd())

        mock_run.assert_not_called()

    def test_run_shell_passes_repo_command_via_stdin_to_static_shell_argv(self) -> None:
        self._assert_run_shell_invocation(
            shell_name='pwsh',
            resolved_path='/usr/bin/pwsh',
            expected_argv=['/usr/bin/pwsh', '-NoLogo', '-Command', '-'],
        )

    def test_run_shell_prefers_windows_powershell_path_when_available(self) -> None:
        self._assert_run_shell_invocation(
            shell_name='pwsh',
            resolved_path=r'C:\Program Files\PowerShell\7\pwsh.exe',
            expected_argv=[r'C:\Program Files\PowerShell\7\pwsh.exe', '-NoLogo', '-Command', '-'],
        )

    def test_run_shell_supports_bash_with_static_shell_argv(self) -> None:
        self._assert_run_shell_invocation(
            shell_name='bash',
            resolved_path='/usr/bin/bash',
            expected_argv=['/usr/bin/bash', '-s'],
        )

    def test_run_shell_supports_bin_bash_fallback(self) -> None:
        self._assert_run_shell_invocation(
            shell_name='bash',
            resolved_path='/bin/bash',
            expected_argv=['/bin/bash', '-s'],
        )

    def test_run_shell_requires_resolved_shell_executable(self) -> None:
        with patch('scripts.quality.run_coverage_gate._path_exists', return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, 'Unable to locate required shell executable: bash'):
                run_coverage_gate._run_shell('echo coverage', shell_name='bash', cwd=Path.cwd())

    def test_run_shell_requires_resolved_powershell_executable(self) -> None:
        with patch('scripts.quality.run_coverage_gate._path_exists', return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, 'Unable to locate required shell executable: pwsh'):
                run_coverage_gate._run_shell('echo coverage', shell_name='pwsh', cwd=Path.cwd())

    def test_path_exists_reports_real_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / 'exists.txt'
            target.write_text('ok', encoding='utf-8')

            self.assertTrue(run_coverage_gate._path_exists(str(target)))
            self.assertFalse(run_coverage_gate._path_exists(str(target.with_name('missing.txt'))))

    def test_run_assert_coverage_invokes_module_in_repo_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / 'repo'
            repo_dir.mkdir()
            platform_dir = Path(temp_dir) / 'platform'
            platform_dir.mkdir()
            coverage_dir = repo_dir / 'coverage'
            coverage_dir.mkdir()
            (coverage_dir / 'platform-coverage.xml').write_text(
                (
                    '<coverage lines-valid="1" lines-covered="1"><packages><package><classes>'
                    '<class filename="src/app.py"><lines><line number="1" hits="1" />'
                    '</lines></class></classes></package></packages></coverage>'
                ),
                encoding='utf-8',
            )

            coverage = {
                'inputs': [
                    {'format': 'xml', 'name': 'platform', 'path': str(coverage_dir / 'platform-coverage.xml')},
                ],
                'require_sources': ['src/app.py'],
                'min_percent': 100.0,
                'branch_min_percent': 85.0,
            }
            observed_cwd = None
            observed_argv = None

            def fake_main() -> int:
                nonlocal observed_cwd, observed_argv
                observed_cwd = Path.cwd()
                observed_argv = list(sys.argv)
                return 17

            with patch('scripts.quality.run_coverage_gate.assert_coverage_100.main', side_effect=fake_main):
                result = run_coverage_gate._run_assert_coverage_100(
                    coverage,
                    repo_dir=repo_dir,
                    platform_dir=platform_dir,
                )

        self.assertEqual(result, 17)
        self.assertEqual(observed_cwd, repo_dir)
        self.assertEqual(
            observed_argv,
            [
                str(platform_dir / 'scripts' / 'quality' / 'assert_coverage_100.py'),
                '--xml',
                f"platform={coverage_dir / 'platform-coverage.xml'}",
                '--require-source',
                'src/app.py',
                '--min-percent',
                '100.0',
                '--branch-min-percent',
                '85.0',
                '--out-json',
                DEFAULT_COVERAGE_JSON,
                '--out-md',
                DEFAULT_COVERAGE_MD,
            ],
        )

    def test_main_runs_profile_command_and_direct_assertion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            repo_dir = temp_path / 'repo'
            repo_dir.mkdir()
            profile_json = temp_path / 'profile.json'
            profile_json.write_text(
                json.dumps(
                    {
                        'coverage': {
                            'command': 'echo coverage',
                            'shell': 'bash',
                            'inputs': [],
                            'min_percent': 100.0,
                        }
                    }
                ),
                encoding='utf-8',
            )

            with (
                patch('scripts.quality.run_coverage_gate._run_shell') as mock_shell,
                patch('scripts.quality.run_coverage_gate._run_assert_coverage_100', return_value=23) as mock_assert,
                patch.object(sys, 'argv', [
                    'run_coverage_gate.py',
                    '--profile-json',
                    str(profile_json),
                    '--event-name',
                    'push',
                    '--repo-dir',
                    str(repo_dir),
                ]),
            ):
                result = run_coverage_gate.main()

        self.assertEqual(result, 23)
        mock_shell.assert_called_once_with('echo coverage', shell_name='bash', cwd=repo_dir.resolve())
        mock_assert.assert_called_once()

    def test_evidence_only_mode_skips_assertion_and_returns_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_json = temp_path / 'profile.json'
            profile_json.write_text(
                json.dumps(
                    {
                        'coverage': {
                            'command': '',
                            'shell': 'bash',
                            'assert_mode': {'default': 'evidence_only'},
                            'evidence_note': 'hard gate is enforced elsewhere',
                        }
                    }
                ),
                encoding='utf-8',
            )

            with (
                patch('scripts.quality.run_coverage_gate._run_shell') as mock_shell,
                patch('scripts.quality.run_coverage_gate._run_assert_coverage_100') as mock_assert,
                patch.object(sys, 'argv', [
                    'run_coverage_gate.py',
                    '--profile-json',
                    str(profile_json),
                    '--event-name',
                    'push',
                ]),
            ):
                result = run_coverage_gate.main()

        self.assertEqual(result, 0)
        mock_shell.assert_called_once_with('', shell_name='bash', cwd=Path.cwd().resolve())
        mock_assert.assert_not_called()

    def test_non_regression_mode_uses_baseline_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            profile_json = temp_path / 'profile.json'
            profile_json.write_text(
                json.dumps(
                    {
                        'slug': 'Prekzursil/SWFOC-Mod-Menu',
                        'default_branch': 'main',
                        'coverage': {
                            'command': '',
                            'shell': 'bash',
                            'assert_mode': {'default': 'non_regression'},
                            'inputs': [],
                        },
                    }
                ),
                encoding='utf-8',
            )

            with (
                patch('scripts.quality.run_coverage_gate._run_shell') as mock_shell,
                patch('scripts.quality.run_coverage_gate._collect_current_coverage_payload', return_value={'combined_percent': 95.0}),
                patch('scripts.quality.run_coverage_gate._load_baseline_coverage_payload', return_value={'combined_percent': 94.0}),
                patch('scripts.quality.run_coverage_gate._write_non_regression_report', return_value=0) as mock_report,
                patch.object(sys, 'argv', [
                    'run_coverage_gate.py',
                    '--profile-json',
                    str(profile_json),
                    '--event-name',
                    'pull_request',
                ]),
            ):
                result = run_coverage_gate.main()

        self.assertEqual(result, 0)
        mock_shell.assert_called_once()
        mock_report.assert_called_once()

    def test_download_and_lookup_helpers_cover_success_paths(self) -> None:
        with patch.object(run_coverage_gate, "load_bytes_https", return_value=(b"bytes", {})) as load_bytes_mock:
            self.assertEqual(run_coverage_gate._download_bytes("https://api.github.com/example", "token"), b"bytes")
        self.assertEqual(load_bytes_mock.call_args.kwargs["allowed_hosts"], {"api.github.com"})

        with patch.dict("os.environ", {"GITHUB_TOKEN": "token"}, clear=True):
            self.assertEqual(run_coverage_gate._github_api_token(), "token")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "GITHUB_TOKEN or GH_TOKEN is required"):
                run_coverage_gate._github_api_token()

        runs = [{"id": 1, "name": "Other", "conclusion": "success"}, {"id": 2, "name": "Quality Zero Platform", "conclusion": "success"}]
        self.assertEqual(run_coverage_gate._find_successful_run_id(runs, "Quality Zero Platform"), 2)
        self.assertIsNone(run_coverage_gate._find_successful_run_id(runs, "Missing"))
        artifacts = [{"name": "coverage-artifacts", "archive_download_url": "https://api.github.com/archive.zip"}]
        self.assertEqual(run_coverage_gate._find_artifact_by_name(artifacts, "coverage-artifacts"), artifacts[0])
        self.assertIsNone(run_coverage_gate._find_artifact_by_name(artifacts, "missing"))

    def test_baseline_loading_and_non_regression_reporting_cover_success_and_failure(self) -> None:
        baseline_zip = io.BytesIO()
        with zipfile.ZipFile(baseline_zip, "w") as handle:
            handle.writestr("coverage-100/coverage.json", json.dumps({"components": [{"covered": 5, "total": 10}]}))

        downloads = [
            json.dumps({"workflow_runs": [{"id": 2, "name": "Quality Zero Platform", "conclusion": "success"}]}).encode("utf-8"),
            json.dumps({"artifacts": [{"name": "coverage-artifacts", "archive_download_url": "https://api.github.com/archive.zip"}]}).encode("utf-8"),
            baseline_zip.getvalue(),
        ]
        with patch.object(run_coverage_gate, "_github_api_token", return_value="token"), patch.object(run_coverage_gate, "_download_bytes", side_effect=downloads):
            payload = run_coverage_gate._load_baseline_coverage_payload({"slug": "owner/repo", "default_branch": "main"})
        self.assertEqual(payload["components"][0]["covered"], 5)

        markdown = run_coverage_gate._render_non_regression_md(
            {"status": "fail", "current_percent": 90.0, "baseline_percent": 95.0, "timestamp_utc": "now", "findings": ["regressed"]}
        )
        self.assertIn("non_regression", markdown)
        self.assertIn("regressed", markdown)

        with patch.object(run_coverage_gate, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 9, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                0,
            )
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "pass")

        with patch.object(run_coverage_gate, "write_report", return_value=0) as write_report_mock:
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 7, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                1,
            )
        self.assertEqual(write_report_mock.call_args.args[0]["status"], "fail")

        with patch.object(run_coverage_gate, "write_report", return_value=6):
            self.assertEqual(
                run_coverage_gate._write_non_regression_report(
                    {"components": [{"covered": 9, "total": 10}]},
                    {"components": [{"covered": 8, "total": 10}]},
                ),
                6,
            )

    def test_remaining_helper_and_entrypoint_paths(self) -> None:
        self.assertEqual(run_coverage_gate._combined_coverage_percent({"components": "bad"}), 100.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir)
            (repo_dir / "coverage-100").mkdir()
            (repo_dir / "coverage-100" / "coverage.json").write_text("{}", encoding="utf-8")
            with patch.object(run_coverage_gate, "_run_assert_coverage_100", return_value=0):
                self.assertEqual(
                    run_coverage_gate._collect_current_coverage_payload({}, repo_dir=repo_dir, platform_dir=repo_dir),
                    {},
                )
            with patch.object(run_coverage_gate, "_run_assert_coverage_100", return_value=2):
                with self.assertRaisesRegex(RuntimeError, "coverage assertion returned unexpected exit code 2"):
                    run_coverage_gate._collect_current_coverage_payload({}, repo_dir=repo_dir, platform_dir=repo_dir)

        with patch.object(run_coverage_gate, "_github_api_token", return_value="token"), patch.object(
            run_coverage_gate, "_download_bytes", return_value=json.dumps({"workflow_runs": []}).encode("utf-8")
        ):
            with self.assertRaisesRegex(RuntimeError, "Unable to find a successful Quality Zero Platform run"):
                run_coverage_gate._load_baseline_coverage_payload({"slug": "owner/repo", "default_branch": "main"})

        with patch.object(run_coverage_gate, "_github_api_token", return_value="token"), patch.object(
            run_coverage_gate,
            "_download_bytes",
            side_effect=[
                json.dumps({"workflow_runs": [{"id": 1, "name": "Quality Zero Platform", "conclusion": "success"}]}).encode("utf-8"),
                json.dumps({"artifacts": []}).encode("utf-8"),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "Unable to find coverage-artifacts"):
                run_coverage_gate._load_baseline_coverage_payload({"slug": "owner/repo", "default_branch": "main"})

        script_path = Path(run_coverage_gate.__file__).resolve()
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_json = Path(temp_dir) / "profile.json"
            profile_json.write_text(json.dumps({"coverage": {"command": "", "shell": "bash", "assert_mode": {"default": "evidence_only"}}}), encoding="utf-8")
            with patch.object(sys, "argv", [str(script_path), "--profile-json", str(profile_json), "--event-name", "push"]):
                with self.assertRaises(SystemExit) as result:
                    runpy.run_path(str(script_path), run_name="__main__")
            self.assertEqual(result.exception.code, 0)
