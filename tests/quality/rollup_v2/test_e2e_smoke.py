"""End-to-end smoke test for the rollup_v2 pipeline (per Phase 18.2)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


class E2ESmokeTest(unittest.TestCase):
    """Run the entire pipeline from fixture artifacts through to outputs."""

    def test_full_pipeline_produces_both_outputs(self) -> None:
        """Pipeline with realistic artifacts produces canonical.json + rollup.md."""
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()

            # Create source files that the normalizer findings reference
            (repo_root / "src").mkdir()
            (repo_root / "src" / "app.py").write_text(
                "import os\nimport sys\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            (repo_root / "src" / "utils.py").write_text(
                "def helper():\n    try:\n        pass\n    except:\n        pass\n",
                encoding="utf-8",
            )

            output_dir = repo_root / "output"
            output_dir.mkdir()

            # Multi-provider artifacts
            artifacts = {
                "qlty": {
                    "issues": [
                        {
                            "rule_id": "unused-import",
                            "file": "src/app.py",
                            "line": 1,
                            "severity": "low",
                            "message": "unused import os",
                        },
                        {
                            "rule_id": "broad-except",
                            "file": "src/utils.py",
                            "line": 4,
                            "severity": "medium",
                            "message": "Too broad exception clause",
                        },
                    ]
                },
                "codacy": {
                    "issues": [
                        {
                            "patternId": "W0611",
                            "filename": "src/app.py",
                            "line": 1,
                            "severity": "Warning",
                            "message": "Unused import os",
                        }
                    ]
                },
            }

            result = run_pipeline(
                artifacts=artifacts,
                repo_root=repo_root,
            )

            # Write outputs (simulating __main__.py behavior)
            canonical_path = output_dir / "canonical.json"
            canonical_path.write_text(
                json.dumps(result.canonical_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            rollup_path = output_dir / "rollup.md"
            rollup_path.write_text(result.markdown, encoding="utf-8")

            # Assert both files exist and are non-empty
            self.assertTrue(canonical_path.exists())
            self.assertTrue(rollup_path.exists())
            self.assertGreater(canonical_path.stat().st_size, 10)
            self.assertGreater(rollup_path.stat().st_size, 10)

            # Assert canonical.json is valid JSON
            payload = json.loads(canonical_path.read_text(encoding="utf-8"))
            self.assertIn("findings", payload)
            self.assertIn("provider_summaries", payload)
            self.assertIn("total_findings", payload)
            self.assertGreater(payload["total_findings"], 0)

            # Assert findings have stable IDs
            for f in payload["findings"]:
                self.assertTrue(f["finding_id"].startswith("qzp-"))

            # Assert markdown has expected sections
            md = rollup_path.read_text(encoding="utf-8")
            self.assertIn("Provider Summary", md)
            self.assertIn("src/app.py", md)
            self.assertIn("src/utils.py", md)

    def test_empty_pipeline_produces_celebration(self) -> None:
        """Pipeline with no artifacts produces celebration banner."""
        from scripts.quality.rollup_v2.pipeline import run_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp).resolve()
            output_dir = repo_root / "output"
            output_dir.mkdir()

            result = run_pipeline(
                artifacts={},
                repo_root=repo_root,
            )

            self.assertEqual(result.findings, [])
            self.assertIn("0 findings", result.markdown)

    def test_cli_entrypoint_runs_successfully(self) -> None:
        """CLI entrypoint can be invoked and returns 0."""
        from scripts.quality.rollup_v2.__main__ import main

        with tempfile.TemporaryDirectory() as tmp:
            artifacts_dir = Path(tmp) / "artifacts"
            artifacts_dir.mkdir()
            output_dir = Path(tmp) / "output"
            output_dir.mkdir()

            with mock_patch(
                "sys.argv",
                [
                    "__main__.py",
                    "--artifacts-dir", str(artifacts_dir),
                    "--output-dir", str(output_dir),
                    "--repo", "owner/repo",
                    "--sha", "abc123",
                ],
            ):
                result = main()
            self.assertEqual(result, 0)

            # Output files should be created
            self.assertTrue((output_dir / "canonical.json").exists())
            self.assertTrue((output_dir / "rollup.md").exists())


if __name__ == "__main__":
    unittest.main()
