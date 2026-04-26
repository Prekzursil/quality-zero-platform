"""Contract: ``sonar.coverage.exclusions`` mirrors the coverage source restriction.

``pyproject.toml [tool.coverage.run].source`` declares which paths are traced
by ``coverage run`` for the platform's own self-CI. Files outside that scope
do NOT appear in the coverage XML uploaded to SonarCloud, so SonarCloud sees
them as 0% covered — which would torpedo the ``new_coverage`` quality-gate
condition (≥80% on new code).

This test pins the contract: every Python/TypeScript file under ``scripts/``
that is NOT in the coverage-run source MUST appear in
``sonar.coverage.exclusions`` in ``sonar-project.properties``. When Phase 4
expands the coverage source to the full ``scripts/quality/`` tree, the
exclusion list shrinks accordingly — and this test prevents the two
configurations from drifting silently.
"""
from __future__ import absolute_import

import tomllib
import unittest
from pathlib import Path
from typing import Set

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"
_SONAR_PROPS = _REPO_ROOT / "sonar-project.properties"


def _load_sonar_property(name: str) -> str:
    """Return the raw value of ``name`` from ``sonar-project.properties``.

    Properties files use ``key=value`` pairs; trailing/leading whitespace and
    comment lines are ignored. Returns the empty string if the property is
    absent.
    """
    text = _SONAR_PROPS.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


def _coverage_run_source_modules() -> Set[str]:
    """Return the set of file/path tokens declared in ``tool.coverage.run.source``."""
    payload = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    source = payload["tool"]["coverage"]["run"]["source"]
    # Source entries can be either filesystem paths ("scripts/quality/rollup_v2")
    # or dotted module names ("scripts.quality.fleet_inventory"); normalise to
    # filesystem-style tokens so downstream comparisons work uniformly.
    return {entry.replace(".", "/") for entry in source}


def _is_in_coverage_source(rel_path: str, source_tokens: Set[str]) -> bool:
    """Return True if ``rel_path`` is inside any ``tool.coverage.run.source`` entry."""
    posix = rel_path.replace("\\", "/")
    candidate = posix
    if candidate.endswith(".py"):
        candidate_no_ext = candidate[:-3]
    else:
        candidate_no_ext = candidate
    for token in source_tokens:
        if posix == token:
            return True
        if candidate_no_ext == token:
            return True
        if posix.startswith(token + "/"):
            return True
    return False


class SonarCoverageExclusionsContract(unittest.TestCase):
    """Pin the exclusion list to the coverage-source declaration."""

    def test_property_present(self) -> None:
        """``sonar.coverage.exclusions`` MUST be declared explicitly."""
        value = _load_sonar_property("sonar.coverage.exclusions")
        self.assertTrue(
            value,
            "sonar.coverage.exclusions must be set in sonar-project.properties — "
            "without it, SonarCloud reads 0% coverage for every untraced file "
            "and the new_coverage quality gate stays red.",
        )

    def test_exclusions_match_coverage_source_complement(self) -> None:
        """Exclusion list MUST equal the complement of coverage-run source.

        For each ``*.py`` and ``*.ts`` file under ``scripts/``: if it's NOT in
        ``tool.coverage.run.source``, it MUST be in ``sonar.coverage.exclusions``.
        And: every entry in ``sonar.coverage.exclusions`` MUST point at a real
        file (no stale/dangling exclusions).
        """
        source_tokens = _coverage_run_source_modules()

        # Enumerate every script file via filesystem walk (NOT git ls-files,
        # which would require a subprocess shell-out). Match what's tracked
        # by sonar.sources=scripts.
        script_root = _REPO_ROOT / "scripts"
        script_files = sorted(
            str(p.relative_to(_REPO_ROOT)).replace("\\", "/")
            for p in script_root.rglob("*")
            if p.is_file() and p.suffix in {".py", ".ts"}
        )

        # Build the expected exclusion set (everything outside the source).
        expected_excluded = {
            f for f in script_files
            if not _is_in_coverage_source(f, source_tokens)
        }

        # Parse the actual exclusion list.
        raw_value = _load_sonar_property("sonar.coverage.exclusions")
        actual_excluded = {p.strip() for p in raw_value.split(",") if p.strip()}

        missing = expected_excluded - actual_excluded
        extra = actual_excluded - expected_excluded

        self.assertFalse(
            missing,
            f"Files outside tool.coverage.run.source but missing from "
            f"sonar.coverage.exclusions:\n  "
            + "\n  ".join(sorted(missing)),
        )
        self.assertFalse(
            extra,
            f"sonar.coverage.exclusions has stale entries (file does not exist "
            f"or is now in tool.coverage.run.source):\n  "
            + "\n  ".join(sorted(extra)),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
