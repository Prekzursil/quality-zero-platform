"""Every text artifact the control plane writes is LF on every OS.

``Path.write_text`` and the text-mode file constructors (``open(..., "w")``,
``tempfile.NamedTemporaryFile(mode="w")``, ...) translate ``"\\n"`` to
``os.linesep``, so on Windows they wrote CRLF. The committed artifacts are LF
(QZP's own preflight tells Windows users to clone with core.autocrlf=false),
so on exactly that recommended Windows setup ``bash scripts/verify`` failed:
regenerated rulesets "drifted" from the committed copies on every byte of line
ending (test_dashboard_extras: 5 failures, 18/18 rulesets reported as drift),
and a CRLF temp patch makes ``git apply --check`` reject a valid diff.

This guard walks the AST of every script and requires every text-mode writer
to pass ``newline="\\n"`` (or ``newline=""``, which also disables translation),
so the platform's outputs are byte-identical on Linux CI and on Windows.
"""

from __future__ import absolute_import

import ast
import re
import unittest
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Positional index of the ``mode`` argument per file constructor. ``open`` on
# any other object (Path.open, ZipFile.open, ...) takes its mode first.
MODE_INDEX = {
    "open": 1,  # builtins.open / io.open / codecs.open
    "fdopen": 1,  # os.fdopen(fd, mode)
    "NamedTemporaryFile": 0,
    "TemporaryFile": 0,
    "SpooledTemporaryFile": 1,  # (max_size, mode, ...)
}
MODULE_OPENERS = {"io", "codecs"}  # io.open / codecs.open take (file, mode)
SAFE_NEWLINES = {"\n", ""}  # both write "\n" untranslated


def _mode_index(func: ast.expr) -> Optional[int]:
    if isinstance(func, ast.Name):
        return MODE_INDEX.get(func.id)
    if isinstance(func, ast.Attribute):
        if func.attr == "open":
            module = func.value.id if isinstance(func.value, ast.Name) else None
            return 1 if module in MODULE_OPENERS else 0
        return MODE_INDEX.get(func.attr)
    return None


def _text_write_mode(call: ast.Call, mode_index: int) -> bool:
    mode = None
    if len(call.args) > mode_index and isinstance(call.args[mode_index], ast.Constant):
        mode = call.args[mode_index].value
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = keyword.value.value
    # A mode string is made only of mode letters; anything else (e.g. the
    # member name in ZipFile.open("coverage-100/coverage.json")) is not one.
    # No literal mode means the constructor default: read for open(), binary
    # ("w+b") for the tempfile family -- neither translates newlines.
    if not isinstance(mode, str) or not re.fullmatch(r"[rwxabtU+]+", mode):
        return False
    return any(c in mode for c in "wax+") and "b" not in mode and mode != "r"


def _newline_is_lf(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg == "newline":
            return isinstance(keyword.value, ast.Constant) and keyword.value.value in SAFE_NEWLINES
    return False


def _is_unpinned_text_writer(call: ast.Call) -> bool:
    if _newline_is_lf(call):
        return False
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "write_text":
        return True
    index = _mode_index(func)
    return index is not None and _text_write_mode(call, index)


def unpinned_writers(source: str, name: str) -> List[str]:
    """``name:line`` of every text-mode writer in ``source`` not pinned to LF."""
    return [
        f"{name}:{node.lineno}"
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and _is_unpinned_text_writer(node)
    ]


class TextWritersPinLfTests(unittest.TestCase):
    """The control plane's writers, and the detector that guards them."""

    def test_every_script_writer_pins_lf(self) -> None:
        offenders = []
        for path in sorted(SCRIPTS.rglob("*.py")):
            offenders += unpinned_writers(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], 'text writers without newline="\\n" (CRLF on Windows)')

    def test_detector_flags_each_unpinned_form(self) -> None:
        source = "\n".join(
            [
                'p.write_text(s, encoding="utf-8")',
                'open(f, "w", encoding="utf-8")',
                'open(f, mode="a")',
                'p.open("x")',
                'io.open(f, "w")',
                'codecs.open(f, "w", "utf-8")',
                'os.fdopen(fd, "w")',
                'tempfile.NamedTemporaryFile(mode="w", suffix=".patch")',
                'NamedTemporaryFile("w+")',
                'tempfile.TemporaryFile(mode="w+t")',
                'tempfile.SpooledTemporaryFile(0, "w")',
                "p.write_text(s, newline=None)",
                'open(f, "w", newline="\\r\\n")',
                'open(f, "w", newline=nl)',
            ]
        )
        self.assertEqual(unpinned_writers(source, "m"), [f"m:{n}" for n in range(1, 15)])

    def test_detector_ignores_lf_pinned_binary_and_read_calls(self) -> None:
        source = "\n".join(
            [
                'p.write_text(s, encoding="utf-8", newline="\\n")',
                'open(f, "w", newline="\\n")',
                'open(f, "w", newline="")',
                'tempfile.NamedTemporaryFile(mode="w", newline="\\n")',
                'open(f, "wb")',
                'p.open("rb")',
                "open(f)",
                'open(f, "r")',
                'p.open(mode="r")',
                "p.open(m)",
                "p.write_bytes(b)",
                'zf.open("coverage-100/coverage.json")',
                "tempfile.NamedTemporaryFile()",
                'tempfile.TemporaryFile(mode="w+b")',
                "f()",
                "factory()()",
            ]
        )
        self.assertEqual(unpinned_writers(source, "m"), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
