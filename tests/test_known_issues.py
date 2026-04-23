"""Schema + loader tests for the Phase 4 known-issues registry."""

from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from scripts.quality import known_issues as ki


_REPO_ROOT = Path(__file__).resolve().parents[1]
_REGISTRY_ROOT = _REPO_ROOT / "known-issues"


class ShippedRegistryTests(unittest.TestCase):
    """The four entries shipped with Phase 4 parse and validate."""

    def test_all_four_required_entries_present(self) -> None:
        """QZ-FP-001..003 + QZ-CV-001 all land under ``known-issues/``."""
        entries = ki.load_known_issues(_REGISTRY_ROOT)
        ids = {e.get("id") for e in entries}
        self.assertIn("QZ-FP-001", ids)
        self.assertIn("QZ-FP-002", ids)
        self.assertIn("QZ-FP-003", ids)
        self.assertIn("QZ-CV-001", ids)

    def test_entries_returned_in_sorted_order(self) -> None:
        """The loader sorts entries by id so QRv2 gets a deterministic prompt."""
        entries = ki.load_known_issues(_REGISTRY_ROOT)
        ids = [e.get("id") for e in entries]
        self.assertEqual(ids, sorted(ids))

    def test_every_entry_feeds_qrv2_with_a_fix_snippet(self) -> None:
        """Every shipped entry has ``feeds_qrv2: true`` + non-empty fix_snippet."""
        entries = ki.load_known_issues(_REGISTRY_ROOT)
        for entry in entries:
            with self.subTest(id=entry["id"]):
                self.assertIs(entry["feeds_qrv2"], True)
                self.assertTrue(entry.get("fix_snippet", "").strip())

    def test_qrv2_prompt_entries_filters_to_fed_entries(self) -> None:
        """``qrv2_prompt_entries`` returns only entries flagged to feed QRv2."""
        entries = ki.load_known_issues(_REGISTRY_ROOT)
        prompt = ki.qrv2_prompt_entries(entries)
        self.assertEqual(len(prompt), len(entries))  # all 4 currently feed


class LoaderValidationTests(unittest.TestCase):
    """``load_known_issues`` rejects malformed entries."""

    @staticmethod
    def _write_registry(files: dict) -> Path:
        """Create a temp registry with ``files``: ``{name: yaml_text}``."""
        root = Path(tempfile.mkdtemp())
        for name, body in files.items():
            (root / name).write_text(body, encoding="utf-8")
        return root

    def test_missing_registry_returns_empty_list(self) -> None:
        """A non-existent registry dir yields an empty list (not an error)."""
        self.assertEqual(
            ki.load_known_issues(Path("/does/not/exist/known-issues")), []
        )

    def test_missing_required_field_raises(self) -> None:
        """An entry lacking a required field fails validation."""
        root = self._write_registry({
            "QZ-BAD-001.yml": "id: QZ-BAD-001\ntitle: missing the rest\n",
        })
        with self.assertRaises(ki.KnownIssueError):
            ki.load_known_issues(root)

    def test_feeds_qrv2_without_fix_snippet_raises(self) -> None:
        """``feeds_qrv2: true`` must accompany a non-empty ``fix_snippet``."""
        root = self._write_registry({
            "QZ-BAD-002.yml": (
                "id: QZ-BAD-002\n"
                "title: t\ndescription: d\naffects: [x]\n"
                "feeds_qrv2: true\nfix_snippet: ''\nverified_at: '2026-04-23'\n"
            ),
        })
        with self.assertRaises(ki.KnownIssueError):
            ki.load_known_issues(root)

    def test_non_mapping_yaml_raises(self) -> None:
        """A YAML file whose top level isn't a mapping is rejected."""
        root = self._write_registry({
            "QZ-BAD-003.yml": "- just\n- a\n- list\n",
        })
        with self.assertRaises(ki.KnownIssueError):
            ki.load_known_issues(root)

    def test_non_yaml_files_skipped(self) -> None:
        """README.md and similar files don't break the loader."""
        root = self._write_registry({
            "README.md": "# not an entry\n",
            "QZ-OK-001.yml": (
                "id: QZ-OK-001\ntitle: ok\ndescription: ok\n"
                "affects: [x]\nfeeds_qrv2: false\nverified_at: '2026-04-23'\n"
            ),
        })
        entries = ki.load_known_issues(root)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "QZ-OK-001")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
