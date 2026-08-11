"""Anti-drift contract for the helpers EMBEDDED in ``reusable-quality.yml``.

``reusable-quality.yml`` executes inside the CALLER's checkout and has no access
to this repository's ``scripts/`` tree, so the lean gate's Python helpers are
embedded in the workflow verbatim (a network fetch would either be unpinned or
skew against a caller pinned to an older workflow ref).

Duplication is only safe when drift is impossible, so this module asserts that
each embedded copy is **byte-identical** to the unit-tested module it came from.
The modules carry the tests and the 100% line+branch coverage; this file carries
the guarantee that the workflow actually runs that same code.

The extractor is itself detector-controlled: ``test_extractor_reports_a_drifted
_copy`` proves it FIRES on a deliberately mutated copy, so a passing identity
assertion is evidence rather than a silent no-op.
"""

from __future__ import absolute_import

import ast
import unittest
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-quality.yml"
MARKER_PREFIX = "QZP_EMBED_"

#: marker -> the source-of-truth module the embedded copy must equal.
EMBEDDED_HELPERS: Dict[str, Path] = {
    "osv_severity_gate": ROOT / "scripts" / "quality" / "osv_severity_gate.py",
}

RESYNC_HINT = (
    "The embedded copy in .github/workflows/reusable-quality.yml has DRIFTED from "
    "its source module. Re-paste the module verbatim into the heredoc, keeping the "
    "block's indentation on every non-empty line and leaving blank lines empty."
)


def extract_embedded_helper(workflow_text: str, marker: str) -> str:
    """Return the heredoc body for ``marker``, dedented back to module form."""
    token = MARKER_PREFIX + marker
    lines = workflow_text.split("\n")
    start = None
    for index, line in enumerate(lines):
        if line.rstrip().endswith("<<'" + token + "'"):
            start = index + 1
            break
    if start is None:
        raise AssertionError("no heredoc opener <<'" + token + "' in " + WORKFLOW.name)
    end = None
    for index in range(start, len(lines)):
        if lines[index].strip() == token:
            end = index
            break
    if end is None:
        raise AssertionError("no heredoc terminator " + token + " in " + WORKFLOW.name)
    terminator = lines[end]
    indent = len(terminator) - len(terminator.lstrip(" "))
    body: List[str] = []
    for line in lines[start:end]:
        if not line.strip():
            body.append("")
            continue
        if not line.startswith(" " * indent):
            raise AssertionError("embedded line is under-indented for the heredoc: " + repr(line))
        body.append(line[indent:])
    return "\n".join(body) + "\n"


class EmbeddedHelperParityTests(unittest.TestCase):
    """Each embedded copy must equal its module, byte for byte."""

    def setUp(self) -> None:
        """Read the workflow once per test."""
        self.workflow_text = WORKFLOW.read_text(encoding="utf-8")

    def test_every_helper_is_embedded_byte_identical(self) -> None:
        """The workflow runs exactly the code the unit tests cover."""
        for marker, module_path in EMBEDDED_HELPERS.items():
            with self.subTest(marker=marker):
                embedded = extract_embedded_helper(self.workflow_text, marker)
                self.assertEqual(embedded, module_path.read_text(encoding="utf-8"), RESYNC_HINT)

    def test_every_embedded_copy_parses_as_python(self) -> None:
        """A mis-indented paste would produce a heredoc that is not valid Python."""
        for marker in EMBEDDED_HELPERS:
            with self.subTest(marker=marker):
                ast.parse(extract_embedded_helper(self.workflow_text, marker))

    def test_extractor_reports_a_drifted_copy(self) -> None:
        """DETECTOR CONTROL: the extractor must FIRE on a mutated copy.

        Without this, an extractor that silently returned the module's own text
        (or an empty string compared against an empty string) would report a
        permanent green while the workflow ran something else entirely.
        """
        synthetic = "\n".join(
            [
                "        run: |",
                "          cat > x.py <<'QZP_EMBED_demo'",
                "          value = 1",
                "",
                "          value = 2",
                "          QZP_EMBED_demo",
            ]
        )
        self.assertEqual(extract_embedded_helper(synthetic, "demo"), "value = 1\n\nvalue = 2\n")
        self.assertNotEqual(extract_embedded_helper(synthetic, "demo"), "value = 1\n\nvalue = 3\n")

    def test_extractor_rejects_a_missing_opener(self) -> None:
        """A helper that is referenced but never embedded is an error, not a pass."""
        with self.assertRaises(AssertionError):
            extract_embedded_helper("nothing here", "osv_severity_gate")

    def test_extractor_rejects_an_unterminated_heredoc(self) -> None:
        """A truncated heredoc must not silently yield a short body."""
        with self.assertRaises(AssertionError):
            extract_embedded_helper("          cat > x.py <<'QZP_EMBED_demo'\n          value = 1", "demo")

    def test_extractor_rejects_an_under_indented_line(self) -> None:
        """A line below the heredoc indent would corrupt the written Python."""
        synthetic = "\n".join(
            [
                "          cat > x.py <<'QZP_EMBED_demo'",
                "  value = 1",
                "          QZP_EMBED_demo",
            ]
        )
        with self.assertRaises(AssertionError):
            extract_embedded_helper(synthetic, "demo")


class EmbeddedHelperWiringTests(unittest.TestCase):
    """The workflow must actually invoke what it materialises."""

    def setUp(self) -> None:
        """Read the workflow once per test."""
        self.workflow_text = WORKFLOW.read_text(encoding="utf-8")

    def test_gate_deps_delegates_the_blocking_decision_to_the_classifier(self) -> None:
        """rc==1 must go through the severity floor, not straight to `exit 1`."""
        self.assertIn(
            'python3 "${RUNNER_TEMP}/qzp-lean-gate/osv_severity_gate.py" "$json_report"',
            self.workflow_text,
        )
        self.assertIn("--format json", self.workflow_text)
        # The blanket "any exit 1 is a red branch" form must not come back.
        self.assertNotIn(
            'echo "FAIL gate-deps: osv-scanner found actionable vulnerabilities (exit 1).',
            self.workflow_text,
        )

    def test_the_other_osv_exit_codes_keep_their_documented_behaviour(self) -> None:
        """0 / 127 / 128 / 129 / 130 semantics are untouched by the severity floor."""
        for expected in [
            'is_scan_error() { case "$1" in 127|129|130) return 0 ;; *) return 1 ;; esac; }',
            'is_retryable() { case "$1" in 127|129) return 0 ;; *) return 1 ;; esac; }',
            'echo "PASS gate-deps: osv-scanner found 0 actionable CVEs."',
            "osv-scanner exited 128 (no package sources found)",
            "exit 75",
        ]:
            self.assertIn(expected, self.workflow_text, expected)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
