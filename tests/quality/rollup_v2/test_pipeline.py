"""Tests for pipeline orchestrator (per design §4.2 + §A.3.5)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import SCHEMA_VERSION, Finding


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


class ProviderSummaryBranchTests(unittest.TestCase):
    """Cover partial branches in _build_provider_summaries and run_pipeline."""

    def test_severity_not_in_standard_levels_skips_increment(self) -> None:
        """Cover branch 127->123: severity not in ('high','medium','low')."""
        from scripts.quality.rollup_v2.pipeline import _build_provider_summaries

        # Finding with severity "critical" -- not in the counts dict keys
        f = _make_finding(category="broad-except")
        f = replace(f, severity="critical")
        summaries = _build_provider_summaries([f])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["total"], 1)
        # "critical" is not a standard key, so high/medium/low all stay 0
        self.assertEqual(summaries[0]["high"], 0)
        self.assertEqual(summaries[0]["medium"], 0)
        self.assertEqual(summaries[0]["low"], 0)

    def test_placeholder_skipped_when_provider_already_configured(self) -> None:
        """Cover branch: placeholder provider IS in configured_providers.

        All 4 PR 3 lanes are now registered normalizers, so RESERVED_LANE_KEYS
        is empty. We inject a temporary reservation to test the dedup logic.
        """
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        tmp = tempfile.TemporaryDirectory()
        repo_root = Path(tmp.name).resolve()
        output_dir = repo_root / "output"
        output_dir.mkdir()
        (repo_root / "src").mkdir()
        (repo_root / "src" / "app.py").write_text("x = 1\n" * 20, encoding="utf-8")

        test_label = "TestReserved Zero"
        mock_finding = replace(
            _make_finding(),
            corroborators=(
                Corroborator.from_provider(
                    provider=test_label,
                    rule_id="test",
                    rule_url=None,
                    original_message="test",
                ),
            ),
        )

        with patch(
            "scripts.quality.rollup_v2.pipeline.NORMALIZER_REGISTRY",
            {"qlty": MagicMock()},
        ) as mock_registry, patch(
            "scripts.quality.rollup_v2.pipeline.RESERVED_LANE_KEYS",
            {"test_reserved": test_label},
        ):
            mock_normalizer = mock_registry["qlty"]
            mock_normalizer.run.return_value = MagicMock(
                findings=[mock_finding],
                normalizer_errors=[],
                security_drops=[],
            )
            result = run_pipeline(
                artifacts={"qlty": {"issues": []}},
                repo_root=repo_root,
                output_dir=output_dir,
            )
        tmp.cleanup()

        # The test_label should appear once (from the finding), not twice
        labels = [s["provider"] for s in result.canonical_payload["provider_summaries"]]
        count = labels.count(test_label)
        self.assertEqual(count, 1, f"{test_label} should appear exactly once")


    def test_not_configured_placeholder_appended_when_no_matching_finding(self) -> None:
        """Cover line 231: placeholder appended when provider NOT in configured_providers."""
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        tmp = tempfile.TemporaryDirectory()
        repo_root = Path(tmp.name).resolve()
        output_dir = repo_root / "output"
        output_dir.mkdir()

        # Mock a reserved key whose label does NOT appear in any finding
        with patch(
            "scripts.quality.rollup_v2.pipeline.NORMALIZER_REGISTRY",
            {},
        ), patch(
            "scripts.quality.rollup_v2.pipeline.RESERVED_LANE_KEYS",
            {"phantom": "Phantom Zero"},
        ):
            result = run_pipeline(
                artifacts={},
                repo_root=repo_root,
                output_dir=output_dir,
            )
        tmp.cleanup()

        labels = [s["provider"] for s in result.canonical_payload["provider_summaries"]]
        self.assertIn("Phantom Zero", labels)


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
