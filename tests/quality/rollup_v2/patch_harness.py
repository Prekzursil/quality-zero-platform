"""Shared patch generator golden-file test harness (per §A.1.5 + §B.3.5).

Class-definition-time dynamic test method generation: discovers fixture triples
from tests/quality/rollup_v2/fixtures/patches/<category>/case_NN.* and attaches
one test method per fixture to PatchGeneratorGoldenTests.
"""
from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality.rollup_v2.patches import dispatch
from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import SCHEMA_VERSION, Finding
from scripts.quality.rollup_v2.schema.patch import PatchResult

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "patches"


def _finding_from_json(data: dict) -> Finding:
    """Build a Finding from a fixture JSON dict.

    The fixture JSON contains only the fields relevant to each test case.
    All other fields get sensible defaults so fixture files stay minimal.
    """
    provider = data.get("provider", "Codacy")
    rule_id = data.get("rule_id", "FIXTURE")
    corr = Corroborator.from_provider(
        provider=provider,
        rule_id=rule_id,
        rule_url=data.get("rule_url"),
        original_message=data.get("primary_message", "fixture message"),
    )
    return Finding(
        schema_version=SCHEMA_VERSION,
        finding_id=data.get("finding_id", "qzp-0000"),
        file=data["file"],
        line=data.get("line", 1),
        end_line=data.get("end_line", data.get("line", 1)),
        column=data.get("column"),
        category=data["category"],
        category_group=data.get("category_group", "quality"),
        severity=data.get("severity", "medium"),
        corroboration=data.get("corroboration", "single"),
        primary_message=data.get("primary_message", "fixture message"),
        corroborators=(corr,),
        fix_hint=data.get("fix_hint"),
        patch=data.get("patch"),
        patch_source=data.get("patch_source", "none"),
        patch_confidence=data.get("patch_confidence"),
        context_snippet=data.get("context_snippet", ""),
        source_file_hash=data.get("source_file_hash", "sha256:fixture"),
        cwe=data.get("cwe"),
        autofixable=data.get("autofixable", False),
        tags=tuple(data.get("tags", ())),
    )


class PatchGeneratorGoldenTests(unittest.TestCase):
    """Parametrized tests -- methods attached dynamically at module load time."""
    pass


def _make_golden_test(category: str, case_name: str, fixture_dir: Path):
    """Create a test method for one golden fixture triple."""

    def test_method(self):
        input_path = fixture_dir / f"{case_name}.input.py"
        finding_path = fixture_dir / f"{case_name}.finding.json"
        expected_diff_path = fixture_dir / f"{case_name}.expected.diff"

        # Read with newline="" to preserve exact line endings in the file
        with open(input_path, encoding="utf-8", newline="") as f:
            source = f.read()
        finding = _finding_from_json(json.loads(finding_path.read_text(encoding="utf-8")))
        with open(expected_diff_path, encoding="utf-8", newline="") as f:
            expected_diff = f.read()

        # Normalize line endings for platform independence.
        # On Windows: Git converts LF→CRLF (autocrlf), generators expect LF.
        # On Linux: Git keeps LF, but bad-line-ending tests NEED CRLF input.
        if category == "bad-line-ending":
            # Inject CRLF regardless of what Git did to the fixture file.
            # Normalize first (remove any existing \r\n), then add \r\n.
            source = source.replace("\r\n", "\n").replace("\n", "\r\n")
        else:
            source = source.replace("\r\n", "\n")

        # Use a tmp dir as repo_root so path_safety passes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_dir = root / Path(finding.file).parent
            file_dir.mkdir(parents=True, exist_ok=True)
            with open(root / finding.file, "w", encoding="utf-8", newline="") as wf:
                wf.write(source)
            result = dispatch(finding, source_file_content=source, repo_root=root)

        self.assertIsInstance(result, PatchResult, f"Expected PatchResult for {category}/{case_name}")
        # Normalize CRLF → LF to handle Windows Git autocrlf in golden fixtures
        actual_diff = result.unified_diff.strip().replace("\r\n", "\n")  # type: ignore[union-attr]
        expected = expected_diff.strip().replace("\r\n", "\n")
        self.assertEqual(actual_diff, expected)
        self.assertEqual(result.category, category)  # type: ignore[union-attr]

    return test_method


def _discover_and_attach() -> None:
    """Walk the fixtures directory and attach one test method per fixture triple."""
    if not _FIXTURES_DIR.exists():
        return
    for category_dir in sorted(_FIXTURES_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        category = category_dir.name.replace("_", "-")
        input_files = sorted(category_dir.glob("*.input.py"))
        for input_file in input_files:
            case_name = input_file.name.replace(".input.py", "")
            # Only attach if all three files exist
            finding_path = category_dir / f"{case_name}.finding.json"
            diff_path = category_dir / f"{case_name}.expected.diff"
            if not finding_path.exists() or not diff_path.exists():
                continue
            method = _make_golden_test(category, case_name, category_dir)
            method.__name__ = f"test_{category.replace('-', '_')}_{case_name}"
            method.__qualname__ = f"PatchGeneratorGoldenTests.{method.__name__}"
            setattr(PatchGeneratorGoldenTests, method.__name__, method)


_discover_and_attach()


if __name__ == "__main__":
    unittest.main()
