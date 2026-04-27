"""Tests to close coverage gaps across rollup_v2 modules."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch as mock_patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import SCHEMA_VERSION, Finding


def _make_finding(
    *,
    category: str = "unused-import",
    file: str = "src/app.py",
    line: int = 10,
    severity: str = "medium",
    patch_source: str = "none",
    category_group: str = "quality",
) -> Finding:
    """Build a minimal Finding for gap tests."""
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id="test-0001",
        file=file,
        line=line,
        end_line=line,
        column=None,
        category=category,
        category_group=category_group,
        severity=severity,
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


class PipelineGapTests(unittest.TestCase):
    """Cover pipeline.py uncovered lines."""

    def test_unknown_artifact_key_skipped(self) -> None:
        """Line 204-205: unknown key -> continue."""
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            output_dir = repo_root / "out"
            output_dir.mkdir()
            result = run_pipeline(
                artifacts={"unknown_provider": {"data": "whatever"}},
                repo_root=repo_root,
                output_dir=output_dir,
            )
            self.assertEqual(result.findings, [])

    def test_read_source_file_missing_returns_empty(self) -> None:
        """Lines 79-80: OSError path."""
        from scripts.quality.rollup_v2.pipeline import _read_source_file

        result = _read_source_file("nonexistent.py", Path("/no/such/dir"))
        self.assertEqual(result, "")

    def test_patch_dispatch_exception_caught(self) -> None:
        """Lines 95-102: patch generator exception -> patch_error field."""
        from scripts.quality.rollup_v2.pipeline import _apply_patches

        finding = _make_finding(category="broad-except")
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            (repo_root / "src").mkdir()
            (repo_root / "src" / "app.py").write_text("x = 1\n" * 20, encoding="utf-8")

            with mock_patch(
                "scripts.quality.rollup_v2.pipeline.patch_dispatcher.dispatch",
                side_effect=RuntimeError("boom"),
            ):
                results = _apply_patches([finding], repo_root)
            self.assertEqual(len(results), 1)
            self.assertIn("boom", results[0].patch_error)

    def test_patch_dispatch_returns_patch_result(self) -> None:
        """Lines 104-111: successful PatchResult."""
        from scripts.quality.rollup_v2.pipeline import _apply_patches
        from scripts.quality.rollup_v2.schema.patch import PatchResult

        finding = _make_finding()
        patch_result = PatchResult(
            unified_diff="--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new",
            confidence="high",
            category="unused-import",
            generator_version="1.0",
            touches_files=frozenset({Path("src/app.py")}),
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            (repo_root / "src").mkdir()
            (repo_root / "src" / "app.py").write_text("x = 1\n" * 20, encoding="utf-8")

            with mock_patch(
                "scripts.quality.rollup_v2.pipeline.patch_dispatcher.dispatch",
                return_value=patch_result,
            ):
                results = _apply_patches([finding], repo_root)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].patch_source, "deterministic")
            self.assertEqual(results[0].patch_confidence, "high")
            self.assertIn("old", results[0].patch)


class RendererGapTests(unittest.TestCase):
    """Cover renderer.py uncovered lines."""

    def test_safe_with_none(self) -> None:
        """Line 34: _safe(None)."""
        from scripts.quality.rollup_v2.renderer import _safe

        self.assertEqual(_safe(None), "")
        self.assertEqual(_safe(""), "")

    def test_provider_label_singular(self) -> None:
        """Lines 40-41: _provider_label with 1 provider."""
        from scripts.quality.rollup_v2.renderer import _provider_label

        self.assertEqual(_provider_label([{"p": 1}]), "1 provider")
        self.assertEqual(_provider_label([{"p": 1}, {"p": 2}]), "2 providers")


def _baseline_finding_kwargs() -> dict:
    """Return the kwargs needed to build a minimal valid ``Finding``."""
    return dict(
        schema_version=SCHEMA_VERSION,
        finding_id="t",
        file="f",
        line=1,
        end_line=1,
        column=None,
        category="c",
        category_group="quality",
        severity="medium",
        corroboration="single",
        primary_message="m",
        corroborators=(),
        fix_hint=None,
        patch=None,
        patch_source="none",
        patch_confidence=None,
        context_snippet="",
        source_file_hash="",
        cwe=None,
        autofixable=False,
        tags=(),
    )


class FindingValidationGapTests(unittest.TestCase):
    """Cover finding.py validation error paths (lines 64, 68, 72)."""

    def _assert_validation_failure(self, *, field: str, **invalid_kwargs) -> None:
        kwargs = _baseline_finding_kwargs() | invalid_kwargs
        with self.assertRaises(AssertionError) as ctx:
            Finding(**kwargs)
        self.assertIn(field, str(ctx.exception))

    def test_invalid_patch_source_raises(self) -> None:
        """Line 64-66."""
        self._assert_validation_failure(field="patch_source", patch_source="INVALID")

    def test_invalid_corroboration_raises(self) -> None:
        """Line 68-70."""
        self._assert_validation_failure(field="corroboration", corroboration="INVALID")

    def test_invalid_schema_version_raises(self) -> None:
        """Line 72-74."""
        self._assert_validation_failure(field="schema_version", schema_version="wrong/99")


class DedupGapTests(unittest.TestCase):
    """Cover dedup.py line 55 -- style findings key by (file, line) only."""

    def test_style_findings_dedup_by_file_line(self) -> None:
        from scripts.quality.rollup_v2.dedup import dedup

        f1 = _make_finding(category="quote-style", category_group="style", line=10)
        f2 = replace(
            f1,
            category="spacing-convention",
            corroborators=(
                Corroborator.from_provider(
                    provider="SonarCloud",
                    rule_id="s-001",
                    rule_url=None,
                    original_message="spacing",
                ),
            ),
        )
        results = dedup([f1, f2])
        # Style findings at same (file, line) should be merged
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].corroboration, "multi")


class TaxonomyGapTests(unittest.TestCase):
    """Cover taxonomy.py uncovered lines."""

    def test_load_taxonomy_with_invalid_yaml(self) -> None:
        """Lines 26, 30: invalid taxonomy YAML raises ValueError."""
        from scripts.quality.rollup_v2 import taxonomy

        taxonomy.load_all_taxonomies.cache_clear()
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config" / "taxonomy"
            config_dir.mkdir(parents=True)
            # Write an invalid taxonomy YAML (missing provider or wrong type)
            (config_dir / "bad.yaml").write_text(
                "provider: 42\nmapping: not-a-dict\n", encoding="utf-8"
            )
            with mock_patch.object(taxonomy, "_CONFIG_DIR", config_dir):
                taxonomy.load_all_taxonomies.cache_clear()
                with self.assertRaises(ValueError):
                    taxonomy.load_all_taxonomies()
        taxonomy.load_all_taxonomies.cache_clear()

    def test_load_taxonomy_no_dir(self) -> None:
        """Line 22: config dir does not exist."""
        from scripts.quality.rollup_v2 import taxonomy

        taxonomy.load_all_taxonomies.cache_clear()
        with mock_patch.object(taxonomy, "_CONFIG_DIR", Path("/nonexistent/path")):
            taxonomy.load_all_taxonomies.cache_clear()
            result = taxonomy.load_all_taxonomies()
            self.assertEqual(result, {})
        taxonomy.load_all_taxonomies.cache_clear()

    def test_load_taxonomy_non_dict_yaml(self) -> None:
        """Line 26: YAML file that parses to non-dict -> skip."""
        from scripts.quality.rollup_v2 import taxonomy

        taxonomy.load_all_taxonomies.cache_clear()
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config" / "taxonomy"
            config_dir.mkdir(parents=True)
            (config_dir / "list.yaml").write_text("- item1\n- item2\n", encoding="utf-8")
            with mock_patch.object(taxonomy, "_CONFIG_DIR", config_dir):
                taxonomy.load_all_taxonomies.cache_clear()
                result = taxonomy.load_all_taxonomies()
                self.assertEqual(result, {})
        taxonomy.load_all_taxonomies.cache_clear()


class NormalizerCoverageGapTests(unittest.TestCase):
    """Cover normalizer lines missed by existing tests."""

    def test_coverage_normalizer_missing_components(self) -> None:
        """Coverage normalizer: missing components key."""
        from scripts.quality.rollup_v2.normalizers.coverage import CoverageNormalizer

        n = CoverageNormalizer()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            result = n.run(artifact={"summary": {"coverage_pct": 80}}, repo_root=repo_root)
            self.assertEqual(len(result.findings), 0)

    def test_coverage_normalizer_low_severity(self) -> None:
        """Coverage normalizer line 23: coverage >= 95 -> low severity."""
        from scripts.quality.rollup_v2.normalizers.coverage import CoverageNormalizer

        n = CoverageNormalizer()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            result = n.run(
                artifact={"components": [{"name": "src/a.py", "percent": 97.0}]},
                repo_root=repo_root,
            )
            self.assertEqual(len(result.findings), 1)
            self.assertEqual(result.findings[0].severity, "low")

    def test_sonarcloud_normalizer_missing_components(self) -> None:
        """SonarCloud normalizer: missing components."""
        from scripts.quality.rollup_v2.normalizers.sonarcloud import SonarCloudNormalizer

        n = SonarCloudNormalizer()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            result = n.run(artifact={"issues": [], "components": None}, repo_root=repo_root)
            self.assertEqual(len(result.findings), 0)

    def test_sonarcloud_component_key_without_colon(self) -> None:
        """SonarCloud normalizer line 36: component key without colon."""
        from scripts.quality.rollup_v2.normalizers.sonarcloud import _extract_file_from_component

        result = _extract_file_from_component("src/app.py")
        self.assertEqual(result, "src/app.py")


class MainLoadArtifactsGapTests(unittest.TestCase):
    """Cover __main__.py _load_artifacts path."""

    def test_load_artifacts_skips_missing(self) -> None:
        from scripts.quality.rollup_v2.__main__ import _load_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            artifacts_dir = Path(tmp)
            # No subdirectories exist
            result = _load_artifacts(artifacts_dir)
            self.assertEqual(result, {})

    def test_load_artifacts_reads_existing(self) -> None:
        from scripts.quality.rollup_v2.__main__ import _load_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            artifacts_dir = Path(tmp)
            qlty_dir = artifacts_dir / "qlty"
            qlty_dir.mkdir()
            (qlty_dir / "qlty.json").write_text('{"issues": []}', encoding="utf-8")
            result = _load_artifacts(artifacts_dir)
            self.assertIn("qlty", result)
            self.assertEqual(result["qlty"], {"issues": []})


if __name__ == "__main__":
    unittest.main()
