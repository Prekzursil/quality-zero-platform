"""Patch generator dispatcher (per design §A.1.4)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Dict

from scripts.quality.rollup_v2.patches import (
    assert_in_production,
    bad_line_ending,
    bare_raise,
    broad_except,
    command_injection,
    coverage_gap,
    cyclic_import,
    dead_code,
    duplicate_code,
    hardcoded_secret,
    indent_mismatch,
    insecure_random,
    line_too_long,
    missing_docstring,
    mutable_default,
    naming_convention,
    open_redirect,
    print_in_production,
    quote_style,
    shadowed_builtin,
    spacing_convention,
    tab_vs_space,
    todo_comment,
    too_complex,
    too_long,
    trailing_newline,
    trailing_whitespace,
    unused_import,
    unused_variable,
    weak_crypto,
    wrong_import_order,
)
from scripts.quality.rollup_v2.path_safety import PathEscapedRootError, validate_finding_file
from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

# Populated by Phase 9 tasks. 31 entries: 30 from §5.1 + 1 coverage-gap (Task 9.31).
GENERATORS: Dict[str, object] = {
    "assert-in-production": assert_in_production,
    "bad-line-ending": bad_line_ending,
    "bare-raise": bare_raise,
    "broad-except": broad_except,
    "command-injection": command_injection,
    "coverage-gap": coverage_gap,
    "cyclic-import": cyclic_import,
    "dead-code": dead_code,
    "duplicate-code": duplicate_code,
    "hardcoded-secret": hardcoded_secret,
    "indent-mismatch": indent_mismatch,
    "insecure-random": insecure_random,
    "line-too-long": line_too_long,
    "missing-docstring": missing_docstring,
    "mutable-default": mutable_default,
    "naming-convention": naming_convention,
    "open-redirect": open_redirect,
    "print-in-production": print_in_production,
    "quote-style": quote_style,
    "shadowed-builtin": shadowed_builtin,
    "spacing-convention": spacing_convention,
    "tab-vs-space": tab_vs_space,
    "todo-comment": todo_comment,
    "too-complex": too_complex,
    "too-long": too_long,
    "trailing-newline": trailing_newline,
    "trailing-whitespace": trailing_whitespace,
    "unused-import": unused_import,
    "unused-variable": unused_variable,
    "weak-crypto": weak_crypto,
    "wrong-import-order": wrong_import_order,
}


def dispatch(
    finding: Finding,
    *,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Route a finding to its registered tier-1 generator (if any).

    Defense-in-depth: validates path before dispatching (second layer after normalizer).
    Returns PatchDeclined on path escape, None if no generator registered.
    """
    # Defense-in-depth path validation — second layer after normalizer
    try:
        validate_finding_file(finding.file, repo_root)
    except PathEscapedRootError:
        return PatchDeclined(
            reason_code="path-traversal-rejected",
            reason_text=f"finding.file escaped repo root: {finding.file!r}",
            suggested_tier="skip",
        )
    gen = GENERATORS.get(finding.category)
    if gen is None:
        return None
    # Pass positionally so the dispatch works regardless of whether the
    # generator spells the third parameter ``repo_root`` or ``_repo_root``.
    return gen.generate(finding, source_file_content, repo_root)  # type: ignore[union-attr]
