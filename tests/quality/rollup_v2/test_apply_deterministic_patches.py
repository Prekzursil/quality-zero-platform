"""Tests for apply_deterministic_patches.py (per design §5.2)."""
from __future__ import absolute_import

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def _make_canonical(findings: list) -> dict:
    """Build a minimal canonical.json payload for testing."""
    return {
        "schema_version": "qzp-rollup/1",
        "total_findings": len(findings),
        "findings": findings,
        "provider_summaries": [],
        "normalizer_errors": [],
        "security_drops": [],
    }


def _make_finding(
    *,
    finding_id: str = "f1",
    file: str = "example.py",
    line: int = 1,
    patch_source: str = "deterministic",
    patch_diff: str | None = None,
    category: str = "unused-import",
) -> dict:
    """Build a minimal finding dict for testing."""
    return {
        "schema_version": "qzp-finding/1",
        "finding_id": finding_id,
        "file": file,
        "line": line,
        "end_line": line,
        "column": None,
        "category": category,
        "category_group": "quality",
        "severity": "medium",
        "corroboration": "single",
        "primary_message": "Test finding",
        "corroborators": [],
        "fix_hint": None,
        "patch": patch_diff,
        "patch_source": patch_source,
        "patch_confidence": "high" if patch_diff else None,
        "context_snippet": "",
        "source_file_hash": "",
        "cwe": None,
        "autofixable": patch_diff is not None,
        "tags": [],
        "patch_error": None,
    }


class FilterFindingsTests(unittest.TestCase):
    """Test that only deterministic findings with patches are selected."""

    def test_filters_deterministic_with_patch(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            filter_patchable_findings,
        )

        findings = [
            _make_finding(patch_source="deterministic", patch_diff="--- a\n+++ b\n"),
            _make_finding(finding_id="f2", patch_source="llm", patch_diff="--- a\n+++ b\n"),
            _make_finding(finding_id="f3", patch_source="deterministic", patch_diff=None),
            _make_finding(finding_id="f4", patch_source="none", patch_diff=None),
        ]
        result = filter_patchable_findings(findings)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["finding_id"], "f1")

    def test_empty_findings(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            filter_patchable_findings,
        )

        self.assertEqual(filter_patchable_findings([]), [])


