"""Every text artifact the control plane writes is LF on every OS.

``Path.write_text`` and text-mode ``open(..., "w")`` translate ``"\\n"`` to
``os.linesep``, so on Windows they wrote CRLF. The committed artifacts are LF
(QZP's own preflight tells Windows users to clone with core.autocrlf=false),
so on exactly that recommended Windows setup ``bash scripts/verify`` failed:
regenerated rulesets "drifted" from the committed copies on every byte of line
ending (test_dashboard_extras: 5 failures, 18/18 rulesets reported as drift).

This guard walks the AST of every script and requires an explicit ``newline=``
on every text-mode writer, so the platform's outputs are byte-identical on
Linux CI and on a Windows checkout.
"""

from __future__ import absolute_import

import ast
import re
import unittest
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _text_write_mode(call: ast.Call, mode_index: int) -> bool:
    mode = None
    if len(call.args) > mode_index and isinstance(call.args[mode_index], ast.Constant):
        mode = call.args[mode_index].value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = keyword.value.value
    # A mode string is made only of mode letters; anything else (e.g. the
    # member name in ZipFile.open("coverage-100/coverage.json")) is not one.
    if not isinstance(mode, str) or not re.fullmatch(r"[rwxabtU+]+", mode):
        return False
    return any(c in mode for c in "wax") and "b" not in mode


def _is_unpinned_text_writer(call: ast.Call) -> bool:
    if any(keyword.arg == "newline" for keyword in call.keywords):
        return False
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "write_text":
        return True
    if isinstance(func, ast.Name) and func.id == "open":
        return _text_write_mode(call, 1)
    if isinstance(func, ast.Attribute) and func.attr == "open":
        return _text_write_mode(call, 0)
    return False


def unpinned_writers(source: str, name: str) -> List[str]:
    """``name:line`` of every text-mode writer in ``source`` lacking ``newline=``."""
    return [
        f"{name}:{node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _is_unpinned_text_writer(node)
    ]


class TextWritersPinLfTests(unittest.TestCase):
    """The control plane's writers, and the detector that guards them."""

    def test_every_script_writer_pins_newline(self) -> None:
        offenders = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            offenders += unpinned_writers(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], "text writers without newline= (CRLF on Windows)")

    def test_detector_flags_each_unpinned_form(self) -> None:
        source = "\n".join(
            [
                'p.write_text(s, encoding="utf-8")',
                'open(f, "w", encoding="utf-8")',
                'open(f, mode="a")',
                'p.open("x")',
            ]
        )
        self.assertEqual(unpinned_writers(source, "m"), ["m:1", "m:2", "m:3", "m:4"])

    def test_detector_ignores_pinned_binary_and_read_calls(self) -> None:
        source = "\n".join(
            [
                'p.write_text(s, encoding="utf-8", newline="\\n")',
                'open(f, "w", newline="\\n")',
                'open(f, "wb")',
                'p.open("rb")',
                "open(f)",
                'p.open(mode="r")',
                "p.open(m)",
                "p.write_bytes(b)",
                'zf.open("coverage-100/coverage.json")',
            ]
        )
        self.assertEqual(unpinned_writers(source, "m"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
