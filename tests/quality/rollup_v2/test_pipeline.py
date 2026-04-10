"""Tests for pipeline orchestrator (per design §4.2 + §A.3.5)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.types.corroborator import Corroborator
from scripts.quality.rollup_v2.types.finding import SCHEMA_VERSION, Finding


def _make_finding(
    *,
    category: str = "broad-except",
    patch_source: str = "none",
    file: str = "src/app.py",
    line: int = 10,
) -> Finding:
    """Build a minimal Finding for pipeline tests."""
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id="test-0001",
        file=file,
        line=line,
        end_line=line,
        column=None,
        category=category,
        category_group="quality",
        severity="medium",
        corroboration="single",
        primary_message="test message",
        corroborators=(
            Corroborator.from_provider(
                provider="QLTY",
                rule_id="test-rule",
                rule_url=None,
                original_message="test",
            ),
        ),
        fix_hint=None,
        patch=None,
        patch_source=patch_source,
        patch_confidence=None,
        context_snippet="",
        source_file_hash="",
        cwe=None,
        autofixable=False,
        tags=(),
    )


class DeriveAutofixableTests(unittest.TestCase):
    """Test the autofixable derivation step (per §A.4.1 plan mandate)."""

    def test_autofixable_is_derived_from_patch_source(self) -> None:
        from scripts.quality.rollup_v2.pipeline import _derive_autofixable

        inputs = [
            _make_finding(category="broad-except", patch_source="none"),
            _make_finding(category="broad-except", patch_source="deterministic"),
            _make_finding(category="broad-except", patch_source="llm"),
        ]
        out = _derive_autofixable(inputs)
        self.assertFalse(out[0].autofixable)  # patch_source == "none"
        self.assertTrue(out[1].autofixable)  # deterministic
        self.assertTrue(out[2].autofixable)  # llm


class PipelineResultTests(unittest.TestCase):
    """Test PipelineResult dataclass."""

    def test_pipeline_result_fields(self) -> None:
        from scripts.quality.rollup_v2.pipeline import PipelineResult

        result = PipelineResult(
            findings=[],
            normalizer_errors=[],
            security_drops=[],
            canonical_payload={},
            markdown="",
        )
        self.assertEqual(result.findings, [])
        self.assertEqual(result.markdown, "")


class RunPipelineTests(unittest.TestCase):
    """Test the run_pipeline orchestrator."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name).resolve()
        (self.repo_root / "src").mkdir()
        (self.repo_root / "src" / "app.py").write_text(
            "x = 1\n" * 20, encoding="utf-8"
        )
        self.output_dir = self.repo_root / "output"
        self.output_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_artifacts_produce_empty_rollup(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        result = run_pipeline(
            artifacts={}, repo_root=self.repo_root, output_dir=self.output_dir
        )
        self.assertEqual(result.findings, [])
        self.assertEqual(result.normalizer_errors, [])
        self.assertIn("0 findings", result.markdown)

    def test_pipeline_with_qlty_artifact(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        artifacts = {
            "qlty": {
                "issues": [
                    {
                        "rule_id": "broad-except",
                        "file": "src/app.py",
                        "line": 5,
                        "severity": "high",
                        "message": "Catching too broad an exception",
                    }
                ]
            }
        }
        result = run_pipeline(
            artifacts=artifacts,
            repo_root=self.repo_root,
            output_dir=self.output_dir,
        )
        self.assertTrue(len(result.findings) >= 1)
        self.assertEqual(result.normalizer_errors, [])

    def test_pipeline_builds_provider_summaries(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        artifacts = {
            "qlty": {
                "issues": [
                    {
                        "rule_id": "unused-import",
                        "file": "src/app.py",
                        "line": 1,
                        "severity": "low",
                        "message": "unused import os",
                    }
                ]
            }
        }
        result = run_pipeline(
            artifacts=artifacts,
            repo_root=self.repo_root,
            output_dir=self.output_dir,
        )
        self.assertIn("provider_summaries", result.canonical_payload)
        summaries = result.canonical_payload["provider_summaries"]
        self.assertTrue(len(summaries) >= 1)


class PipelineErrorBoundaryTests(unittest.TestCase):
    """Test pipeline error boundary (per §A.6) -- malformed artifacts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmp.name).resolve()
        self.output_dir = self.repo_root / "output"
        self.output_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_malformed_codacy_artifact_still_produces_rollup(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        # Codacy normalizer does (artifact or {}).get("issues", [])
        # A string like "not-a-dict" has no .get, so it falls through as empty.
        # To trigger a real error, pass an artifact that causes parse() to crash:
        # a list will fail on .get() call  -> AttributeError caught by BaseNormalizer
        artifacts = {
            "codacy": [1, 2, 3],  # list has no .get method -> AttributeError
        }
        result = run_pipeline(
            artifacts=artifacts,
            repo_root=self.repo_root,
            output_dir=self.output_dir,
        )
        # Rollup is still produced
        self.assertIsNotNone(result.markdown)
        self.assertTrue(len(result.normalizer_errors) >= 1)
        # Banner present in markdown
        self.assertIn("Normalizer errors", result.markdown)

    def test_malformed_artifact_alongside_valid(self) -> None:
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        (self.repo_root / "src").mkdir(exist_ok=True)
        (self.repo_root / "src" / "app.py").write_text("x = 1\n" * 20, encoding="utf-8")

        artifacts = {
            "codacy": [1, 2, 3],  # malformed - list has no .get
            "qlty": {
                "issues": [
                    {
                        "rule_id": "unused-import",
                        "file": "src/app.py",
                        "line": 1,
                        "severity": "low",
                        "message": "unused import os",
                    }
                ]
            },
        }
        result = run_pipeline(
            artifacts=artifacts,
            repo_root=self.repo_root,
            output_dir=self.output_dir,
        )
        # Valid findings from qlty are still processed
        self.assertTrue(len(result.findings) >= 1)
        # Codacy error captured
        self.assertTrue(len(result.normalizer_errors) >= 1)


if __name__ == "__main__":
    unittest.main()
