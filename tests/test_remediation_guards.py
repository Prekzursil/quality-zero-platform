"""Test remediation guards."""

from __future__ import absolute_import

import io
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from scripts.quality import remediation_guards as guards
from scripts.quality.remediation_guards import (
    GuardExecutionError,
    RemediationGuardError,
    assert_diff_is_safe_class,
    assert_no_force_push,
    assert_not_blocked_path,
    assert_security_class_pr_only,
    assert_tree_not_shrunk,
    main,
)

_GIT = guards._GIT_EXECUTABLE


def _fixture_env(config_global: str) -> Dict[str, str]:
    """Build a git env with ALL inherited GIT_* removed (incident lesson)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": config_global,
            "GIT_AUTHOR_NAME": "Guard Fixture",
            "GIT_AUTHOR_EMAIL": "guard-fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Guard Fixture",
            "GIT_COMMITTER_EMAIL": "guard-fixture@example.invalid",
        }
    )
    return env


class _GitFixtureMixin(unittest.TestCase):
    """Shared isolated-git-repo fixture helpers."""

    def setUp(self) -> None:
        """Create the temp root and an empty global config for isolation."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        config_global = self.tmp_path / "empty-gitconfig"
        config_global.write_text("", encoding="utf-8")
        self.env = _fixture_env(str(config_global))

    def _git(self, repo: Path, *args: str) -> str:
        """Run one git command against the fixture repo, fully isolated."""
        # Safe by construction: shell=False list argv, git resolved via
        # shutil.which at module import, fixture-controlled arguments only.
        result = subprocess.run(  # noqa: S603  # nosec B603
            [_GIT, "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
            env=self.env,
        )
        return result.stdout

    def _make_repo(self, files: Optional[Dict[str, str]] = None) -> Path:
        """Create an isolated repo with an initial commit of ``files``."""
        repo = Path(tempfile.mkdtemp(dir=str(self.tmp_path)))
        self._git(repo, "init", "-q")
        if files is None:
            self._git(repo, "commit", "--allow-empty", "-q", "-m", "empty base")
            return repo
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self._git(repo, "add", "--", *files.keys())
        self._git(repo, "commit", "-q", "-m", "base")
        return repo


class SyspathAndEnvTests(unittest.TestCase):
    """Cover the import-time syspath helper and git env scrubbing."""

    def test_ensure_platform_on_syspath_inserts_when_missing(self) -> None:
        """Insert the platform root when absent and no-op when present."""
        platform_root = str(Path(guards.__file__).resolve().parents[2])
        guards._ensure_platform_on_syspath()
        self.assertIn(platform_root, sys.path)
        original = list(sys.path)
        try:
            sys.path[:] = [entry for entry in sys.path if entry != platform_root]
            guards._ensure_platform_on_syspath()
            self.assertEqual(sys.path[0], platform_root)
        finally:
            sys.path[:] = original

    def test_scrubbed_git_env_drops_hook_injected_redirection(self) -> None:
        """Strip GIT_DIR-style redirection vars while keeping the rest."""
        injected = {"GIT_DIR": "/evil/.git", "GIT_WORK_TREE": "/evil", "KEEP_ME": "1"}
        with unittest.mock.patch.dict(os.environ, injected, clear=False):
            scrubbed = guards._scrubbed_git_env()
        for blocked in guards._GIT_ENV_BLOCKLIST:
            self.assertNotIn(blocked, scrubbed)
        self.assertEqual(scrubbed.get("KEEP_ME"), "1")

    def test_bot_identity_is_not_the_leaked_fixture_identity(self) -> None:
        """Lock in the real bot identity; never ``Test <test@test.com>``."""
        self.assertEqual(guards.REMEDIATION_BOT_NAME, "qzp-remediation-bot")
        self.assertEqual(
            guards.REMEDIATION_BOT_EMAIL, "qzp-remediation@users.noreply.github.com"
        )
        self.assertNotIn("test@test.com", guards.REMEDIATION_BOT_EMAIL)


class ForcePushGuardTests(unittest.TestCase):
    """Cover assert_no_force_push token screening."""

    def test_safe_push_args_pass(self) -> None:
        """Accept a plain branch push argv."""
        assert_no_force_push(
            ["push", "origin", "HEAD:refs/heads/codex/fix/x", "--no-verify-hint", "-n"]
        )

    def test_each_forbidden_flag_is_refused(self) -> None:
        """Reject every forbidden flag, the lease=ref form, and force refspecs."""
        forbidden = list(guards._FORBIDDEN_PUSH_TOKENS) + [
            "--force-with-lease=refs/heads/main",
            "+refs/heads/main",
            "+main",
        ]
        for token in forbidden:
            with self.subTest(token=token):
                with self.assertRaises(RemediationGuardError):
                    assert_no_force_push(["push", "origin", token])

    def test_delete_refspec_is_refused_but_normal_refspec_passes(self) -> None:
        """Reject ``:branch`` deletions while allowing ``src:dst`` refspecs."""
        with self.assertRaises(RemediationGuardError):
            assert_no_force_push([":refs/heads/victim"])
        assert_no_force_push(["main:refs/heads/codex/fix/x", "origin"])


class TreeShrinkGuardTests(_GitFixtureMixin):
    """Cover assert_tree_not_shrunk against real isolated repos."""

    def test_empty_base_tree_passes(self) -> None:
        """Return early when the base ref tracks no files."""
        repo = self._make_repo(files=None)
        assert_tree_not_shrunk(str(repo), "HEAD")

    def test_small_deletion_passes_and_mass_deletion_is_refused(self) -> None:
        """Allow <=50% deletions but refuse the scaffold-clobber signature."""
        files = {f"src/file_{index}.txt": f"content {index}\n" for index in range(4)}
        repo = self._make_repo(files)
        (repo / "src/file_0.txt").unlink()
        assert_tree_not_shrunk(str(repo), "HEAD")
        for index in range(1, 4):
            (repo / f"src/file_{index}.txt").unlink()
        with self.assertRaises(RemediationGuardError) as caught:
            assert_tree_not_shrunk(str(repo), "HEAD")
        self.assertIn("scaffold-clobber", str(caught.exception))

    def test_committed_head_ref_mode_is_supported(self) -> None:
        """Compare two committed refs when head_ref is provided."""
        files = {f"f{index}.txt": "x\n" for index in range(4)}
        repo = self._make_repo(files)
        base_sha = self._git(repo, "rev-parse", "HEAD").strip()
        for index in range(3):
            self._git(repo, "rm", "-q", "--", f"f{index}.txt")
        self._git(repo, "commit", "-q", "-m", "mass delete")
        with self.assertRaises(RemediationGuardError):
            assert_tree_not_shrunk(str(repo), base_sha, "HEAD")

    def test_git_failure_raises_execution_error(self) -> None:
        """Map git failures (unknown ref) to GuardExecutionError."""
        repo = self._make_repo({"a.txt": "a\n"})
        with self.assertRaises(GuardExecutionError):
            assert_tree_not_shrunk(str(repo), "no-such-ref")

    def test_git_failure_with_empty_stderr_gets_fallback_message(self) -> None:
        """Use the synthesized message when git fails without stderr."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr="   "
        )
        with unittest.mock.patch.object(guards.subprocess, "run", return_value=completed):
            with self.assertRaises(GuardExecutionError) as caught:
                guards._run_git(".", ["status"])
        self.assertIn("git status failed", str(caught.exception))


class SafeClassDiffTests(unittest.TestCase):
    """Cover assert_diff_is_safe_class parsing and class checkers."""

    @staticmethod
    def _diff(body: str, path: str = "a/x.py b/x.py") -> str:
        """Build a minimal one-file unified diff around ``body``."""
        return (
            f"diff --git {path}\n"
            "index 1111111..2222222 100644\n"
            f"--- a/{path.split()[0][2:]}\n"
            f"+++ b/{path.split()[1][2:]}\n"
            "@@ -1,2 +1,2 @@\n"
            f"{body}"
        )

    def test_empty_diff_and_hunkless_file_pass(self) -> None:
        """Accept an empty diff and a file entry with no hunks."""
        assert_diff_is_safe_class("", ("whitespace",))
        assert_diff_is_safe_class(
            "\ndiff --git a/x.py b/x.py\nindex 1111111..2222222 100644\n",
            ("whitespace",),
        )

    def test_whitespace_indentation_and_wrap_pass(self) -> None:
        """Accept indentation, blank-line, and line-wrap reflows."""
        assert_diff_is_safe_class(
            self._diff("-def f(a,b):\n+def f(a,\n+      b):\n \n"),
            ("whitespace",),
        )
        assert_diff_is_safe_class(
            self._diff("-x=1\n+x = 1\n"), ("line-wrap",)
        )

    def test_identifier_merge_is_refused(self) -> None:
        """Reject whitespace removal that merges identifiers (``a b`` -> ``ab``)."""
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff("-value = a b\n+value = ab\n"), ("whitespace",)
            )

    def test_string_literal_whitespace_change_is_refused(self) -> None:
        """Reject whitespace edits inside quoted string literals."""
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff('-msg = "a b"\n+msg = "a  b"\n'), ("whitespace",)
            )

    def test_import_reorder_passes_and_import_rewrite_is_refused(self) -> None:
        """Accept pure import reordering; reject changed or non-import lines."""
        reorder = "-import sys\n-import os\n+import os\n+import sys\n \n"
        assert_diff_is_safe_class(self._diff(reorder), ("import-order",))
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff("-import os\n+import shutil\n"), ("import-order",)
            )
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff("-import os\n+value = 1\n"), ("import-order",)
            )
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(self._diff("+import os\n"), ("import-order",))

    def test_quote_style_swap_passes_and_mismatch_is_refused(self) -> None:
        """Accept quote swaps line-for-line; reject anything else."""
        assert_diff_is_safe_class(
            self._diff("-name = 'qzp'\n+name = \"qzp\"\n"), ("quote-style",)
        )
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff("-name = 'qzp'\n+name = \"other\"\n"), ("quote-style",)
            )
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff("-name = 'qzp'\n+name = \"qzp\"\n+extra = 1\n"),
                ("quote-style",),
            )

    def test_second_allowed_class_can_validate_a_hunk(self) -> None:
        """Pass when a later checker in the allowed list validates the hunk."""
        assert_diff_is_safe_class(
            self._diff("-x=1\n+x = 1\n"), ("import-order", "whitespace")
        )

    def test_structural_diffs_are_refused(self) -> None:
        """Reject file adds/deletes, renames, mode changes, and binary patches."""
        headers = [
            "new file mode 100644",
            "deleted file mode 100644",
            "old mode 100644",
            "new mode 100755",
            "rename from x.py",
            "rename to y.py",
            "copy from x.py",
            "copy to y.py",
            "similarity index 90%",
            "dissimilarity index 10%",
            "Binary files a/x and b/x differ",
            "GIT binary patch",
        ]
        for header in headers:
            with self.subTest(header=header):
                text = f"diff --git a/x.py b/x.py\n{header}\n"
                with self.assertRaises(RemediationGuardError):
                    assert_diff_is_safe_class(text, ("whitespace",))
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                "diff --git a/x.py b/x.py\n--- /dev/null\n+++ b/x.py\n",
                ("whitespace",),
            )

    def test_unparseable_diffs_are_refused(self) -> None:
        """Reject content before headers, unknown headers, and bogus hunk lines."""
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class("garbage first line\n", ("whitespace",))
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                "diff --git a/x.py b/x.py\ntotally unknown header\n", ("whitespace",)
            )
        with self.assertRaises(RemediationGuardError):
            assert_diff_is_safe_class(
                self._diff("bogus hunk line\n"), ("whitespace",)
            )

    def test_header_blank_line_and_no_newline_marker_are_tolerated(self) -> None:
        """Skip blank header lines, ``\\ No newline`` markers, and empty context."""
        text = (
            "diff --git a/x.py b/x.py\n"
            "\n"
            "index 1111111..2222222 100644\n"
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1 +1 @@\n"
            "-x=1\n"
            "+x = 1\n"
            "\\ No newline at end of file\n"
            "\n"
        )
        assert_diff_is_safe_class(text, ("whitespace",))

    def test_unknown_or_empty_class_configuration_is_an_execution_error(self) -> None:
        """Fail loud on unknown class names or an empty allowed list."""
        with self.assertRaises(GuardExecutionError):
            assert_diff_is_safe_class("", ("not-a-class",))
        with self.assertRaises(GuardExecutionError):
            assert_diff_is_safe_class("", ())

    def test_import_order_with_blank_only_added_side_is_refused(self) -> None:
        """Reject hunks whose added side has no import lines at all."""
        self.assertFalse(
            guards._is_import_order_only_change(["import os"], ["   "])
        )


class BlockedPathGuardTests(unittest.TestCase):
    """Cover assert_not_blocked_path delegation and fallback."""

    def test_delegates_to_ssot_module_when_importable(self) -> None:
        """Use check_blocked_paths.is_blocked_path when the SSOT imports."""
        fake = SimpleNamespace(is_blocked_path=lambda path: path == "special.txt")
        with unittest.mock.patch.object(guards.importlib, "import_module", return_value=fake):
            with self.assertRaises(RemediationGuardError):
                assert_not_blocked_path(["special.txt"])
            assert_not_blocked_path(["other.txt"])

    def test_falls_back_to_local_contract_on_import_error(self) -> None:
        """Enforce the same BLOCKED_PATTERNS contract when the SSOT is absent."""
        with unittest.mock.patch.object(
            guards.importlib, "import_module", side_effect=ImportError("absent")
        ):
            with self.assertRaises(RemediationGuardError) as caught:
                assert_not_blocked_path(
                    ["src/ok.py", ".github/workflows/gate.yml"]
                )
            self.assertIn(".github/workflows/gate.yml", str(caught.exception))
            assert_not_blocked_path(["src/ok.py", "docs/readme.md"])

    def test_fallback_blocks_basenames_at_any_depth_and_windows_paths(self) -> None:
        """Match bare-name patterns depth-independently and normalize separators."""
        self.assertTrue(guards._fallback_is_blocked_path("deep/nested/pyproject.toml"))
        self.assertTrue(guards._fallback_is_blocked_path("scripts\\helper.ps1"))
        self.assertTrue(guards._fallback_is_blocked_path("./Dockerfile"))
        self.assertFalse(guards._fallback_is_blocked_path("src/app.py"))
        self.assertFalse(guards._fallback_is_blocked_path("   "))


class SecurityClassGuardDelegationTests(unittest.TestCase):
    """Cover assert_security_class_pr_only delegation."""

    def test_security_finding_with_auto_merge_intent_is_refused(self) -> None:
        """Convert SecurityAutoMergeRefusedError into RemediationGuardError."""
        findings = [{"scanner": "codeql", "id": "py/path-injection"}]
        with self.assertRaises(RemediationGuardError):
            assert_security_class_pr_only(findings, intends_auto_merge=True)

    def test_pr_only_intent_passes_with_security_findings(self) -> None:
        """Allow security findings when no auto-merge is intended."""
        findings = [{"scanner": "codeql", "id": "py/path-injection"}]
        assert_security_class_pr_only(findings, intends_auto_merge=False)
        assert_security_class_pr_only(
            [{"scanner": "pylint", "id": "C0301"}], intends_auto_merge=True
        )


class FindingsLoaderTests(unittest.TestCase):
    """Cover the findings JSON loader edge cases."""

    def setUp(self) -> None:
        """Create a temp directory for findings fixtures."""
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_valid_list_loads(self) -> None:
        """Load a JSON list as-is."""
        path = self.tmp_path / "findings.json"
        path.write_text('[{"scanner": "codeql"}]', encoding="utf-8")
        self.assertEqual(guards._load_findings(str(path)), [{"scanner": "codeql"}])

    def test_missing_file_invalid_json_and_non_list_raise(self) -> None:
        """Map read errors, parse errors, and non-list payloads to execution errors."""
        with self.assertRaises(GuardExecutionError):
            guards._load_findings(str(self.tmp_path / "absent.json"))
        bad = self.tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with self.assertRaises(GuardExecutionError):
            guards._load_findings(str(bad))
        not_list = self.tmp_path / "obj.json"
        not_list.write_text('{"scanner": "codeql"}', encoding="utf-8")
        with self.assertRaises(GuardExecutionError):
            guards._load_findings(str(not_list))


class QuotedSegmentTests(unittest.TestCase):
    """Pin the quoted-string-literal extraction contract.

    The ``_QUOTED_SEGMENT_RE`` pattern was rewritten into a non-backtracking
    (CodeQL py/redos-safe) form. These cases lock in that the extracted
    segments are byte-for-byte identical to the previous backreference form,
    which is the contract ``_is_whitespace_only_change`` depends on. Cases
    cover both quote types, escaped quotes, escaped backslashes, the opposite
    quote appearing inside a literal, adjacent literals, and unterminated
    literals.
    """

    def test_quoted_segments_extraction_contract_is_unchanged(self) -> None:
        """Extract the same literal segments the legacy regex produced."""
        expectations = [
            ("x = 'hello'", ["'hello'"]),
            ('y = "world"', ['"world"']),
            (r"a = 'he\'llo'", [r"'he\'llo'"]),
            (r'b = "he\"llo"', [r'"he\"llo"']),
            ("c = 'a' + 'b'", ["'a'", "'b'"]),
            # Opposite quote inside a literal must be kept inside the body —
            # this is exactly where the naive char-class form regressed.
            ('d = \'has "double" inside\'', ['\'has "double" inside\'']),
            ("e = \"has 'single' inside\"", ["\"has 'single' inside\""]),
            (r"f = 'trailing backslash\\'", [r"'trailing backslash\\'"]),
            ("h = ''", ["''"]),
            ('i = ""', ['""']),
            ("j = 'unterminated", []),
            (r"k = 'a\\' + 'b'", [r"'a\\'", "'b'"]),
            ("l = \"a'b'c\"", ["\"a'b'c\""]),
            ("multi\nline = 'value'\nnext = \"v2\"", ["'value'", '"v2"']),
        ]
        for source, expected in expectations:
            with self.subTest(source=source):
                self.assertEqual(guards._quoted_segments([source]), expected)

    def test_quoted_segments_runs_in_linear_time(self) -> None:
        """A long pathological body must not exhibit catastrophic backtracking."""
        import time

        evil = "'" + ("a" * 100000)  # unterminated, long body
        start = time.perf_counter()
        result = guards._quoted_segments([evil])
        elapsed = time.perf_counter() - start
        self.assertEqual(result, [])
        self.assertLess(elapsed, 1.0)


class GuardRunnerCliTests(_GitFixtureMixin):
    """Cover the CLI guard-runner end to end on isolated repos."""

    def _run_main(self, argv: List[str]) -> Tuple[int, str, str]:
        """Invoke main(argv) capturing stdout/stderr."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_whitespace_only_worktree_change_passes_all_guards(self) -> None:
        """Exit 0 with a SUCCESS terminal line for a formatter-only change."""
        repo = self._make_repo({"app.py": "x=1\n", "lib.py": "y = 2\n"})
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        code, out, err = self._run_main(
            [
                "--repo-dir",
                str(repo),
                "--base-ref",
                "HEAD",
                "--allowed-classes",
                "whitespace,line-wrap,",
            ]
        )
        self.assertEqual((code, err), (0, ""))
        self.assertIn("SUCCESS:remediation-guards 1 changed path(s)", out)

    def test_blocked_path_change_fails(self) -> None:
        """Exit 1 when the candidate touches workflow infrastructure."""
        repo = self._make_repo({".github/workflows/ci.yml": "name: ci\n"})
        (repo / ".github/workflows/ci.yml").write_text("name: ci2\n", encoding="utf-8")
        code, _, err = self._run_main(
            ["--repo-dir", str(repo), "--base-ref", "HEAD"]
        )
        self.assertEqual(code, 1)
        self.assertIn("FAILED:remediation-guards", err)
        self.assertIn("blocked paths", err)

    def test_scaffold_clobber_signature_fails(self) -> None:
        """Exit 1 when the candidate deletes most of the tracked tree."""
        files = {f"src/m{index}.py": "pass\n" for index in range(6)}
        repo = self._make_repo(files)
        for index in range(5):
            (repo / f"src/m{index}.py").unlink()
        code, _, err = self._run_main(
            [
                "--repo-dir",
                str(repo),
                "--base-ref",
                "HEAD",
                "--safe-classes-only",
                "false",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("tree-shrink refused", err)

    def test_untracked_file_fails_safe_class_mode(self) -> None:
        """Exit 1 when safe-classes-only finds untracked (created) files."""
        repo = self._make_repo({"app.py": "x = 1\n"})
        (repo / "example.py").write_text("print('scaffold')\n", encoding="utf-8")
        code, _, err = self._run_main(
            ["--repo-dir", str(repo), "--base-ref", "HEAD"]
        )
        self.assertEqual(code, 1)
        self.assertIn("untracked files present", err)

    def test_non_formatter_change_passes_when_safe_class_mode_off(self) -> None:
        """Exit 0 for behavioral edits only when safe-classes-only=false."""
        repo = self._make_repo({"app.py": "x = 1\n"})
        (repo / "app.py").write_text("y = 2\n", encoding="utf-8")
        argv = ["--repo-dir", str(repo), "--base-ref", "HEAD"]
        code_strict, _, err_strict = self._run_main(list(argv))
        self.assertEqual(code_strict, 1)
        self.assertIn("not provably", err_strict)
        code_off, _, _ = self._run_main(argv + ["--safe-classes-only", "false"])
        self.assertEqual(code_off, 0)

    def test_committed_head_ref_mode_passes(self) -> None:
        """Validate a committed candidate via --head-ref."""
        repo = self._make_repo({"app.py": "x=1\n"})
        base_sha = self._git(repo, "rev-parse", "HEAD").strip()
        (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        self._git(repo, "add", "--", "app.py")
        self._git(repo, "commit", "-q", "-m", "format")
        code, out, _ = self._run_main(
            [
                "--repo-dir",
                str(repo),
                "--base-ref",
                base_sha,
                "--head-ref",
                "HEAD",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("SUCCESS:remediation-guards", out)

    def test_security_findings_screening_via_cli(self) -> None:
        """Exit 1 only when auto-merge is intended on security findings."""
        repo = self._make_repo({"app.py": "x = 1\n"})
        findings = self.tmp_path / "findings.json"
        findings.write_text(
            json.dumps([{"scanner": "codeql", "id": "py/path-injection"}]),
            encoding="utf-8",
        )
        argv = [
            "--repo-dir",
            str(repo),
            "--base-ref",
            "HEAD",
            "--findings-json",
            str(findings),
        ]
        code_pr_only, _, _ = self._run_main(list(argv))
        self.assertEqual(code_pr_only, 0)
        code_auto, _, err = self._run_main(argv + ["--intends-auto-merge"])
        self.assertEqual(code_auto, 1)
        self.assertIn("refused to auto-merge", err)

    def test_force_push_args_are_screened_via_cli(self) -> None:
        """Exit 1 when an intended push argument carries force semantics."""
        repo = self._make_repo({"app.py": "x = 1\n"})
        code, _, err = self._run_main(
            [
                "--repo-dir",
                str(repo),
                "--base-ref",
                "HEAD",
                "--push-arg=origin",
                "--push-arg=--force",
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("forbidden push argument", err)

    def test_git_error_exits_2(self) -> None:
        """Exit 2 with an ERROR terminal line when git itself fails."""
        repo = self._make_repo({"app.py": "x = 1\n"})
        code, _, err = self._run_main(
            ["--repo-dir", str(repo), "--base-ref", "no-such-ref"]
        )
        self.assertEqual(code, 2)
        self.assertIn("ERROR:remediation-guards", err)

    def test_unknown_safe_class_exits_2(self) -> None:
        """Exit 2 when the allowed-classes configuration is invalid."""
        repo = self._make_repo({"app.py": "x = 1\n"})
        (repo / "app.py").write_text("x  =  1\n", encoding="utf-8")
        code, _, err = self._run_main(
            [
                "--repo-dir",
                str(repo),
                "--base-ref",
                "HEAD",
                "--allowed-classes",
                "not-a-class",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown safe-diff class", err)


if __name__ == "__main__":
    unittest.main()
