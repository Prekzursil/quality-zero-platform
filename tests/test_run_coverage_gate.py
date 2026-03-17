from __future__ import absolute_import, division

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quality.common import DEFAULT_COVERAGE_JSON, DEFAULT_COVERAGE_MD

from scripts.quality import run_coverage_gate


class RunCoverageGateTests(unittest.TestCase):
    def test_coverage_mode_prefers_event_specific_override(self) -> None:
        self.assertEqual(
            run_coverage_gate._coverage_mode({'assert_mode': {'default': 'enforce', 'pull_request': 'evidence_only'}}, 'pull_request'),
            'evidence_only',
        )

    def test_run_shell_ignores_blank_commands(self) -> None:
        with patch('scripts.quality.run_coverage_gate.subprocess.run') as mock_run:
            run_coverage_gate._run_shell('   ', shell_name='bash', cwd=Path.cwd())

        mock_run.assert_not_called()

    def test_run_shell_passes_repo_command_via_stdin_to_static_shell_argv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            with patch('scripts.quality.run_coverage_gate.subprocess.run', return_value=object()) as mock_run:
                run_coverage_gate._run_shell('echo coverage', shell_name='pwsh', cwd=cwd)

        mock_run.assert_called_once_with(
            ['pwsh', '-NoLogo', '-Command', '-'],
            cwd=cwd,
            input='echo coverage',
            text=True,
            shell=False,
            check=True,
        )

    def test_run_assert_coverage_invokes_module_in_repo_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir = Path(temp_dir) / 'repo'
            repo_dir.mkdir()
            platform_dir = Path(temp_dir) / 'platform'
            platform_dir.mkdir()
            coverage_dir = repo_dir / 'coverage'
            coverage_dir.mkdir()
            (coverage_dir / 'platform-coverage.xml').write_text(
                '<coverage lines-valid="1" lines-covered="1"><packages><package><classes><class filename="src/app.py"><lines><line number="1" hits="1" /></lines></class></classes></package></packages></coverage>',
                encoding='utf-8',
            )

            coverage = {
                'inputs': [
                    {'format': 'xml', 'name': 'platform', 'path': str(coverage_dir / 'platform-coverage.xml')},
                ],
                'require_sources': ['src/app.py'],
                'min_percent': 100.0,
            }
            observed = {}

            def fake_main() -> int:
                observed['cwd'] = Path.cwd()
                observed['argv'] = list(sys.argv)
                return 17

            with patch('scripts.quality.run_coverage_gate.assert_coverage_100.main', side_effect=fake_main):
                result = run_coverage_gate._run_assert_coverage_100(
                    coverage,
                    repo_dir=repo_dir,
                    platform_dir=platform_dir,
                )

        self.assertEqual(result, 17)
        self.assertEqual(observed['cwd'], repo_dir)
        self.assertEqual(
            observed['argv'],
            [
                str(platform_dir / 'scripts' / 'quality' / 'assert_coverage_100.py'),
                '--xml',
                f"platform={coverage_dir / 'platform-coverage.xml'}",
                '--require-source',
                'src/app.py',
                '--min-percent',
                '100.0',
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
