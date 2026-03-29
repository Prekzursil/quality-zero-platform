"""Test run codex exec."""

from __future__ import absolute_import

import io
import runpy
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Tuple
from unittest.mock import patch

from scripts.quality.run_codex_exec import (
    _parse_args,
    _run_codex_exec,
    _validate_cli_token,
    build_codex_command,
    main,
)


class RunCodexExecTests(unittest.TestCase):
    """Run Codex Exec Tests."""

    @staticmethod
    def _build_parse_args_argv() -> List[str]:
        """Handle build parse args argv."""
        argv = ["run_codex_exec.py"]
        for flag, value in (
            ("--repo-dir", "repo"),
            ("--prompt-file", "prompt.md"),
            ("--output-last-message", "last.txt"),
            ("--json-log", "codex.jsonl"),
            ("--sandbox", "danger-full-access"),
            ("--config", "a=b"),
            ("--config", "c=d"),
            ("--profile", "trusted-profile"),
            ("--model", "gpt-5.4"),
        ):
            argv.extend([flag, value])
        return argv

    @staticmethod
    def _build_main_args(
        tmpdir_path: Path, *, prompt_text: str = "hello codex"
    ) -> Namespace:
        """Handle build main args."""
        repo_dir = tmpdir_path / "repo"
        repo_dir.mkdir()
        prompt_file = tmpdir_path / "prompt.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")
        return Namespace(
            repo_dir=str(repo_dir),
            prompt_file=str(prompt_file),
            output_last_message=str(tmpdir_path / "message.txt"),
            sandbox="workspace-write",
            profile="trusted-profile",
            model="gpt-5.4",
            config=["a=b"],
            json_log=str(tmpdir_path / "run.json"),
        )

    @staticmethod
    def _expected_codex_command(args: Namespace) -> List[str]:
        """Handle expected codex command."""
        with patch(
            "scripts.quality.run_codex_exec.shutil.which",
            return_value=r"C:\Tools\codex.exe",
        ):
            return build_codex_command(args)

    @staticmethod
    @staticmethod
    def _run_main_with_patched_subprocess(
        args: Namespace,
        completed: SimpleNamespace,
    ):
        """Handle run main with patched subprocess."""
        json_log = Path(args.json_log)
        with patch(
            "scripts.quality.run_codex_exec._parse_args", return_value=args
        ), patch(
            "scripts.quality.run_codex_exec.shutil.which",
            return_value=r"C:\Tools\codex.exe",
        ), patch(
            "scripts.quality.run_codex_exec.subprocess.run", return_value=completed
        ) as mock_run, patch(
            "sys.stdout", new=io.StringIO()
        ) as stdout, patch(
            "sys.stderr", new=io.StringIO()
        ) as stderr:
            exit_code = main()
        return {
            "exit_code": exit_code,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "json_log": json_log.read_text(encoding="utf-8"),
            "call_args": mock_run.call_args,
            "mock_run": mock_run,
        }

    def _assert_subprocess_call_matches_args(
        self,
        call_args: Tuple[Tuple[object, ...], Dict[str, object]],
        *,
        args: Namespace,
        prompt_text: str,
    ) -> None:
        """Handle assert subprocess call matches args."""
        called_args, called_kwargs = call_args
        self.assertEqual(called_args[0], self._expected_codex_command(args))
        self.assertEqual(called_kwargs["executable"], r"C:\Tools\codex.exe")
        self.assertFalse(called_kwargs["shell"])
        self.assertEqual(called_kwargs["input"], prompt_text)
        self.assertTrue(called_kwargs["text"])
        self.assertTrue(called_kwargs["capture_output"])
        self.assertFalse(called_kwargs["check"])

    def test_parse_args_accepts_all_supported_flags(self):
        """Cover parse args accepts all supported flags."""
        argv = self._build_parse_args_argv()

        with patch("sys.argv", argv):
            args = _parse_args()

        self.assertEqual(args.repo_dir, "repo")
        self.assertEqual(args.prompt_file, "prompt.md")
        self.assertEqual(args.output_last_message, "last.txt")
        self.assertEqual(args.json_log, "codex.jsonl")
        self.assertEqual(args.sandbox, "danger-full-access")
        self.assertEqual(args.config, ["a=b", "c=d"])
        self.assertEqual(args.profile, "trusted-profile")
        self.assertEqual(args.model, "gpt-5.4")

    def test_build_codex_command_uses_resolved_paths_and_optional_flags(self):
        """Cover build codex command uses resolved paths and optional flags."""
        args = Namespace(
            repo_dir=r".\repo",
            prompt_file=r".\prompt.txt",
            output_last_message=r".\out\message.txt",
            sandbox="workspace-write",
            profile="trusted-profile",
            model="gpt-5.4",
            config=["a=b", "c=d"],
        )

        with patch(
            "scripts.quality.run_codex_exec.shutil.which",
            return_value=r"C:\Tools\codex.exe",
        ):
            command = build_codex_command(args)

        self.assertEqual(command[:4], ["codex", "exec", "--full-auto", "-C"])
        self.assertEqual(command[4], str(Path(args.repo_dir).resolve()))
        self.assertEqual(command[5:8], ["-s", "workspace-write", "--json"])
        self.assertEqual(command[8], "-o")
        self.assertEqual(command[9], str(Path(args.output_last_message).resolve()))
        self.assertEqual(command[10], "-")
        self.assertEqual(
            command[11:],
            ["-p", "trusted-profile", "-m", "gpt-5.4", "-c", "a=b", "-c", "c=d"],
        )

    def test_build_codex_command_omits_optional_flags_when_not_set(self):
        """Cover build codex command omits optional flags when not set."""
        args = Namespace(
            repo_dir=r".\repo",
            prompt_file=r".\prompt.txt",
            output_last_message=r".\out\message.txt",
            sandbox="workspace-write",
            profile="",
            model="",
            config=[],
        )

        with patch(
            "scripts.quality.run_codex_exec.shutil.which",
            return_value=r"C:\Tools\codex.exe",
        ):
            command = build_codex_command(args)

        self.assertEqual(
            command,
            [
                "codex",
                "exec",
                "--full-auto",
                "-C",
                str(Path(args.repo_dir).resolve()),
                "-s",
                "workspace-write",
                "--json",
                "-o",
                str(Path(args.output_last_message).resolve()),
                "-",
            ],
        )

    def test_validate_cli_token_rejects_control_characters(self):
        """Cover validate cli token rejects control characters."""
        with self.assertRaisesRegex(
            ValueError, "--profile contains unsupported control characters"
        ):
            _validate_cli_token("trusted\nprofile", flag_name="--profile")

    def test_validate_cli_token_rejects_empty_values(self):
        """Cover validate cli token rejects empty values."""
        with self.assertRaisesRegex(ValueError, "--model cannot be empty"):
            _validate_cli_token("", flag_name="--model")

    def test_build_codex_command_rejects_unsupported_profile_characters(self):
        """Cover build codex command rejects unsupported profile characters."""
        args = Namespace(
            repo_dir=r".\repo",
            prompt_file=r".\prompt.txt",
            output_last_message=r".\out\message.txt",
            sandbox="workspace-write",
            profile="trusted profile",
            model="",
            config=[],
        )

        with patch(
            "scripts.quality.run_codex_exec.shutil.which",
            return_value=r"C:\Tools\codex.exe",
        ):
            with self.assertRaisesRegex(
                ValueError, "--profile contains unsupported characters"
            ):
                build_codex_command(args)

    def test_build_codex_command_requires_codex_executable_on_path(self):
        """Cover build codex command requires codex executable on path."""
        args = Namespace(
            repo_dir=r".\repo",
            prompt_file=r".\prompt.txt",
            output_last_message=r".\out\message.txt",
            sandbox="workspace-write",
            profile="",
            model="",
            config=[],
        )

        with patch("scripts.quality.run_codex_exec.shutil.which", return_value=None):
            with self.assertRaisesRegex(
                FileNotFoundError, "Unable to locate required executable: codex"
            ):
                build_codex_command(args)

    def test_run_codex_exec_invokes_subprocess_with_shell_false_and_stdin_prompt(self):
        """Cover run codex exec invokes subprocess with shell false and stdin prompt."""
        args = Namespace(
            repo_dir=r".\repo",
            output_last_message=r".\out\message.txt",
            sandbox="workspace-write",
            profile="trusted-profile",
            model="gpt-5.4",
            config=["a=b"],
        )

        completed = SimpleNamespace(stdout='{"ok":true}', stderr="warn", returncode=7)

        with patch(
            "scripts.quality.run_codex_exec.shutil.which",
            return_value=r"C:\Tools\codex.exe",
        ), patch(
            "scripts.quality.run_codex_exec.subprocess.run", return_value=completed
        ) as mock_run:
            result = _run_codex_exec(args, "hello codex")

        self.assertIs(result, completed)
        mock_run.assert_called_once()
        self._assert_subprocess_call_matches_args(
            mock_run.call_args,
            args=args,
            prompt_text="hello codex",
        )

    def test_main_invokes_subprocess_with_shell_false_and_writes_json_log(self):
        """Cover main invokes subprocess with shell false and writes json log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            args = self._build_main_args(tmpdir_path)
            completed = SimpleNamespace(
                stdout='{"ok":true}', stderr="warn", returncode=7
            )
            result = self._run_main_with_patched_subprocess(args, completed)

            self.assertEqual(result["exit_code"], 7)
            result["mock_run"].assert_called_once()
            self._assert_subprocess_call_matches_args(
                result["call_args"],
                args=args,
                prompt_text="hello codex",
            )
            self.assertEqual(result["json_log"], '{"ok":true}')
            self.assertEqual(result["stdout"], '{"ok":true}')
            self.assertEqual(result["stderr"], "warn")

    def test_script_entrypoint_executes_main_and_returns_exit_code(self):
        """Cover script entrypoint executes main and returns exit code."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            repo_dir = tmpdir_path / "repo"
            repo_dir.mkdir()
            prompt_file = tmpdir_path / "prompt.txt"
            prompt_file.write_text("hello from entrypoint", encoding="utf-8")
            output_last_message = tmpdir_path / "message.txt"
            script_path = (
                Path(__file__).resolve().parents[1]
                / "scripts"
                / "quality"
                / "run_codex_exec.py"
            )
            completed = SimpleNamespace(
                stdout='{"entry":true}', stderr="", returncode=0
            )
            argv = [
                str(script_path),
                "--repo-dir",
                str(repo_dir),
                "--prompt-file",
                str(prompt_file),
                "--output-last-message",
                str(output_last_message),
            ]

            with patch("shutil.which", return_value=r"C:\Tools\codex.exe"), patch(
                "subprocess.run", return_value=completed
            ) as mock_run, patch("sys.argv", argv), patch(
                "sys.stdout", new=io.StringIO()
            ) as stdout, self.assertRaises(
                SystemExit
            ) as result:
                runpy.run_path(str(script_path), run_name="__main__")

            self.assertEqual(result.exception.code, 0)
            mock_run.assert_called_once()
            self.assertEqual(stdout.getvalue(), '{"entry":true}')