class ApplyPatchTests(unittest.TestCase):
    """Test apply_single_patch logic with real git repos."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmpdir.name)
        # Init a git repo so git apply works
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_apply_clean_patch(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            apply_single_patch,
        )

        # Create a source file
        src = self.repo_dir / "example.py"
        src.write_text("import os\nimport sys\n\ndef main():\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

        diff = (
            "--- a/example.py\n"
            "+++ b/example.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-import os\n"
            " import sys\n"
        )
        result = apply_single_patch(diff, self.repo_dir)
        self.assertTrue(result["applied"])
        self.assertFalse(result["skipped"])
        # Verify the file was actually patched
        content = src.read_text(encoding="utf-8")
        self.assertNotIn("import os", content)
        self.assertIn("import sys", content)

    def test_skip_conflicting_patch(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            apply_single_patch,
        )

        src = self.repo_dir / "example.py"
        src.write_text("def hello():\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

        # Diff that doesn't match the file content
        diff = (
            "--- a/example.py\n"
            "+++ b/example.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-import os\n"
            " import sys\n"
        )
        result = apply_single_patch(diff, self.repo_dir)
        self.assertFalse(result["applied"])
        self.assertTrue(result["skipped"])
        self.assertIn("reason", result)


    def test_check_passes_but_apply_fails(self) -> None:
        """Cover the edge case where git apply --check succeeds but git apply fails."""
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            apply_single_patch,
        )

        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"

        # Mock subprocess.run so --check succeeds (rc=0) but actual apply
        # raises CalledProcessError (rc=1) — mirrors the new check=True
        # contract added to drop PYL-W1510.
        call_count = [0]
        original_run = subprocess.run

        def mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            if "git" in str(cmd) and "apply" in cmd:
                call_count[0] += 1
                if "--check" in cmd:
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                raise subprocess.CalledProcessError(
                    1, cmd, output="", stderr="apply failed race condition"
                )
            return original_run(cmd, **kwargs)

        with patch("subprocess.run", side_effect=mock_run):
            result = apply_single_patch(diff, self.repo_dir)

        self.assertFalse(result["applied"])
        self.assertTrue(result["skipped"])
        self.assertIn("apply failed race condition", result["reason"])


class RunPatcherTests(unittest.TestCase):
    """Test the full run_patcher orchestrator."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmpdir.name)
        subprocess.run(
            ["git", "init"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_run_patcher_applies_and_skips(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import run_patcher

        src = self.repo_dir / "example.py"
        src.write_text("import os\nimport sys\n\ndef main():\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_dir,
            capture_output=True,
            check=True,
        )

        good_diff = (
            "--- a/example.py\n"
            "+++ b/example.py\n"
            "@@ -1,2 +1,1 @@\n"
            "-import os\n"
            " import sys\n"
        )
        bad_diff = (
            "--- a/missing.py\n"
            "+++ b/missing.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        findings = [
            _make_finding(finding_id="good", patch_diff=good_diff),
            _make_finding(finding_id="bad", patch_diff=bad_diff),
            _make_finding(finding_id="llm", patch_source="llm", patch_diff="--- a\n+++ b\n"),
            _make_finding(finding_id="nopatch", patch_source="deterministic", patch_diff=None),
        ]
        canonical = _make_canonical(findings)

        result = run_patcher(canonical, self.repo_dir)
        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(len(result["applied"]), 1)
        self.assertEqual(result["applied"][0]["finding_id"], "good")
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["finding_id"], "bad")

    def test_run_patcher_empty_findings(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import run_patcher

        canonical = _make_canonical([])
        result = run_patcher(canonical, self.repo_dir)
        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["skipped"], [])


class CLITests(unittest.TestCase):
    """Test the CLI entry point (parse_args + main)."""

    def test_parse_args(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import parse_args

        with patch(
            "sys.argv",
            [
                "apply_deterministic_patches.py",
                "--canonical-json", "/tmp/canonical.json",
                "--repo-dir", "/tmp/repo",
                "--out-json", "/tmp/result.json",
            ],
        ):
            args = parse_args()
        self.assertEqual(args.canonical_json, "/tmp/canonical.json")
        self.assertEqual(args.repo_dir, "/tmp/repo")
        self.assertEqual(args.out_json, "/tmp/result.json")

    def test_main_writes_output(self) -> None:
        from scripts.quality.rollup_v2.apply_deterministic_patches import main

        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "t@t.com"],
                cwd=repo_dir, capture_output=True, check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "T"],
                cwd=repo_dir, capture_output=True, check=True,
            )

            canonical_path = Path(tmp) / "canonical.json"
            canonical_path.write_text(
                json.dumps(_make_canonical([])),
                encoding="utf-8",
            )

            # ``safe_output_path`` (Sonar S2083 defence in main()) resolves
            # the --out-json argument against ``Path.cwd()`` and rejects
            # anything that escapes that root. Chdir into tmp so the
            # relative path resolves inside the workspace; restore cwd on
            # the way out so the fixture cannot leak across tests.
            out_path = Path(tmp) / "result.json"
            original_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with patch(
                    "sys.argv",
                    [
                        "apply_deterministic_patches.py",
                        "--canonical-json", str(canonical_path),
                        "--repo-dir", str(repo_dir),
                        "--out-json", "result.json",
                    ],
                ):
                    exit_code = main()
            finally:
                os.chdir(original_cwd)
            self.assertEqual(exit_code, 0)
            self.assertTrue(out_path.exists())
            result = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(result["applied_count"], 0)
            self.assertEqual(result["skipped_count"], 0)


    def test_main_rejects_path_traversal_escape(self) -> None:
        """``safe_output_path`` MUST reject --out-json values that escape cwd.

        The Sonar S2083 fix relies on the fact that an attacker-supplied
        ``--out-json=../../etc/passwd`` cannot reach the workspace. This
        test exercises that rejection path so a future refactor that
        accidentally drops ``safe_output_path`` is caught here.
        """
        from scripts.quality.rollup_v2.apply_deterministic_patches import main

        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = os.getcwd()
            try:
                os.chdir(tmp)
                # Escape the workspace via leading ``..`` components.
                with patch(
                    "sys.argv",
                    [
                        "apply_deterministic_patches.py",
                        "--canonical-json", "canonical.json",
                        "--repo-dir", ".",
                        "--out-json", "../../escape.json",
                    ],
                ):
                    with self.assertRaises(ValueError) as ctx:
                        main()
                self.assertIn("escapes workspace root", str(ctx.exception))
            finally:
                os.chdir(original_cwd)


class ResolveGitPathTests(unittest.TestCase):
    """Cover both branches of ``_resolve_git_path`` (BAN-B607 mitigation)."""

    def test_returns_resolved_path_when_git_on_path(self) -> None:
        """Return the absolute path when shutil.which finds git."""
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            _resolve_git_path,
        )

        with patch(
            "scripts.quality.rollup_v2.apply_deterministic_patches.shutil.which",
            return_value="/usr/bin/git",
        ):
            self.assertEqual(_resolve_git_path(), "/usr/bin/git")

    def test_raises_runtime_error_when_git_missing(self) -> None:
        """Raise RuntimeError when shutil.which returns None."""
        from scripts.quality.rollup_v2.apply_deterministic_patches import (
            _resolve_git_path,
        )

        with patch(
            "scripts.quality.rollup_v2.apply_deterministic_patches.shutil.which",
            return_value=None,
        ), self.assertRaises(RuntimeError) as ctx:
            _resolve_git_path()
        self.assertIn("git binary not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
