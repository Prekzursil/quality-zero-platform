"""Tests for verify_action_pins.py (per §B.3.6)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.verify_action_pins import (
    main,
    scan_workflow,
    scan_workflows_dir,
)


class ScanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_workflow(self, content: str, name: str = "test.yml") -> Path:
        path = self.tmp_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_sha_pinned_action_passes(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: peter-evans/create-pull-request@c5a7806660adbe173f04e3e038b0ccdcd758773c
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 0)

    def test_floating_tag_detected(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: some-owner/some-action@v4
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["action"], "some-owner/some-action")
        self.assertEqual(violations[0]["ref"], "v4")

    def test_first_party_actions_exempted(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - uses: actions/upload-artifact@v4
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 0)

    def test_mixed_pinned_and_floating(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: good-owner/good-action@abc123def456abc123def456abc123def456abc1
      - uses: bad-owner/bad-action@main
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["action"], "bad-owner/bad-action")
        self.assertEqual(violations[0]["ref"], "main")

    def test_sha_with_inline_comment(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: chromaui/action@1cfa065cbdab28f6ca3afaeb3d761383076a35aa  # v11
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 0)

    def test_sub_action_path_handled(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: qltysh/qlty-action/install@a19242102d17e497f437d7466aa01b528537e899
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 0)

    def test_sub_action_floating_detected(self):
        wf = self._write_workflow("""
name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: qltysh/qlty-action/install@v1
""")
        violations = scan_workflow(wf)
        self.assertEqual(len(violations), 1)

    def test_line_number_reported(self):
        wf = self._write_workflow("""name: Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: bad/action@v1
""")
        violations = scan_workflow(wf)
        self.assertEqual(violations[0]["line"], "7")


class ScanWorkflowsDirTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workflows_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scans_multiple_files(self):
        (self.workflows_dir / "a.yml").write_text(
            "jobs:\n  j:\n    steps:\n      - uses: bad/action@v1\n", "utf-8"
        )
        (self.workflows_dir / "b.yml").write_text(
            "jobs:\n  j:\n    steps:\n      - uses: actions/checkout@v4\n", "utf-8"
        )
        violations = scan_workflows_dir(self.workflows_dir)
        self.assertEqual(len(violations), 1)

    def test_scans_yaml_extension_too(self):
        (self.workflows_dir / "c.yaml").write_text(
            "jobs:\n  j:\n    steps:\n      - uses: yaml-owner/yaml-action@v3\n", "utf-8"
        )
        violations = scan_workflows_dir(self.workflows_dir)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["action"], "yaml-owner/yaml-action")

    def test_empty_dir_returns_empty(self):
        violations = scan_workflows_dir(self.workflows_dir)
        self.assertEqual(len(violations), 0)

    def test_nonexistent_dir_returns_empty(self):
        violations = scan_workflows_dir(self.workflows_dir / "nonexistent")
        self.assertEqual(len(violations), 0)


class MainTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workflows_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_returns_zero_on_clean(self):
        (self.workflows_dir / "clean.yml").write_text(
            "jobs:\n  j:\n    steps:\n      - uses: actions/checkout@v4\n", "utf-8"
        )
        rc = main(["--workflows-dir", str(self.workflows_dir)])
        self.assertEqual(rc, 0)

    def test_main_returns_one_on_violation(self):
        (self.workflows_dir / "bad.yml").write_text(
            "jobs:\n  j:\n    steps:\n      - uses: bad/action@v2\n", "utf-8"
        )
        rc = main(["--workflows-dir", str(self.workflows_dir)])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
