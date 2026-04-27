"""BaseNormalizer abstract class (per design §B.1.2 + §A.6)."""
from __future__ import absolute_import

import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, final

from scripts.quality.rollup_v2.path_safety import (
    PathEscapedRootError,
    validate_finding_file,
)
from scripts.quality.rollup_v2.redaction import redact_secrets
from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    SCHEMA_VERSION,
    CategoryGroup,
    Finding,
)


@dataclass(frozen=True, slots=True)
class NormalizerResult:
    findings: Tuple[Finding, ...]
    normalizer_errors: Tuple[Dict[str, str], ...]
    security_drops: Tuple[Dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class FindingFields:
    """Per-finding inputs to ``BaseNormalizer._build_finding``.

    Bundles what used to be 14+ keyword args on ``_build_finding`` so
    Lizard's ``many_parameters`` smell no longer fires (threshold 10).
    Field semantics match the legacy kw-args one-for-one — see the
    docstring of ``_build_finding`` for the redaction + corroborator
    contract this dataclass feeds.

    Optional fields default to ``None`` so most call sites can omit
    them; ``end_line`` defaults to ``None`` and the helper falls back
    to ``line`` (preserving the prior ``end_line=line if None`` shape).
    """

    finding_id: str
    file: str
    line: int
    category: str
    category_group: CategoryGroup
    severity: str
    primary_message: str
    rule_id: str
    original_message: str
    rule_url: str | None = None
    context_snippet: str = ""
    end_line: int | None = None
    column: int | None = None
    fix_hint: str | None = None
    cwe: str | None = None


class BaseNormalizer(ABC):
    """Base class for all per-provider normalizers.

    Subclasses implement `parse(artifact, repo_root)` which returns an iterable
    of Finding objects. The base class's `run()` method is `@final` -- it wraps
    parse() in try/except, applies redaction + path validation to every yielded
    Finding, and packages the result into a NormalizerResult.
    """
    provider: str = "UNKNOWN"

    @abstractmethod
    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        """Parse a provider artifact and yield Finding objects.

        Subclasses should use `self._build_finding(...)` to construct Findings.
        """

    @final
    def run(self, *, artifact: Any, repo_root: Path) -> NormalizerResult:
        """Run the normalizer; catch crashes and validate paths.

        This method is @final -- subclasses MUST NOT override it.
        """
        findings_out: List[Finding] = []
        errors: List[Dict[str, str]] = []
        drops: List[Dict[str, str]] = []
        try:
            raw = list(self.parse(artifact, repo_root))
        except Exception as exc:
            errors.append({
                "provider": self.provider,
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
                "traceback_digest": traceback.format_exc()[:1024],
            })
            return NormalizerResult(findings=(), normalizer_errors=tuple(errors), security_drops=())
        for finding in raw:
            # 1. Path validation (defense-in-depth against poisoned provider output)
            try:
                validate_finding_file(finding.file, repo_root)
            except PathEscapedRootError as exc:
                drops.append({
                    "provider": self.provider,
                    "file": finding.file,
                    "reason": str(exc),
                })
                continue
            # 2. Redaction (belt-and-suspenders -- already applied by _build_finding,
            # but applied again at finalize time to catch subclass bypass attempts)
            redacted = self._redact_finding(finding)
            findings_out.append(redacted)
        return NormalizerResult(
            findings=tuple(findings_out),
            normalizer_errors=tuple(errors),
            security_drops=tuple(drops),
        )

    def _build_finding(self, fields: FindingFields) -> Finding:
        """Construct a Finding from per-finding inputs (with redaction applied).

        Subclasses should construct a ``FindingFields`` and pass it in.
        Redaction is applied to ``original_message``, ``primary_message``,
        and ``context_snippet`` automatically; ``end_line`` defaults to
        ``line`` when not set explicitly.
        """
        corroborator = Corroborator.from_provider(
            provider=self.provider,
            rule_id=fields.rule_id,
            rule_url=fields.rule_url,
            original_message=redact_secrets(fields.original_message),
        )
        return Finding(
            schema_version=SCHEMA_VERSION,
            finding_id=fields.finding_id,
            file=fields.file,
            line=fields.line,
            end_line=fields.end_line if fields.end_line is not None else fields.line,
            column=fields.column,
            category=fields.category,
            category_group=fields.category_group,
            severity=fields.severity,
            corroboration="single",
            primary_message=redact_secrets(fields.primary_message),
            corroborators=(corroborator,),
            fix_hint=fields.fix_hint,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet=redact_secrets(fields.context_snippet),
            source_file_hash="",
            cwe=fields.cwe,
            autofixable=False,
            tags=(),
        )

    @staticmethod
    def _redact_finding(finding: Finding) -> Finding:
        """Re-apply redaction to every string field of a Finding (idempotent)."""
        redacted_corroborators = tuple(
            Corroborator(
                provider=c.provider,
                rule_id=c.rule_id,
                rule_url=c.rule_url,
                original_message=redact_secrets(c.original_message),
                provider_priority_rank=c.provider_priority_rank,
            )
            for c in finding.corroborators
        )
        return replace(
            finding,
            primary_message=redact_secrets(finding.primary_message),
            context_snippet=redact_secrets(finding.context_snippet),
            corroborators=redacted_corroborators,
        )
