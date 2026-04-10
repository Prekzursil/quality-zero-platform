"""QualitySecrets normalizer (per design §4.2 + §A.6, Task 6.9 special handling)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.types.finding import (
    CATEGORY_GROUP_SECURITY,
    Finding,
)


class SecretsNormalizer(BaseNormalizer):
    """Normalize secrets preflight artifacts into canonical Findings.

    Every detected secret produces a Finding with severity='critical',
    category_group='security', category='hardcoded-secret', cwe='CWE-798'.
    Redaction is extra-critical here since the secret IS the finding.
    """
    provider = "QualitySecrets"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        secrets = (artifact or {}).get("secrets", [])
        for index, secret in enumerate(secrets):
            secret_type = str(secret.get("type", "Unknown Secret"))
            file_path = str(secret.get("file", ""))
            line = int(secret.get("line") or 1)
            rule_id = str(secret.get("rule_id", "hardcoded-secret"))
            yield self._build_finding(
                finding_id=f"secret-{index:04d}",
                file=file_path,
                line=line,
                category="hardcoded-secret",
                category_group=CATEGORY_GROUP_SECURITY,
                severity="critical",
                primary_message=f"Detected {secret_type} in {file_path}:{line}",
                rule_id=rule_id,
                rule_url=None,
                original_message=f"Secret of type {secret_type} detected",
                context_snippet="",
                cwe="CWE-798",
            )
