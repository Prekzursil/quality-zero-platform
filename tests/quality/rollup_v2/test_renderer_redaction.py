"""Writer-side belt-and-suspenders redaction test (per §B.1.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import render_markdown
from scripts.quality.rollup_v2.types.corroborator import Corroborator
from scripts.quality.rollup_v2.types.finding import (
    CATEGORY_GROUP_QUALITY,
    SCHEMA_VERSION,
    Finding,
)


class WriterRedactionTests(unittest.TestCase):
    def _leaky_finding(self) -> Finding:
        """Construct a finding as if a buggy normalizer forgot to redact."""
        leaked_secret = 'DB_PASSWORD = "superlongsecretpasswordvalue"'
        corroborator = Corroborator.from_provider(
            provider="Codacy",
            rule_id="Pylint_W0703",
            rule_url=None,
            original_message=leaked_secret,
        )
        return Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="leak-1",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="broad-except",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="medium",
            corroboration="single",
            primary_message=leaked_secret,
            corroborators=(corroborator,),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet=leaked_secret,  # deliberately unredacted
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error=None,
        )

    def test_writer_redacts_leaked_secret_in_message(self):
        payload = {
            "schema_version": SCHEMA_VERSION,
            "total_findings": 1,
            "findings": [self._leaky_finding()],
            "provider_summaries": [],
            "unmapped_rules": [],
            "normalizer_errors": [],
        }
        md = render_markdown(payload)
        self.assertNotIn("superlongsecretpasswordvalue", md)
        self.assertIn("<REDACTED>", md)

    def test_writer_redacts_leaked_secret_in_fix_hint(self):
        """Fix hint with a secret is also redacted."""
        f = self._leaky_finding()
        # Use object.__setattr__ to bypass frozen — simulating a malformed Finding
        patched = Finding(
            schema_version=f.schema_version,
            finding_id=f.finding_id,
            file=f.file,
            line=f.line,
            end_line=f.end_line,
            column=f.column,
            category=f.category,
            category_group=f.category_group,
            severity=f.severity,
            corroboration=f.corroboration,
            primary_message="clean message",
            corroborators=f.corroborators,
            fix_hint='Set MY_API_KEY = "verylongsecretkeythatwillberedacted"',
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="# clean",
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error=None,
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "total_findings": 1,
            "findings": [patched],
            "provider_summaries": [],
            "unmapped_rules": [],
            "normalizer_errors": [],
        }
        md = render_markdown(payload)
        self.assertNotIn("verylongsecretkeythatwillberedacted", md)
        self.assertIn("<REDACTED>", md)

    def test_writer_redacts_leaked_secret_in_patch(self):
        """Patch diff containing a secret is redacted."""
        leaked_patch = (
            "--- a/config.py\n"
            "+++ b/config.py\n"
            "@@ -1 +1 @@\n"
            '-MY_SECRET = "verylongsecretinpatchdiff1234"\n'
            "+MY_SECRET = os.environ['SECRET']\n"
        )
        corr = Corroborator.from_provider(
            provider="Codacy",
            rule_id="rule",
            rule_url=None,
            original_message="msg",
        )
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="patch-leak",
            file="config.py",
            line=1,
            end_line=1,
            column=None,
            category="hardcoded-secret",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="high",
            corroboration="single",
            primary_message="Hardcoded secret",
            corroborators=(corr,),
            fix_hint=None,
            patch=leaked_patch,
            patch_source="deterministic",
            patch_confidence="high",
            context_snippet="# clean",
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=True,
            tags=(),
            patch_error=None,
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "total_findings": 1,
            "findings": [f],
            "provider_summaries": [],
            "unmapped_rules": [],
            "normalizer_errors": [],
        }
        md = render_markdown(payload)
        self.assertNotIn("verylongsecretinpatchdiff1234", md)
        self.assertIn("<REDACTED>", md)

    def test_clean_finding_passes_through(self):
        """Finding with no secrets has no REDACTED markers."""
        corr = Corroborator.from_provider(
            provider="Codacy",
            rule_id="rule",
            rule_url=None,
            original_message="Clean message",
        )
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="clean-1",
            file="clean.py",
            line=1,
            end_line=1,
            column=None,
            category="broad-except",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="low",
            corroboration="single",
            primary_message="Too broad exception clause",
            corroborators=(corr,),
            fix_hint="Use specific exception",
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="try:\n    pass\nexcept Exception:\n    pass",
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error=None,
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "total_findings": 1,
            "findings": [f],
            "provider_summaries": [],
            "unmapped_rules": [],
            "normalizer_errors": [],
        }
        md = render_markdown(payload)
        self.assertNotIn("<REDACTED>", md)
        self.assertIn("Too broad exception clause", md)
        self.assertIn("Use specific exception", md)


if __name__ == "__main__":
    unittest.main()
