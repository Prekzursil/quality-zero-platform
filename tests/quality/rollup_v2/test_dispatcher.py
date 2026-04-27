"""Tests for patch generator dispatcher (per design §A.1.4)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    SCHEMA_VERSION,
    Finding,
)
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult


def _make_finding(
    file: str = "src/app.py",
    line: int = 10,
    category: str = "broad-except",
    category_group: str = CATEGORY_GROUP_QUALITY,
) -> Finding:
    corr = Corroborator.from_provider("Codacy", "W0703", None, "msg")
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id="qzp-0001",
        file=file,
        line=line,
        end_line=line,
        column=None,
        category=category,
        category_group=category_group,
        severity="medium",
        corroboration="single",
        primary_message="msg",
        corroborators=(corr,),
        fix_hint=None,
        patch=None,
        patch_source="none",
        patch_confidence=None,
        context_snippet="",
        source_file_hash="sha256:abc",
        cwe=None,
        autofixable=False,
        tags=(),
    )


class DispatcherTests(unittest.TestCase):
    """Tests for dispatch() routing."""

    def test_no_generator_returns_none(self):
        """Dispatching a finding with no registered generator returns None."""
        from scripts.quality.rollup_v2.patches import dispatch

        f = _make_finding(category="unknown-category-xyz")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src_dir = root / "src"
            src_dir.mkdir()
            (src_dir / "app.py").write_text("pass", encoding="utf-8")
            result = dispatch(f, source_file_content="pass", repo_root=root)
        self.assertIsNone(result)

    def test_path_traversal_returns_patch_declined(self):
        """Finding with path-escaping file returns PatchDeclined."""
        from scripts.quality.rollup_v2.patches import dispatch

        f = _make_finding(file="../../../etc/passwd")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            result = dispatch(f, source_file_content="", repo_root=root)
        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.reason_code, "path-traversal-rejected")
        self.assertEqual(result.suggested_tier, "skip")

    def test_registered_generator_is_called(self):
        """When a generator is registered for the category, it is called."""
        from scripts.quality.rollup_v2 import patches
        from scripts.quality.rollup_v2.patches import dispatch

        expected = PatchResult(
            unified_diff="--- a\n+++ b\n",
            confidence="high",
            category="broad-except",
            generator_version="1.0",
            touches_files=frozenset({Path("src/app.py")}),
        )
        fake_gen = SimpleNamespace(generate=lambda _finding, _source, _root:expected)

        with patch.dict(patches.GENERATORS, {"broad-except": fake_gen}):
            f = _make_finding(category="broad-except")
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                src_dir = root / "src"
                src_dir.mkdir()
                (src_dir / "app.py").write_text("pass", encoding="utf-8")
                result = dispatch(f, source_file_content="pass", repo_root=root)

        self.assertIsInstance(result, PatchResult)
        self.assertEqual(result.unified_diff, expected.unified_diff)

    def test_generator_returning_declined(self):
        """Generator returning PatchDeclined flows through dispatch."""
        from scripts.quality.rollup_v2 import patches
        from scripts.quality.rollup_v2.patches import dispatch

        declined = PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="Cannot determine fix",
            suggested_tier="llm-fallback",
        )
        fake_gen = SimpleNamespace(generate=lambda _finding, _source, _root:declined)

        with patch.dict(patches.GENERATORS, {"broad-except": fake_gen}):
            f = _make_finding(category="broad-except")
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                src_dir = root / "src"
                src_dir.mkdir()
                (src_dir / "app.py").write_text("pass", encoding="utf-8")
                result = dispatch(f, source_file_content="pass", repo_root=root)

        self.assertIsInstance(result, PatchDeclined)
        self.assertEqual(result.reason_code, "ambiguous-fix")

    def test_generators_dict_has_31_entries(self):
        """GENERATORS has 31 entries after Phase 9 (30 from §5.1 + 1 coverage-gap)."""
        from scripts.quality.rollup_v2.patches import GENERATORS

        self.assertEqual(len(GENERATORS), 31)


if __name__ == "__main__":
    unittest.main()
