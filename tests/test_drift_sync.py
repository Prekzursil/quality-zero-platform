"""Coverage for ``scripts.quality.drift_sync`` — the Phase 3 comparator.

Tests exercise every DriftEntry status (missing, drift, in_sync), the
CLI exit contract (0 / 1 / 2), and the report JSON shape.
"""

from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.quality import drift_sync as ds


def _make_temp_repo(files: dict) -> Path:
    """Create a temp repo with ``files``: ``{relative_path: content}``."""
    root = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


class DetectDriftTests(unittest.TestCase):
    """``detect_drift`` reports missing / drift / in_sync per template."""

    def test_empty_stack_returns_no_entries(self) -> None:
        """Profiles without a ``stack`` value produce no entries."""
        self.assertEqual(ds.detect_drift({}, Path(".")), [])

    def test_missing_output_flags_missing(self) -> None:
        """When the consumer repo has no file, the template is ``missing``."""
        repo = _make_temp_repo({})
        profile = {
            "stack": "python-only",
            "default_branch": "main",
            "coverage": {
                "command": "pytest",
                "inputs": [
                    {"name": "x", "flag": "x", "path": "x.xml"},
                ],
            },
        }
        entries = ds.detect_drift(profile, repo)
        self.assertTrue(entries, "template list must not be empty")
        for entry in entries:
            self.assertEqual(entry.status, "missing")
        # At least one of the rendered templates must be non-empty so we
        # also exercise the diff-contains-new-content path.
        non_empty = [e for e in entries if e.proposed_content]
        self.assertTrue(non_empty)
        self.assertIn("+++ b/", non_empty[0].diff)

    def test_exact_match_flags_in_sync(self) -> None:
        """When the consumer file equals the rendered body, status is ``in_sync``."""
        profile = {
            "stack": "python-only",
            "default_branch": "main",
            "coverage": {"command": "pytest --cov"},
        }
        # Render the template the same way drift_sync will, then write it
        # into the fake consumer repo at the expected output path.
        from scripts.quality import template_render as tr

        mapping = tr.list_templates("python-only")
        files = {}
        for template_path, output_path in mapping.items():
            files[output_path] = tr.render_template(template_path, profile)
        repo = _make_temp_repo(files)
        entries = ds.detect_drift(profile, repo)
        for entry in entries:
            self.assertEqual(entry.status, "in_sync")
            self.assertEqual(entry.diff, "")

    def test_changed_content_flags_drift(self) -> None:
        """When the consumer file diverges from the rendered body, status is ``drift``."""
        profile = {
            "stack": "python-only",
            "default_branch": "main",
            "coverage": {"command": "pytest --cov"},
        }
        # Seed the repo with a modified version of each output.
        from scripts.quality import template_render as tr

        mapping = tr.list_templates("python-only")
        files = {}
        for template_path, output_path in mapping.items():
            rendered = tr.render_template(template_path, profile)
            files[output_path] = "# DRIFTED\n" + rendered
        repo = _make_temp_repo(files)
        entries = ds.detect_drift(profile, repo)
        self.assertTrue(entries)
        self.assertTrue(all(e.status == "drift" for e in entries))
        self.assertTrue(all("# DRIFTED" in e.diff for e in entries))


class DriftSummaryTests(unittest.TestCase):
    """``drift_summary`` counts entries by status."""

    def test_counts_each_status(self) -> None:
        """Every status appears in the output even when zero."""
        entries = [
            ds.DriftEntry(
                template_path="a", output_path="a", status="missing",
                diff="", proposed_content="",
            ),
            ds.DriftEntry(
                template_path="b", output_path="b", status="drift",
                diff="", proposed_content="",
            ),
            ds.DriftEntry(
                template_path="c", output_path="c", status="drift",
                diff="", proposed_content="",
            ),
            ds.DriftEntry(
                template_path="d", output_path="d", status="in_sync",
                diff="", proposed_content="",
            ),
        ]
        self.assertEqual(
            ds.drift_summary(entries),
            {"missing": 1, "drift": 2, "in_sync": 1},
        )


class CliTests(unittest.TestCase):
    """End-to-end CLI: exit codes 0 / 1 / 2."""

    @staticmethod
    def _run(argv: list) -> int:
        """Invoke ``main()`` with patched ``sys.argv``."""
        with patch.object(sys, "argv", ["drift_sync.py", *argv]):
            return ds.main()

    def test_missing_profile_json_returns_2(self) -> None:
        """Exit 2 when profile JSON path doesn't exist."""
        rc = self._run([
            "--profile-json", "/does/not/exist.json",
            "--repo-root", str(Path.cwd()),
        ])
        self.assertEqual(rc, 2)

    def test_missing_repo_root_returns_2(self) -> None:
        """Exit 2 when repo root path doesn't exist or isn't a directory."""
        profile_path = Path(tempfile.NamedTemporaryFile(
            delete=False, suffix=".json"
        ).name)
        self.addCleanup(profile_path.unlink, missing_ok=True)
        profile_path.write_text(
            json.dumps({"stack": "python-only", "coverage": {}}),
            encoding="utf-8",
        )
        rc = self._run([
            "--profile-json", str(profile_path),
            "--repo-root", "/nope/not/here",
        ])
        self.assertEqual(rc, 2)

    def test_fail_on_drift_flag_returns_1_when_out_of_sync(self) -> None:
        """Exit 1 with ``--fail-on-drift`` and at least one missing/drift entry."""
        profile = {"stack": "python-only", "coverage": {"command": "pytest"}}
        profile_path = Path(tempfile.NamedTemporaryFile(
            delete=False, suffix=".json"
        ).name)
        self.addCleanup(profile_path.unlink, missing_ok=True)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        repo = _make_temp_repo({})  # empty repo = everything missing
        rc = self._run([
            "--profile-json", str(profile_path),
            "--repo-root", str(repo),
            "--fail-on-drift",
        ])
        self.assertEqual(rc, 1)

    def test_no_fail_on_drift_returns_0_even_with_drift(self) -> None:
        """Without ``--fail-on-drift`` the CLI always returns 0."""
        profile = {"stack": "python-only", "coverage": {"command": "pytest"}}
        profile_path = Path(tempfile.NamedTemporaryFile(
            delete=False, suffix=".json"
        ).name)
        self.addCleanup(profile_path.unlink, missing_ok=True)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        repo = _make_temp_repo({})
        rc = self._run([
            "--profile-json", str(profile_path),
            "--repo-root", str(repo),
        ])
        self.assertEqual(rc, 0)

    def test_out_json_writes_report_file(self) -> None:
        """``--out-json`` writes the report instead of printing to stdout."""
        profile = {"stack": "python-only", "coverage": {"command": "pytest"}}
        profile_path = Path(tempfile.NamedTemporaryFile(
            delete=False, suffix=".json"
        ).name)
        self.addCleanup(profile_path.unlink, missing_ok=True)
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        repo = _make_temp_repo({})
        out_path = Path(tempfile.NamedTemporaryFile(
            delete=False, suffix=".json"
        ).name)
        self.addCleanup(out_path.unlink, missing_ok=True)
        rc = self._run([
            "--profile-json", str(profile_path),
            "--repo-root", str(repo),
            "--out-json", str(out_path),
        ])
        self.assertEqual(rc, 0)
        report = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertIn("summary", report)
        self.assertIn("entries", report)
        self.assertGreaterEqual(report["summary"]["missing"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
