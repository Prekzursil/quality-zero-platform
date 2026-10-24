"""Tests for ``scripts/quality/validate_control_plane.py``, the fleet-profile validator ``scripts/verify`` runs."""

from __future__ import absolute_import

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.quality import validate_control_plane as validator

ROOT = Path(__file__).resolve().parents[1]
COMMITTED_RULESETS = ROOT / "generated" / "rulesets"


class ValidateControlPlaneTests(unittest.TestCase):
    """Every enrolled profile validates, and the generated rulesets are exactly the committed ones."""

    def _main(self, *argv: str):
        out = io.StringIO()
        with patch.object(sys, "argv", ["validate_control_plane.py", *argv]), redirect_stdout(out):
            code = validator.main()
        return code, out.getvalue()

    def test_the_fleet_validates_and_regenerates_the_committed_rulesets(self) -> None:
        """The default inventory passes and --write-generated reproduces generated/rulesets byte for byte."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "nested" / "rulesets"
            code, out = self._main("--write-generated", str(out_dir))
            self.assertEqual(code, 0, out)
            written = sorted(path.name for path in out_dir.iterdir())
            self.assertEqual(written, sorted(path.name for path in COMMITTED_RULESETS.glob("*.json")))
            for name in written:
                with self.subTest(ruleset=name):
                    self.assertEqual(
                        (out_dir / name).read_text(encoding="utf-8"),
                        (COMMITTED_RULESETS / name).read_text(encoding="utf-8"),
                    )
        self.assertIn(f"Validated {len(written)} repo profiles.\n", out)

    def test_findings_are_printed_and_fail_the_run(self) -> None:
        """Any profile finding is printed and the exit code is 1; nothing is generated unless asked."""
        inventory = ROOT / "inventory" / "repos.yml"
        with patch.object(validator, "validate_profile", side_effect=lambda profile: [f"{profile['slug']}: broken"]):
            code, out = self._main("--inventory", str(inventory))
        self.assertEqual(code, 1)
        self.assertIn("Prekzursil/quality-zero-platform: broken\n", out)
        self.assertNotIn("Validated", out)

    def test_loading_by_path_bootstraps_the_repo_root(self) -> None:
        """Run as `python scripts/quality/validate_control_plane.py`, the module puts the repo root on sys.path."""
        module_path = Path(validator.__file__).resolve()
        repo_root = str(module_path.parents[2])
        for root_on_path in (False, True):
            with self.subTest(root_on_path=root_on_path):
                spec = importlib.util.spec_from_file_location("validate_control_plane_under_test", module_path)
                if spec is None or spec.loader is None:  # pragma: no cover - environment guard
                    self.fail("no module spec for validate_control_plane")
                module = importlib.util.module_from_spec(spec)
                original = list(sys.path)
                try:
                    sys.path[:] = [entry for entry in sys.path if entry != repo_root]
                    if root_on_path:
                        sys.path.insert(0, repo_root)
                    spec.loader.exec_module(module)
                    self.assertEqual(sys.path.count(repo_root), 1)
                finally:
                    sys.path[:] = original
                self.assertIs(module.load_inventory, validator.load_inventory)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
