"""Tests for JSON Schema validation of canonical findings (per §A.9.5)."""
from __future__ import absolute_import

import json
import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "schemas" / "qzp-finding-v1.json"
)


def _load_schema() -> dict:
    """Load the JSON Schema from disk."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _sample_finding() -> dict:
    """Build a minimal valid finding dict."""
    return {
        "schema_version": "qzp-finding/1",
        "finding_id": "qzp-0001",
        "file": "src/app.py",
        "line": 42,
        "end_line": 42,
        "column": None,
        "category": "unused-import",
        "category_group": "quality",
        "severity": "low",
        "corroboration": "single",
        "primary_message": "Unused import os",
        "corroborators": [
            {
                "provider": "QLTY",
                "rule_id": "W0611",
                "rule_url": None,
                "original_message": "Unused import os",
                "provider_priority_rank": 5,
            }
        ],
        "fix_hint": None,
        "patch": None,
        "patch_source": "none",
        "patch_confidence": None,
        "context_snippet": "import os",
        "source_file_hash": "",
        "cwe": None,
        "autofixable": False,
        "tags": [],
        "patch_error": None,
    }


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class JsonSchemaTests(unittest.TestCase):
    """Validate sample findings against the JSON Schema."""

    def test_schema_file_exists(self) -> None:
        self.assertTrue(SCHEMA_PATH.exists(), f"Schema not found at {SCHEMA_PATH}")

    def test_schema_is_valid_json(self) -> None:
        schema = _load_schema()
        self.assertIn("$schema", schema)
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

    def test_valid_finding_passes(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        jsonschema.validate(instance=finding, schema=schema)

    def test_finding_with_patch_passes(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        finding["patch"] = "--- a/src/app.py\n+++ b/src/app.py\n@@ -42,1 +42,0 @@\n-import os"
        finding["patch_source"] = "deterministic"
        finding["patch_confidence"] = "high"
        finding["autofixable"] = True
        jsonschema.validate(instance=finding, schema=schema)

    def test_finding_with_multi_corroboration_passes(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        finding["corroboration"] = "multi"
        finding["corroborators"].append({
            "provider": "SonarCloud",
            "rule_id": "python:S1481",
            "rule_url": "https://rules.sonarsource.com/python/RSPEC-1481",
            "original_message": "Remove unused variable",
            "provider_priority_rank": 1,
        })
        jsonschema.validate(instance=finding, schema=schema)

    def test_invalid_schema_version_fails(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        finding["schema_version"] = "qzp-finding/99"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=finding, schema=schema)

    def test_invalid_severity_fails(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        finding["severity"] = "unknown"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=finding, schema=schema)

    def test_missing_required_field_fails(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        del finding["file"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=finding, schema=schema)

    def test_empty_corroborators_fails(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        finding["corroborators"] = []
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=finding, schema=schema)

    def test_additional_properties_rejected(self) -> None:
        schema = _load_schema()
        finding = _sample_finding()
        finding["unknown_field"] = "should fail"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=finding, schema=schema)

    def test_pipeline_output_validates_against_schema(self) -> None:
        """Integration: run pipeline and validate each finding against schema."""
        import tempfile

        from scripts.quality.rollup_v2.pipeline import run_pipeline

        schema = _load_schema()
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            (repo_root / "src").mkdir()
            (repo_root / "src" / "app.py").write_text("x = 1\n" * 5, encoding="utf-8")
            output_dir = repo_root / "output"
            output_dir.mkdir()

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
                artifacts=artifacts, repo_root=repo_root, output_dir=output_dir
            )
            for finding_dict in result.canonical_payload.get("findings", []):
                jsonschema.validate(instance=finding_dict, schema=schema)


if __name__ == "__main__":
    unittest.main()
