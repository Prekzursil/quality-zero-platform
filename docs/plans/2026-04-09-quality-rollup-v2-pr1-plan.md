# Quality Rollup v2 — PR 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the useless 3-column `build_quality_rollup.py` table with a canonical SARIF-inspired finding schema, 9 per-provider normalizers, hybrid dedup, multi-view GFM-safe rendered markdown, ~30 deterministic patch generators, LLM fallback scaffold, and platform dogfood wiring — all TDD, all covered at 100% (within the `scripts/quality/rollup_v2/` coverage scope).

**Architecture:** Python 3.12, `@dataclass(frozen=True, slots=True)` types, explicit-dict dispatcher pattern (no decorators/dynamic imports), YAML-backed taxonomy, unittest-based TDD with class-definition-time dynamic test method generation for the shared patch-generator harness.

**Tech Stack:** Python 3.12 stdlib + PyYAML (already in `requirements-dev.txt`) + `coverage.py` and `jsonschema` (added to `requirements-dev.txt` in Task 0.4). Tests use `unittest.TestCase` + `unittest.mock.patch`. 100% line+branch coverage enforced on `scripts/quality/rollup_v2/` via `python -m coverage run … && python -m coverage report --fail-under=100` (Task 18.1). The existing `scripts/quality/assert_coverage_100.py` is a separate LCOV/XML percentage asserter used by downstream repos and is NOT invoked by PR 1's coverage gate.

**Source of truth:** `docs/plans/2026-04-09-quality-rollup-v2-design.md` (§§1-13 + Addendum A §A.1-A.12 + Addendum B §B.1-B.4). This plan does NOT re-state the design; it translates it into bite-sized tasks. When the plan says "per design §X.Y", the reader must consult the design doc.

**Scope boundary:** This plan covers **PR 1 ONLY** — "Rollup rewrite + patch generators". PR 2 (self-governance + Sonar + Codacy config) and PR 3 (new provider lanes + momentstudio visual) have separate plans drafted after PR 1 merges.

---

## Pre-flight (run once before Phase 0)

- [ ] **P.1: Verify branch and clean tree**

Run: `git status && git rev-parse --abbrev-ref HEAD`
Expected: on `feat/quality-rollup-v2`, working tree clean (except possibly this plan file being added), HEAD at `9b148e9` (addendum-b commit) or later.

- [ ] **P.2: Verify Python + unittest + coverage are available**

Run:
```bash
python -c "import sys; print(sys.version)"
python -m coverage --version
python -c "import yaml; print(yaml.__version__)"
```
Expected: Python 3.12+, coverage.py 7.x+, PyYAML 6.x+. If any fails, `pip install -r requirements-dev.txt` and re-run.

- [ ] **P.3: Read the design doc sections referenced below**

The implementer MUST read these design sections before starting each phase:
| Phase | Design sections |
|---|---|
| 0 (Scaffold) | §10 PR 1, A.5, A.12 |
| 1 (Types) | §3.1, §3.3, A.3.2, A.4.1, A.4.2, A.4.3, B.3.4, B.3.11 |
| 2 (Redaction) | A.2.2, B.1, B.1.1, B.1.2, B.1.3 |
| 3 (Path validation) | A.2.3, B.2, B.2.1, B.2.2, B.2.3 |
| 4 (Taxonomy) | §3.2, A.4.4, A.4.5 |
| 5 (BaseNormalizer) | §3.3, A.6, B.1.2 |
| 6 (Normalizers) | §3 all, §4.2, A.6 |
| 7 (Dedup + merge) | §3.3, A.3.2, A.4.2 |
| 8 (Patch infra) | §5.1, A.1.3, A.1.4, A.1.5, B.3.5, B.3.11 |
| 9 (Patch generators) | §5.1 |
| 10 (LLM fallback) | §5.2, A.2.1, A.2.2, B.3.12 |
| 11 (Renderer) | §4.1, §4.2, A.1.1, A.1.2, B.3.9, B.3.15, B.3.16 |
| 12 (Writer pipeline) | §4.2, A.3.5 |
| 13 (Platform dogfood) | §10 PR 1, A.8 |
| 14 (Legacy wrapper) | A.3.4 |
| 15 (Lane key reservation) | A.5 |
| 16 (Golden fixtures) | A.9, B.3.7, B.3.16 |
| 17 (Documentation) | A.9.1, A.9.5, B.3.8, B.3.10 |
| 18 (Final integration) | §13, A.9 all |

---

## Phase 0 — Scaffolding (~3 tasks)

### Task 0.1: Create `scripts/quality/rollup_v2/` package + test directories

**Files:**
- Create: `scripts/quality/rollup_v2/__init__.py`
- Create: `scripts/quality/rollup_v2/normalizers/__init__.py`
- Create: `scripts/quality/rollup_v2/patches/__init__.py`
- Create: `scripts/quality/rollup_v2/templates/` (empty dir, `.gitkeep`)
- Create: `tests/quality/__init__.py`
- Create: `tests/quality/rollup_v2/__init__.py`
- Create: `tests/quality/rollup_v2/fixtures/.gitkeep`
- Create: `tests/quality/rollup_v2/fixtures/patches/.gitkeep`
- Create: `tests/quality/rollup_v2/fixtures/renderer/.gitkeep`
- Create: `tests/quality/rollup_v2/fixtures/normalizers/.gitkeep`
- Create: `config/taxonomy/.gitkeep`

- [ ] **Step 1: Create every `__init__.py` as a 2-line module docstring**

Each file's content:
```python
"""<package_name> — part of quality-rollup-v2 per docs/plans/2026-04-09-quality-rollup-v2-design.md."""
```
Replace `<package_name>` with an actual description for each file.

- [ ] **Step 2: Run: `python -c "from scripts.quality.rollup_v2 import __init__"` to verify the package imports**

Expected: no exception, exits 0.

- [ ] **Step 3: Commit**
```bash
git add scripts/quality/rollup_v2 tests/quality config/taxonomy
git commit -m "feat(qrv2): scaffold rollup_v2 package + test/fixture/taxonomy dirs"
```

### Task 0.2: Add scripts/quality/rollup_v2 to the package search path

**Files:**
- Verify: `setup.py`, `pyproject.toml`, or existing `sys.path` handling

- [ ] **Step 1: Confirm how existing `scripts.quality.*` modules are importable in tests**

Read: `tests/test_quality_rollup.py` (first 20 lines) to see how it imports `scripts.quality.build_quality_rollup`.
Expected: `sys.path.insert(0, str(Path(__file__).resolve().parents[2]))` pattern (per `scripts/quality/assert_coverage_100.py:13-14`).

- [ ] **Step 2: No action required if existing pattern works for rollup_v2 subpackage; confirm by running a sanity import**

Run: `python -c "from scripts.quality.rollup_v2 import normalizers, patches; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 3: Commit (only if any sys.path tweak was needed — otherwise skip)**

### Task 0.3: Document the plan as the source of truth for PR 1 work units

**Files:**
- Modify: `.beads/context/project-context.md` (append PR 1 plan reference)

- [ ] **Step 1: Append a pointer to the plan file**

Append to `.beads/context/project-context.md`:
```markdown

## PR 1 work plan

- Plan file: `docs/plans/2026-04-09-quality-rollup-v2-pr1-plan.md`
- Scope: PR 1 only (rollup rewrite + patch generators)
- Coverage scope: `scripts/quality/rollup_v2/` + `config/taxonomy/` (via YAML loader tests)
```

- [ ] **Step 2: Commit**
```bash
git add .beads/context/project-context.md
git commit -m "chore(beads): point project-context.md at PR 1 plan file"
```

### Task 0.4: Add `coverage` and `jsonschema` to `requirements-dev.txt`

The PR 1 coverage gate (Task 18.1) uses `python -m coverage run` + `coverage report --fail-under=100`. Task 17.3 validates `canonical.json` against a JSON Schema. Neither `coverage` nor `jsonschema` is currently in `requirements-dev.txt` (verified: file contains only `PyYAML>=6.0,<7.0` and `types-PyYAML`). They must be added before Task 18.1 can execute.

**Files:**
- Modify: `requirements-dev.txt`

- [ ] **Step 1: Append the two new dependencies**

Append to `requirements-dev.txt`:
```text
coverage[toml]>=7.4,<8.0
jsonschema>=4.20,<5.0
```

- [ ] **Step 2: Install + smoke-test**

Run:
```bash
pip install -r requirements-dev.txt
python -c "import coverage; print('coverage', coverage.__version__)"
python -c "import jsonschema; print('jsonschema', jsonschema.__version__)"
```
Expected: both imports succeed.

- [ ] **Step 3: Commit**
```bash
git add requirements-dev.txt
git commit -m "chore(deps): add coverage + jsonschema for QRv2 PR 1 coverage + schema validation gates"
```

---

## Phase 1 — Core Types (~5 tasks)

### Task 1.1: `SEVERITY_ORDER` constant and `max_severity()` helper

**Files:**
- Create: `scripts/quality/rollup_v2/severity.py`
- Create: `tests/quality/rollup_v2/test_severity.py`

- [ ] **Step 1: Write failing test**

`tests/quality/rollup_v2/test_severity.py`:
```python
"""Tests for severity ordering (per design §A.4.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.severity import SEVERITY_ORDER, max_severity


class SeverityOrderTests(unittest.TestCase):
    def test_severity_order_tuple(self):
        self.assertEqual(
            SEVERITY_ORDER,
            ("critical", "high", "medium", "low", "info"),
        )

    def test_max_severity_picks_highest(self):
        self.assertEqual(max_severity(["low", "critical", "medium"]), "critical")

    def test_max_severity_single(self):
        self.assertEqual(max_severity(["medium"]), "medium")

    def test_max_severity_all_same(self):
        self.assertEqual(max_severity(["high", "high", "high"]), "high")

    def test_max_severity_empty_raises(self):
        with self.assertRaises(ValueError):
            max_severity([])

    def test_max_severity_unknown_severity_raises(self):
        with self.assertRaises(ValueError):
            max_severity(["bogus"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `python -m unittest tests.quality.rollup_v2.test_severity -v`
Expected: `ModuleNotFoundError: No module named 'scripts.quality.rollup_v2.severity'`.

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/severity.py`:
```python
"""Severity ordering for canonical findings (per design §A.4.2)."""
from __future__ import absolute_import

from typing import Final, Sequence

SEVERITY_ORDER: Final[tuple[str, ...]] = ("critical", "high", "medium", "low", "info")


def max_severity(severities: Sequence[str]) -> str:
    """Return the HIGHEST severity from the input sequence.

    Higher severity = lower index in SEVERITY_ORDER.
    Raises ValueError on empty input or unknown severities.
    """
    if not severities:
        raise ValueError("max_severity requires at least one severity")
    for severity in severities:
        if severity not in SEVERITY_ORDER:
            raise ValueError(f"Unknown severity: {severity!r}")
    return min(severities, key=SEVERITY_ORDER.index)
```

- [ ] **Step 4: Run test to confirm it passes**

Run: `python -m unittest tests.quality.rollup_v2.test_severity -v`
Expected: 6 tests, all OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/severity.py tests/quality/rollup_v2/test_severity.py
git commit -m "feat(qrv2): SEVERITY_ORDER constant + max_severity() helper (§A.4.2)"
```

### Task 1.2: `PROVIDER_PRIORITY_RANK` dict + `priority_rank_for()` helper

**Files:**
- Create: `scripts/quality/rollup_v2/providers.py`
- Create: `tests/quality/rollup_v2/test_providers.py`

- [ ] **Step 1: Write failing test**

`tests/quality/rollup_v2/test_providers.py`:
```python
"""Tests for provider priority ranking (per design §A.4.3)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.providers import (
    PROVIDER_PRIORITY_RANK,
    UNKNOWN_PROVIDER_RANK,
    priority_rank_for,
)


class ProviderPriorityTests(unittest.TestCase):
    def test_codeql_has_highest_priority(self):
        self.assertEqual(PROVIDER_PRIORITY_RANK["CodeQL"], 0)

    def test_sonar_above_codacy(self):
        self.assertLess(
            PROVIDER_PRIORITY_RANK["SonarCloud"],
            PROVIDER_PRIORITY_RANK["Codacy"],
        )

    def test_ordering_matches_design_a_4_3(self):
        expected_order = (
            "CodeQL",
            "SonarCloud",
            "Codacy",
            "DeepSource",
            "Semgrep",
            "QLTY",
            "DeepScan",
        )
        for index, name in enumerate(expected_order):
            self.assertEqual(PROVIDER_PRIORITY_RANK[name], index)

    def test_non_analyzer_providers_rank_high(self):
        for non_analyzer in ("Sentry", "Chromatic", "Applitools"):
            self.assertEqual(PROVIDER_PRIORITY_RANK[non_analyzer], UNKNOWN_PROVIDER_RANK)

    def test_priority_rank_for_known(self):
        self.assertEqual(priority_rank_for("CodeQL"), 0)

    def test_priority_rank_for_unknown_returns_sentinel(self):
        self.assertEqual(priority_rank_for("MysteryVendor"), UNKNOWN_PROVIDER_RANK)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to confirm it fails**
Run: `python -m unittest tests.quality.rollup_v2.test_providers -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/providers.py`:
```python
"""Provider priority ranking for canonical-finding merges (per design §A.4.3)."""
from __future__ import absolute_import

from typing import Final, Mapping

UNKNOWN_PROVIDER_RANK: Final[int] = 99

PROVIDER_PRIORITY_RANK: Final[Mapping[str, int]] = {
    "CodeQL": 0,
    "SonarCloud": 1,
    "Codacy": 2,
    "DeepSource": 3,
    "Semgrep": 4,
    "QLTY": 5,
    "DeepScan": 6,
    "Sentry": UNKNOWN_PROVIDER_RANK,
    "Chromatic": UNKNOWN_PROVIDER_RANK,
    "Applitools": UNKNOWN_PROVIDER_RANK,
}


def priority_rank_for(provider: str) -> int:
    """Return the priority rank for a provider; UNKNOWN_PROVIDER_RANK for unknown."""
    return PROVIDER_PRIORITY_RANK.get(provider, UNKNOWN_PROVIDER_RANK)
```

- [ ] **Step 4: Run test to confirm it passes**
Run: `python -m unittest tests.quality.rollup_v2.test_providers -v`
Expected: 6 tests, all OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/providers.py tests/quality/rollup_v2/test_providers.py
git commit -m "feat(qrv2): PROVIDER_PRIORITY_RANK + priority_rank_for() (§A.4.3)"
```

### Task 1.3: `Corroborator` frozen dataclass + `from_provider()` factory

**Files:**
- Create: `scripts/quality/rollup_v2/schema/__init__.py` (empty module)
- Create: `scripts/quality/rollup_v2/schema/corroborator.py`
- Create: `tests/quality/rollup_v2/test_corroborator.py`

- [ ] **Step 1: Write failing test**

`tests/quality/rollup_v2/test_corroborator.py`:
```python
"""Tests for Corroborator dataclass (per design §A.3.2 + §B.3.4)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.providers import UNKNOWN_PROVIDER_RANK
from scripts.quality.rollup_v2.schema.corroborator import Corroborator


class CorroboratorTests(unittest.TestCase):
    def test_from_provider_populates_rank(self):
        c = Corroborator.from_provider(
            provider="SonarCloud",
            rule_id="python:S1166",
            rule_url="https://sonarcloud.io/S1166",
            original_message="Catch a more specific exception",
        )
        self.assertEqual(c.provider, "SonarCloud")
        self.assertEqual(c.rule_id, "python:S1166")
        self.assertEqual(c.provider_priority_rank, 1)

    def test_from_provider_unknown_provider_uses_sentinel_rank(self):
        c = Corroborator.from_provider(
            provider="MysteryVendor",
            rule_id="X-001",
            rule_url=None,
            original_message="msg",
        )
        self.assertEqual(c.provider_priority_rank, UNKNOWN_PROVIDER_RANK)

    def test_frozen(self):
        c = Corroborator.from_provider("SonarCloud", "S1", None, "m")
        with self.assertRaises(Exception):
            c.provider = "Other"  # type: ignore[misc]

    def test_direct_construction_with_unmapped_rank_raises(self):
        with self.assertRaises(AssertionError):
            Corroborator(
                provider="SonarCloud",
                rule_id="S1",
                rule_url=None,
                original_message="m",
                provider_priority_rank=-1,
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to confirm it fails**
Run: `python -m unittest tests.quality.rollup_v2.test_corroborator -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/schema/corroborator.py`:
```python
"""Corroborator dataclass for canonical findings (per design §A.3.2 + §B.3.4)."""
from __future__ import absolute_import

from dataclasses import dataclass

from scripts.quality.rollup_v2.providers import priority_rank_for


@dataclass(frozen=True, slots=True)
class Corroborator:
    """A per-provider record attached to a canonical Finding.

    Always construct via `Corroborator.from_provider(...)` — direct construction
    with `provider_priority_rank == -1` (the "not looked up" sentinel) raises.
    """
    provider: str
    rule_id: str
    rule_url: str | None
    original_message: str
    provider_priority_rank: int

    def __post_init__(self) -> None:
        if self.provider_priority_rank == -1:
            raise AssertionError(
                "Corroborator.provider_priority_rank was not set. "
                "Use Corroborator.from_provider() instead of direct construction."
            )

    @classmethod
    def from_provider(
        cls,
        provider: str,
        rule_id: str,
        rule_url: str | None,
        original_message: str,
    ) -> "Corroborator":
        """Preferred constructor: looks up the priority rank from PROVIDER_PRIORITY_RANK."""
        return cls(
            provider=provider,
            rule_id=rule_id,
            rule_url=rule_url,
            original_message=original_message,
            provider_priority_rank=priority_rank_for(provider),
        )
```

Also create: `scripts/quality/rollup_v2/schema/__init__.py`:
```python
"""Canonical types for rollup_v2 (per design §3.1 + §A.3.2 + §A.4.1)."""
```

- [ ] **Step 4: Run test to confirm it passes**
Run: `python -m unittest tests.quality.rollup_v2.test_corroborator -v`
Expected: 4 tests, all OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/schema tests/quality/rollup_v2/test_corroborator.py
git commit -m "feat(qrv2): Corroborator frozen dataclass + from_provider() factory (§A.3.2 §B.3.4)"
```

### Task 1.4: `Finding` frozen dataclass with all A.4.1 fields

**Files:**
- Create: `scripts/quality/rollup_v2/schema/finding.py`
- Create: `tests/quality/rollup_v2/test_finding.py`

- [ ] **Step 1: Write failing test**

`tests/quality/rollup_v2/test_finding.py`:
```python
"""Tests for Finding dataclass (per design §3.1 + §A.3.2 + §A.4.1)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    SCHEMA_VERSION,
    CATEGORY_GROUP_SECURITY,
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_STYLE,
    Finding,
)


def _make_corroborator():
    return Corroborator.from_provider("Codacy", "Pylint_W0703", None, "broad except")


class FindingTests(unittest.TestCase):
    def test_schema_version_is_qzp_finding_1(self):
        self.assertEqual(SCHEMA_VERSION, "qzp-finding/1")

    def test_category_group_constants(self):
        self.assertEqual(CATEGORY_GROUP_SECURITY, "security")
        self.assertEqual(CATEGORY_GROUP_QUALITY, "quality")
        self.assertEqual(CATEGORY_GROUP_STYLE, "style")

    def test_all_required_fields(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="qzp-0001",
            file="scripts/quality/coverage_parsers.py",
            line=42,
            end_line=42,
            column=5,
            category="broad-except",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="medium",
            corroboration="single",
            primary_message="Catch a more specific exception",
            corroborators=(_make_corroborator(),),
            fix_hint="Narrow the exception type",
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="    try:\n        parse_coverage(path)\n    except Exception as e:\n        log.warning(...)",
            source_file_hash="sha256:deadbeef",
            cwe=None,
            autofixable=False,
            tags=(),
        )
        self.assertEqual(f.schema_version, SCHEMA_VERSION)
        self.assertEqual(f.category_group, "quality")
        self.assertEqual(len(f.corroborators), 1)

    def test_frozen(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="qzp-0001",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="broad-except",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="medium",
            corroboration="single",
            primary_message="m",
            corroborators=(),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="",
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=False,
            tags=(),
        )
        with self.assertRaises(Exception):
            f.line = 2  # type: ignore[misc]

    def test_invalid_category_group_raises(self):
        with self.assertRaises(AssertionError):
            Finding(
                schema_version=SCHEMA_VERSION,
                finding_id="x",
                file="a.py",
                line=1,
                end_line=1,
                column=None,
                category="c",
                category_group="invalid",   # <- not security/quality/style
                severity="low",
                corroboration="single",
                primary_message="m",
                corroborators=(),
                fix_hint=None,
                patch=None,
                patch_source="none",
                patch_confidence=None,
                context_snippet="",
                source_file_hash="",
                cwe=None,
                autofixable=False,
                tags=(),
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to confirm it fails**
Run: `python -m unittest tests.quality.rollup_v2.test_finding -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/schema/finding.py`:
```python
"""Canonical Finding dataclass (per design §3.1 + §A.3.2 + §A.4.1)."""
from __future__ import absolute_import

from dataclasses import dataclass
from typing import Final, Literal

from scripts.quality.rollup_v2.schema.corroborator import Corroborator

SCHEMA_VERSION: Final[str] = "qzp-finding/1"

CATEGORY_GROUP_SECURITY: Final[str] = "security"
CATEGORY_GROUP_QUALITY: Final[str] = "quality"
CATEGORY_GROUP_STYLE: Final[str] = "style"

_VALID_CATEGORY_GROUPS: Final[frozenset[str]] = frozenset(
    {CATEGORY_GROUP_SECURITY, CATEGORY_GROUP_QUALITY, CATEGORY_GROUP_STYLE}
)

_VALID_PATCH_SOURCES: Final[frozenset[str]] = frozenset({"deterministic", "llm", "none"})
_VALID_CORROBORATION: Final[frozenset[str]] = frozenset({"multi", "single"})


CategoryGroup = Literal["security", "quality", "style"]
PatchSource = Literal["deterministic", "llm", "none"]
Corroboration = Literal["multi", "single"]


@dataclass(frozen=True, slots=True)
class Finding:
    """Canonical finding produced by any provider normalizer.

    All string fields that may contain user/provider content have been passed
    through `redact_secrets()` by the normalizer before construction (see §B.1).
    """
    schema_version: str
    finding_id: str
    file: str
    line: int
    end_line: int
    column: int | None
    category: str
    category_group: CategoryGroup
    severity: str
    corroboration: Corroboration
    primary_message: str
    corroborators: tuple[Corroborator, ...]
    fix_hint: str | None
    patch: str | None
    patch_source: PatchSource
    patch_confidence: str | None
    context_snippet: str
    source_file_hash: str
    cwe: str | None
    autofixable: bool
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.category_group not in _VALID_CATEGORY_GROUPS:
            raise AssertionError(
                f"category_group must be one of {_VALID_CATEGORY_GROUPS}, got {self.category_group!r}"
            )
        if self.patch_source not in _VALID_PATCH_SOURCES:
            raise AssertionError(
                f"patch_source must be one of {_VALID_PATCH_SOURCES}, got {self.patch_source!r}"
            )
        if self.corroboration not in _VALID_CORROBORATION:
            raise AssertionError(
                f"corroboration must be one of {_VALID_CORROBORATION}, got {self.corroboration!r}"
            )
        if self.schema_version != SCHEMA_VERSION:
            raise AssertionError(
                f"schema_version must be {SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
```

- [ ] **Step 4: Run test to confirm it passes**
Run: `python -m unittest tests.quality.rollup_v2.test_finding -v`
Expected: 5 tests, all OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/schema/finding.py tests/quality/rollup_v2/test_finding.py
git commit -m "feat(qrv2): Finding frozen dataclass with all §A.4.1 fields"
```

### Task 1.5: Add `patch_error` optional side-channel field to Finding (clarify A.6)

**Files:**
- Modify: `scripts/quality/rollup_v2/schema/finding.py`
- Modify: `tests/quality/rollup_v2/test_finding.py`

- [ ] **Step 1: Write failing test**

Add to `tests/quality/rollup_v2/test_finding.py` at the end of `FindingTests`:
```python
    def test_patch_error_defaults_to_none_and_is_optional(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="x",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="c",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="low",
            corroboration="single",
            primary_message="m",
            corroborators=(),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="",
            source_file_hash="",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error=None,
        )
        self.assertIsNone(f.patch_error)

    def test_patch_error_accepts_non_empty_string(self):
        f = Finding(
            schema_version=SCHEMA_VERSION,
            finding_id="x",
            file="a.py",
            line=1,
            end_line=1,
            column=None,
            category="c",
            category_group=CATEGORY_GROUP_QUALITY,
            severity="low",
            corroboration="single",
            primary_message="m",
            corroborators=(),
            fix_hint=None,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet="",
            source_file_hash="",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error="ValueError: unable to parse snippet",
        )
        self.assertEqual(f.patch_error, "ValueError: unable to parse snippet")
```

- [ ] **Step 2: Run to confirm fails** — `TypeError: __init__() got an unexpected keyword argument 'patch_error'`.

- [ ] **Step 3: Add field to Finding**

Add to `scripts/quality/rollup_v2/schema/finding.py` Finding dataclass (last field before `__post_init__`):
```python
    patch_error: str | None = None   # per A.6 — set when a patch generator raised; none otherwise
```

Note: `patch_error` MUST be the last field in the dataclass because it has a default and later fields without defaults are disallowed. Verify by running the existing tests — if `test_all_required_fields` fails because the kwarg order changed, that's OK since kwargs are named, not positional.

- [ ] **Step 4: Run tests to confirm pass**
Run: `python -m unittest tests.quality.rollup_v2.test_finding -v`
Expected: 7 tests, all OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/schema/finding.py tests/quality/rollup_v2/test_finding.py
git commit -m "feat(qrv2): add Finding.patch_error side-channel field for §A.6 error boundaries"
```

---

## Phase 2 — Redaction module (§B.1, ~3 tasks)

### Task 2.1: `redact_secrets()` core implementation + idempotency tests

**Files:**
- Create: `scripts/quality/rollup_v2/redaction.py`
- Create: `tests/quality/rollup_v2/test_redaction.py`

- [ ] **Step 1: Write failing test**

`tests/quality/rollup_v2/test_redaction.py`:
```python
"""Tests for secret redaction (per design §B.1)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.redaction import REDACTED, redact_secrets


def _build_test_token_shape() -> str:
    """Build a test string that matches the JWT regex in redaction.py at runtime.

    The returned value has three base64url segments joined by dots and its first
    segment starts with the two-character prefix required by the JWT regex, but
    NO part of this helper or the plan file contains a literal token substring.
    This keeps the plan file and test source bytes clean for secret scanners.
    """
    import base64
    import json
    import secrets as _s

    def _b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    # Dict inputs are ordinary data, not token fragments.
    header_obj = {"alg": "H" + "S256", "typ": "J" + "WT"}
    payload_obj = {"sub": "test", "iat": 0}
    header_seg = _b64url(json.dumps(header_obj, separators=(",", ":")).encode("utf-8"))
    payload_seg = _b64url(json.dumps(payload_obj, separators=(",", ":")).encode("utf-8"))
    sig_seg = _b64url(_s.token_bytes(24))
    return f"{header_seg}.{payload_seg}.{sig_seg}"


class RedactSecretsTests(unittest.TestCase):
    # --- positive tests: each pattern must be redacted
    def test_named_assignment_api_key(self):
        out = redact_secrets('FOO_API_KEY = "EXAMPLE_KEY_1"')
        self.assertNotIn("EXAMPLE_KEY_1", out)
        self.assertIn(REDACTED, out)

    def test_named_assignment_lowercase(self):
        out = redact_secrets(f'api_key = "{"verylong" + "secretvalue"}"')
        self.assertNotIn("verylongsecretvalue", out)
        self.assertIn(REDACTED, out)

    def test_named_assignment_client_secret(self):
        out = redact_secrets(f'client_secret: "{"longsecret" + "valueabcdef"}"')
        self.assertIn(REDACTED, out)

    def test_named_assignment_private_key(self):
        out = redact_secrets('PRIVATE_KEY = "longprivatekeyvaluexyz"')
        self.assertIn(REDACTED, out)

    def test_bare_jwt(self):
        # IMPLEMENTER: build the test token at runtime from a helper — do NOT
        # paste a literal token string into this plan or the test file, so the
        # source tree stays clean for any pre-commit secret scanner. Use the
        # `_build_test_token_shape()` helper below.
        token = _build_test_token_shape()
        out = redact_secrets(f"bearer: {token}")
        self.assertNotIn(token, out)
        self.assertIn(REDACTED, out)
        self.assertEqual(out, redact_secrets(out))  # idempotent

    def test_pem_block(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDabc123\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out = redact_secrets(f"key: {pem}")
        self.assertNotIn("MIIEvQIBADANBgkqhkiG9w0", out)
        self.assertIn(REDACTED, out)

    def test_openai_sk_key(self):
        key = "sk-" + "a" * 40
        out = redact_secrets(f"openai={key}")
        self.assertNotIn(key, out)
        self.assertIn(REDACTED, out)

    def test_github_pat(self):
        pat = "ghp_" + "a" * 36
        out = redact_secrets(f"token {pat}")
        self.assertNotIn(pat, out)
        self.assertIn(REDACTED, out)

    def test_aws_access_key(self):
        key = "AWS_KEY_EXAMPLE_REDACTED"
        out = redact_secrets(f"aws_key: {key}")
        self.assertNotIn(key, out)
        self.assertIn(REDACTED, out)

    def test_authorization_bearer(self):
        out = redact_secrets("Authorization: Bearer abcdef1234567890abcdef1234567890")
        self.assertNotIn("abcdef1234567890abcdef1234567890", out)
        self.assertIn(REDACTED, out)

    # --- negative tests: look-alikes must NOT be redacted
    def test_short_value_not_redacted(self):
        out = redact_secrets('FOO_KEY = "short"')
        self.assertEqual(out, 'FOO_KEY = "short"')

    def test_normal_python_code_not_redacted(self):
        code = "def hello():\n    print('Hello, world!')"
        self.assertEqual(redact_secrets(code), code)

    # --- idempotency
    def test_idempotent_on_redacted_output(self):
        original = 'FOO_API_KEY = "EXAMPLE_KEY_2"'
        once = redact_secrets(original)
        twice = redact_secrets(once)
        self.assertEqual(once, twice)

    def test_empty_string_passthrough(self):
        self.assertEqual(redact_secrets(""), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to fail**
Run: `python -m unittest tests.quality.rollup_v2.test_redaction -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation (copy from design §B.1.1 verbatim)**

`scripts/quality/rollup_v2/redaction.py`: copy the module content from design §B.1.1 exactly. Then split `_REDACTION_PATTERNS` into `_NAMED_ASSIGNMENT_PATTERNS` (just the first pattern) and `_FULL_MATCH_PATTERNS` (the remaining 6 patterns) per Designer suggestion from Round 3 (remove fragile index-0 special case).

Final shape:
```python
"""Secret redaction for quality-rollup-v2 canonical findings (per design §B.1)."""
from __future__ import absolute_import

import re
from typing import Final

_NAMED_ASSIGNMENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"([A-Za-z_][A-Za-z0-9_]*(?:_?(?:KEY|TOKEN|SECRET|PASSWORD|PASS|PWD|DSN|API[_-]?KEY|"
        r"ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|AUTH))"
        r"\s*[=:]\s*)"
        r"""(["']?)([^"'\s,;]{8,})\2""",
        re.IGNORECASE,
    ),
)

_FULL_MATCH_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Bare JWTs
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
    # PEM blocks
    re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?"
        r"(?:PRIVATE\s+KEY|CERTIFICATE|ENCRYPTED\s+PRIVATE\s+KEY)-----"
        r"[\s\S]{16,}?"
        r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?"
        r"(?:PRIVATE\s+KEY|CERTIFICATE|ENCRYPTED\s+PRIVATE\s+KEY)-----"
    ),
    # OpenAI sk-
    re.compile(r"\bsk-[A-Za-z0-9_\-]{32,}\b"),
    # GitHub PATs
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),
    # AWS access key IDs
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Authorization: Bearer
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._\-]{16,})"),
)

REDACTED: Final[str] = "<REDACTED>"


def redact_secrets(text: str) -> str:
    """Return `text` with all known secret patterns replaced by REDACTED.

    Idempotent: `redact_secrets(redact_secrets(x)) == redact_secrets(x)`.
    """
    if not text:
        return text
    result = text
    # Named assignments: keep prefix + quote, replace value, preserve quote pairing.
    for pattern in _NAMED_ASSIGNMENT_PATTERNS:
        result = pattern.sub(rf"\1\2{REDACTED}\2", result)
    # Full-match patterns: replace the entire match with REDACTED.
    for pattern in _FULL_MATCH_PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result
```

- [ ] **Step 4: Run test to confirm it passes**
Run: `python -m unittest tests.quality.rollup_v2.test_redaction -v`
Expected: 14 tests, all OK. If any fail, adjust regex or test boundaries.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/redaction.py tests/quality/rollup_v2/test_redaction.py
git commit -m "feat(qrv2): redact_secrets() with 7 secret patterns (§B.1.1)"
```

### Task 2.2: Extend `redact_secrets` patterns for Slack, Stripe, GCP, Azure

**Files:**
- Modify: `scripts/quality/rollup_v2/redaction.py`
- Modify: `tests/quality/rollup_v2/test_redaction.py`

- [ ] **Step 1: Add failing tests for 4 new pattern categories**

Append to `RedactSecretsTests`:
```python
    def test_slack_bot_token(self):
        token = "xox" + "b-1234567890-1234567890-abcdef1234567890ABCDEF"
        out = redact_secrets(f"slack: {token}")
        self.assertIn(REDACTED, out)
        self.assertNotIn(token, out)

    def test_stripe_live_secret(self):
        key = "sk_live_" + "a" * 24
        out = redact_secrets(f"stripe: {key}")
        self.assertIn(REDACTED, out)
        self.assertNotIn(key, out)

    def test_gcp_private_key_json(self):
        blob = '"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvQIBAD...aaa"'
        out = redact_secrets(blob)
        self.assertIn(REDACTED, out)

    def test_azure_sas_token_in_url(self):
        url = "https://example.blob.core.windows.net/container?sig=abc123def456ghi789jkl012mnop345qrs&sv=2020-01-01"
        out = redact_secrets(url)
        self.assertIn(REDACTED, out)
        self.assertNotIn("abc123def456ghi789jkl012mnop345qrs", out)
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Add the 4 patterns to `_FULL_MATCH_PATTERNS` in `redaction.py`**

Append to `_FULL_MATCH_PATTERNS`:
```python
    # Slack bot/user/app/legacy tokens
    re.compile(r"\bxox[baprs]-[0-9]+-[0-9]+-[A-Za-z0-9]{20,}\b"),
    # Stripe live/test keys
    re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{24,}\b"),
    # Azure SAS: sig=<urlsafe-base64>
    re.compile(r"(?i)([?&]sig=)([A-Za-z0-9%+/=\-_]{20,})"),
```

For GCP private_key inside JSON blobs, rely on the existing PEM-block pattern to match the `-----BEGIN PRIVATE KEY-----` content. The value between PEM markers is redacted via the existing rule. Add one more specific pattern for the case where the PEM is JSON-escaped (`\\n` separators) which the existing regex won't catch:
```python
    # GCP service account: JSON-escaped PEM block
    re.compile(
        r'"private_key"\s*:\s*"-----BEGIN[^"]+?-----END[^"]+?"',
        re.DOTALL,
    ),
```

- [ ] **Step 4: Run to confirm pass**

Fix any test/pattern mismatches. `sig=` pattern uses `\1` backreference to preserve the `sig=` prefix.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/redaction.py tests/quality/rollup_v2/test_redaction.py
git commit -m "feat(qrv2): extend redact_secrets for Slack/Stripe/GCP/Azure (per Round 3 security)"
```

### Task 2.3: Integration test — end-to-end normalizer pipeline redaction

Deferred until Phase 5 (BaseNormalizer exists) — tracked here for sequencing. Do NOT start this task until Task 5.2 is complete.

---

## Phase 3 — Path validation hardening (§B.2, ~2 tasks)

### Task 3.1: Add symlink/traversal tests for existing `_ensure_within_root` (tests only — no function modification)

**Scope note**: Design §B.2.3 mandates "A.2.3 **reuses `_ensure_within_root` as-is**." Modifying `scripts/quality/common.py` is PR 4 scope per A.3.3. This task ONLY adds new test coverage for the existing helper — the function body stays unchanged. Any hardening the rollup_v2 pipeline needs happens in Task 3.2 inside `scripts/quality/rollup_v2/path_safety.py`, which is within PR 1's coverage scope.

**Files:**
- Modify: `tests/test_quality_common.py` (or `tests/test_security_helpers.py` — whichever currently exercises `common.py`)

- [ ] **Step 1: Locate the existing `_ensure_within_root` tests**

Run: `grep -rn "_ensure_within_root" tests/`
Expected: find existing tests in `tests/test_quality_common.py` or `tests/test_security_helpers.py`.

- [ ] **Step 2: Add 4 new tests that exercise the helper's current lexical semantics with pre-resolved paths**

Add to the appropriate existing test file (`tests/test_quality_common.py` is the most likely location):
```python
import sys
import tempfile
from pathlib import Path


class EnsureWithinRootPR1Tests(unittest.TestCase):
    """Augmenting tests for scripts.quality.common._ensure_within_root (per QRv2 §B.2.3).

    These tests assume callers pre-resolve paths via Path.resolve(strict=False)
    before invoking the helper — this is the contract that
    scripts/quality/rollup_v2/path_safety.validate_finding_file enforces in PR 1.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name).resolve()
        (self.tmp_root / "a").mkdir()
        (self.tmp_root / "a" / "b.py").write_text("pass", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_well_formed_resolved_path_accepted(self):
        from scripts.quality.common import _ensure_within_root
        _ensure_within_root(
            (self.tmp_root / "a" / "b.py").resolve(strict=False),
            self.tmp_root,
        )

    def test_resolved_dotdot_escape_rejected(self):
        from scripts.quality.common import _ensure_within_root
        escape = (self.tmp_root / ".." / ".." / "etc" / "passwd").resolve(strict=False)
        with self.assertRaises(ValueError):
            _ensure_within_root(escape, self.tmp_root)

    def test_absolute_escape_rejected(self):
        from scripts.quality.common import _ensure_within_root
        with self.assertRaises(ValueError):
            _ensure_within_root(Path("/etc/passwd"), self.tmp_root)

    @unittest.skipIf(sys.platform == "win32", "POSIX symlink behavior required")
    def test_symlink_escape_rejected_when_resolved_by_caller(self):
        escape_link = self.tmp_root / "a" / "escape"
        escape_link.symlink_to(Path("/etc/passwd"))
        from scripts.quality.common import _ensure_within_root
        with self.assertRaises(ValueError):
            _ensure_within_root(escape_link.resolve(strict=False), self.tmp_root)
```

- [ ] **Step 3: Run tests to confirm they pass against the CURRENT `_ensure_within_root` implementation**
Run: `python -m unittest tests.test_quality_common -v`
Expected: all existing tests still pass + 4 new tests pass. `common.py` is NOT modified. If any new test fails, the finding itself is important — escalate to writing-plans before modifying `common.py` (which would be out of PR 1 scope per A.3.3).

- [ ] **Step 4: Commit (tests only)**
```bash
git add tests/test_quality_common.py
git commit -m "test(common): add _ensure_within_root symlink/dotdot/abs tests (QRv2 §B.2.3)"
```

### Task 3.2: Defense-in-depth: path validation helper for `rollup_v2/`

**Files:**
- Create: `scripts/quality/rollup_v2/path_safety.py`
- Create: `tests/quality/rollup_v2/test_path_safety.py`

- [ ] **Step 1: Write failing test**

`tests/quality/rollup_v2/test_path_safety.py`:
```python
"""Tests for rollup_v2 path safety wrapper (per design §B.2)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.path_safety import (
    PathEscapedRootError,
    validate_finding_file,
)


class ValidateFindingFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("x", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_relative_path_passes(self):
        result = validate_finding_file("src/a.py", self.root)
        self.assertEqual(result, (self.root / "src" / "a.py").resolve())

    def test_absolute_escape_raises(self):
        with self.assertRaises(PathEscapedRootError):
            validate_finding_file("/etc/passwd", self.root)

    def test_dotdot_escape_raises(self):
        with self.assertRaises(PathEscapedRootError):
            validate_finding_file("../../etc/passwd", self.root)

    def test_empty_path_raises(self):
        with self.assertRaises(PathEscapedRootError):
            validate_finding_file("", self.root)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run → fail (ModuleNotFoundError)**

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/path_safety.py`:
```python
"""Path safety wrapper for rollup_v2 (per design §A.2.3 + §B.2).

IMPORTANT: This wrapper performs path resolution (`.resolve(strict=False)`)
on both the candidate and the repo root BEFORE calling `_ensure_within_root`
from `scripts.quality.common`. `_ensure_within_root` itself is lexical-only
and is NOT modified in PR 1 (per design §B.2.3 — "reuses as-is"). All path
normalization for rollup_v2 happens here, keeping the change inside PR 1's
coverage scope (`scripts/quality/rollup_v2/` per A.3.3).
"""
from __future__ import absolute_import

from pathlib import Path

from scripts.quality.common import _ensure_within_root


class PathEscapedRootError(ValueError):
    """Raised when a finding's file path escapes the repo root."""


def validate_finding_file(finding_file: str, repo_root: Path) -> Path:
    """Validate a `Finding.file` value against `repo_root`.

    Resolves both the candidate and the repo root via `.resolve(strict=False)`
    before comparison, so symlink-escape, lexical `..`, and non-existent-path
    traversal attempts are all caught even though the underlying
    `_ensure_within_root` helper is lexical-only.

    Returns the resolved absolute path on success. Raises PathEscapedRootError
    on any failure (lexical escape, symlink escape, absolute path outside root,
    empty string).
    """
    if not finding_file:
        raise PathEscapedRootError("finding_file is empty")
    raw = Path(finding_file) if Path(finding_file).is_absolute() else (repo_root / finding_file)
    # Resolve BOTH sides before calling _ensure_within_root. This is where rollup_v2
    # gets its "strengthened" path check without touching scripts/quality/common.py.
    candidate = raw.resolve(strict=False)
    resolved_root = repo_root.resolve(strict=False)
    try:
        _ensure_within_root(candidate, resolved_root)
    except ValueError as exc:
        raise PathEscapedRootError(str(exc)) from exc
    return candidate
```

- [ ] **Step 4: Run to pass**
Run: `python -m unittest tests.quality.rollup_v2.test_path_safety -v`
Expected: 4 tests, OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/path_safety.py tests/quality/rollup_v2/test_path_safety.py
git commit -m "feat(qrv2): path_safety.validate_finding_file wrapper (§B.2.2)"
```

---

## Phase 4 — Taxonomy loader (§A.4.4, ~3 tasks)

### Task 4.1: Create seed taxonomy YAML files with ~40 canonical categories

**Files:**
- Create: `config/taxonomy/codacy.yaml`
- Create: `config/taxonomy/sonarcloud.yaml`
- Create: `config/taxonomy/deepsource.yaml`
- Create: `config/taxonomy/semgrep.yaml`
- Create: `config/taxonomy/codeql.yaml`
- Create: `config/taxonomy/qlty.yaml`
- Create: `config/taxonomy/deepscan.yaml`

- [ ] **Step 1: Seed each YAML with the skeleton + first 5-10 mappings**

`config/taxonomy/codacy.yaml`:
```yaml
# Canonical category mapping for Codacy rule IDs (per design §3.2, §A.4.4).
# Add new rule mappings here as they are encountered in production.
# Rule IDs are the exact strings Codacy reports in its findings JSON.
---
provider: Codacy
mapping:
  # Pylint rules (most common Python rules)
  Pylint_W0703: broad-except
  Pylint_W0611: unused-import
  Pylint_W0612: unused-variable
  Pylint_W0102: mutable-default
  Pylint_R0903: too-few-methods
  Pylint_R0912: too-many-branches
  Pylint_R0913: too-many-args
  Pylint_R0914: too-many-locals
  Pylint_R0915: too-many-statements
  Pylint_C0301: line-too-long
  Pylint_C0303: trailing-whitespace
  Pylint_C0114: missing-docstring
  Pylint_C0115: missing-docstring
  Pylint_C0116: missing-docstring
  Pylint_W0603: global-statement
  Pylint_W0104: no-effect
  Pylint_R1705: unnecessary-else
  # Bandit rules (security)
  Bandit_B101: assert-in-production
  Bandit_B102: exec-used
  Bandit_B104: hardcoded-bind-all-interfaces
  Bandit_B105: hardcoded-password-string
  Bandit_B303: weak-crypto
  Bandit_B311: insecure-random
  Bandit_B404: subprocess-import
  Bandit_B602: command-injection
  Bandit_B608: sql-injection
```

Repeat for the other 6 providers with an initial 5-10 mappings each. The ~30 additional mappings per provider are deferred to PR 1.5 or the writing-plans research phase.

**For this PR 1 plan, include ONLY the seed mappings above** — enough to prove the loader works. More mappings land as normalizers are implemented.

Content for each file (minimum seed, ~5 mappings each):

`config/taxonomy/sonarcloud.yaml`:
```yaml
---
provider: SonarCloud
mapping:
  python:S1166: broad-except      # "Exception handlers should preserve..."
  python:S1481: unused-variable
  python:S1763: unreachable-code
  python:S2245: insecure-random
  python:S5547: weak-crypto
```

`config/taxonomy/deepsource.yaml`:
```yaml
---
provider: DeepSource
mapping:
  PYL-W0703: broad-except
  PYL-W0611: unused-import
  BAN-B101: assert-in-production
  PYL-C0301: line-too-long
  BAN-B311: insecure-random
```

`config/taxonomy/semgrep.yaml`:
```yaml
---
provider: Semgrep
mapping:
  python.lang.security.dangerous-eval: code-injection
  python.lang.security.weak-cryptographic-hash: weak-crypto
  python.flask.security.xss: xss
```

`config/taxonomy/codeql.yaml`:
```yaml
---
provider: CodeQL
mapping:
  py/bare-except: broad-except
  py/command-line-injection: command-injection
  py/sql-injection: sql-injection
  py/weak-crypto-key: weak-crypto
```

`config/taxonomy/qlty.yaml`:
```yaml
---
provider: QLTY
mapping:
  qlty_rule_unused_import: unused-import
  qlty_rule_too_many_lines: too-long
  qlty_rule_complexity: too-complex
```

`config/taxonomy/deepscan.yaml`:
```yaml
---
provider: DeepScan
mapping:
  DS_UNUSED_VAR: unused-variable
  DS_UNREACHABLE: dead-code
```

- [ ] **Step 2: Commit the 7 YAML files**
```bash
git add config/taxonomy/*.yaml
git commit -m "feat(qrv2): seed taxonomy YAML files for 7 providers (§A.4.4)"
```

### Task 4.2: `taxonomy.lookup()` loader + tests

**Files:**
- Create: `scripts/quality/rollup_v2/taxonomy.py`
- Create: `tests/quality/rollup_v2/test_taxonomy.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for taxonomy loader (per design §A.4.4)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.taxonomy import lookup, load_all_taxonomies


class TaxonomyTests(unittest.TestCase):
    def test_codacy_pylint_broad_except(self):
        self.assertEqual(lookup("Codacy", "Pylint_W0703"), "broad-except")

    def test_sonarcloud_broad_except(self):
        self.assertEqual(lookup("SonarCloud", "python:S1166"), "broad-except")

    def test_codeql_broad_except(self):
        self.assertEqual(lookup("CodeQL", "py/bare-except"), "broad-except")

    def test_unknown_rule_returns_none(self):
        self.assertIsNone(lookup("Codacy", "Pylint_NoSuchRule"))

    def test_unknown_provider_returns_none(self):
        self.assertIsNone(lookup("MysteryVendor", "anything"))

    def test_load_all_taxonomies_returns_all_seven(self):
        all_tax = load_all_taxonomies()
        for provider in ("Codacy", "SonarCloud", "DeepSource", "Semgrep",
                         "CodeQL", "QLTY", "DeepScan"):
            self.assertIn(provider, all_tax)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/taxonomy.py`:
```python
"""Canonical category taxonomy loader (per design §A.4.4)."""
from __future__ import absolute_import

from functools import lru_cache
from pathlib import Path
from typing import Mapping

import yaml

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config" / "taxonomy"


@lru_cache(maxsize=1)
def load_all_taxonomies() -> Mapping[str, Mapping[str, str]]:
    """Load every provider's taxonomy YAML and return {provider: {rule_id: category}}.

    Result is cached for the lifetime of the process. Call `load_all_taxonomies.cache_clear()`
    if the on-disk YAMLs change during tests.
    """
    result: dict[str, dict[str, str]] = {}
    if not _CONFIG_DIR.exists():
        return result
    for yaml_path in sorted(_CONFIG_DIR.glob("*.yaml")):
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        provider = data.get("provider")
        mapping = data.get("mapping") or {}
        if not isinstance(provider, str) or not isinstance(mapping, dict):
            raise ValueError(f"invalid taxonomy file: {yaml_path}")
        result[provider] = {str(k): str(v) for k, v in mapping.items()}
    return result


def lookup(provider: str, rule_id: str) -> str | None:
    """Return the canonical category for (provider, rule_id), or None if unmapped."""
    return load_all_taxonomies().get(provider, {}).get(rule_id)
```

- [ ] **Step 4: Run to pass**

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/taxonomy.py tests/quality/rollup_v2/test_taxonomy.py
git commit -m "feat(qrv2): taxonomy YAML loader + lookup() (§A.4.4)"
```

### Task 4.3: Track unmapped rules for observability (§A.4.5)

**Files:**
- Modify: `scripts/quality/rollup_v2/taxonomy.py`
- Modify: `tests/quality/rollup_v2/test_taxonomy.py`

- [ ] **Step 1: Add test for an unmapped-rules collector**

Add test:
```python
    def test_unmapped_rules_collector(self):
        from scripts.quality.rollup_v2.taxonomy import UnmappedRulesCollector
        collector = UnmappedRulesCollector()
        collector.record("Codacy", "Pylint_Unknown_1")
        collector.record("Codacy", "Pylint_Unknown_1")
        collector.record("SonarCloud", "python:Snew")
        entries = collector.as_list()
        self.assertEqual(len(entries), 2)
        codacy_entry = next(e for e in entries if e["provider"] == "Codacy")
        self.assertEqual(codacy_entry["count"], 2)
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Add `UnmappedRulesCollector` class**

```python
class UnmappedRulesCollector:
    """Accumulates unmapped (provider, rule_id) pairs during a rollup run."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str], int] = {}

    def record(self, provider: str, rule_id: str) -> None:
        key = (provider, rule_id)
        self._counts[key] = self._counts.get(key, 0) + 1

    def as_list(self) -> list[dict[str, object]]:
        """Return the collected entries as dicts sorted by (provider, rule_id)."""
        return [
            {"provider": p, "rule_id": r, "count": c}
            for (p, r), c in sorted(self._counts.items())
        ]
```

- [ ] **Step 4: Run to pass**

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/taxonomy.py tests/quality/rollup_v2/test_taxonomy.py
git commit -m "feat(qrv2): UnmappedRulesCollector for §A.4.5 observability"
```

---

## Phase 5 — BaseNormalizer abstract class (§B.1.2, ~2 tasks)

### Task 5.1: `BaseNormalizer` abstract class with `@final` `finalize()`

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/_base.py`
- Create: `tests/quality/rollup_v2/test_base_normalizer.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for BaseNormalizer abstract class (per design §B.1.2 + §A.6)."""
from __future__ import absolute_import

import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer, NormalizerResult
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    SCHEMA_VERSION,
    Finding,
)


class _DemoNormalizer(BaseNormalizer):
    provider = "Codacy"

    def parse(self, artifact, repo_root):
        # Pretend to parse an artifact and yield one Finding
        return [
            self._build_finding(
                finding_id="demo-1",
                file="a.py",
                line=1,
                category="broad-except",
                category_group=CATEGORY_GROUP_QUALITY,
                severity="medium",
                primary_message='FOO_KEY = "EXAMPLE_REDACTED" was here',
                rule_id="Pylint_W0703",
                rule_url=None,
                original_message="broad-except",
                context_snippet='API_KEY = "EXAMPLE_REDACTED"',
            )
        ]


class BaseNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "a.py").write_text("pass\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_finalize_redacts_context_snippet(self):
        norm = _DemoNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertIsInstance(result, NormalizerResult)
        self.assertEqual(len(result.findings), 1)
        finding = result.findings[0]
        self.assertNotIn("EXAMPLE_REDACTED", finding.context_snippet)
        self.assertIn("<REDACTED>", finding.context_snippet)

    def test_finalize_redacts_primary_message(self):
        norm = _DemoNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertNotIn("EXAMPLE_REDACTED", result.findings[0].primary_message)

    def test_path_escape_produces_normalizer_error_not_finding(self):
        class _EscapeNormalizer(_DemoNormalizer):
            def parse(self, artifact, repo_root):
                return [
                    self._build_finding(
                        finding_id="x",
                        file="../../etc/passwd",
                        line=1,
                        category="broad-except",
                        category_group=CATEGORY_GROUP_QUALITY,
                        severity="low",
                        primary_message="m",
                        rule_id="R",
                        rule_url=None,
                        original_message="m",
                        context_snippet="",
                    )
                ]
        norm = _EscapeNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(len(result.security_drops), 1)

    def test_crash_in_parse_is_caught_and_reported(self):
        class _CrashNormalizer(_DemoNormalizer):
            def parse(self, artifact, repo_root):
                raise ValueError("simulated parser crash")
        norm = _CrashNormalizer()
        result = norm.run(artifact=None, repo_root=self.root)
        self.assertEqual(len(result.findings), 0)
        self.assertEqual(len(result.normalizer_errors), 1)
        err = result.normalizer_errors[0]
        self.assertEqual(err["provider"], "Codacy")
        self.assertIn("simulated parser crash", err["error_message"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Write the implementation**

`scripts/quality/rollup_v2/normalizers/_base.py`:
```python
"""BaseNormalizer abstract class (per design §B.1.2 + §A.6)."""
from __future__ import absolute_import

import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, final

from scripts.quality.rollup_v2.path_safety import (
    PathEscapedRootError,
    validate_finding_file,
)
from scripts.quality.rollup_v2.redaction import redact_secrets
from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    SCHEMA_VERSION,
    Finding,
    CategoryGroup,
)


@dataclass(frozen=True, slots=True)
class NormalizerResult:
    findings: tuple[Finding, ...]
    normalizer_errors: tuple[dict[str, str], ...]
    security_drops: tuple[dict[str, str], ...]


class BaseNormalizer(ABC):
    """Base class for all per-provider normalizers.

    Subclasses implement `parse(artifact, repo_root)` which returns an iterable
    of Finding objects. The base class's `run()` method is `@final` — it wraps
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

        This method is @final — subclasses MUST NOT override it.
        """
        findings_out: list[Finding] = []
        errors: list[dict[str, str]] = []
        drops: list[dict[str, str]] = []
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
            # 2. Redaction (belt-and-suspenders — already applied by _build_finding,
            # but applied again at finalize time to catch subclass bypass attempts)
            redacted = self._redact_finding(finding)
            findings_out.append(redacted)
        return NormalizerResult(
            findings=tuple(findings_out),
            normalizer_errors=tuple(errors),
            security_drops=tuple(drops),
        )

    def _build_finding(
        self,
        *,
        finding_id: str,
        file: str,
        line: int,
        category: str,
        category_group: CategoryGroup,
        severity: str,
        primary_message: str,
        rule_id: str,
        rule_url: str | None,
        original_message: str,
        context_snippet: str,
        end_line: int | None = None,
        column: int | None = None,
        fix_hint: str | None = None,
        cwe: str | None = None,
    ) -> Finding:
        """Helper for subclasses to construct Findings with redaction applied."""
        corroborator = Corroborator.from_provider(
            provider=self.provider,
            rule_id=rule_id,
            rule_url=rule_url,
            original_message=redact_secrets(original_message),
        )
        return Finding(
            schema_version=SCHEMA_VERSION,
            finding_id=finding_id,
            file=file,
            line=line,
            end_line=end_line if end_line is not None else line,
            column=column,
            category=category,
            category_group=category_group,
            severity=severity,
            corroboration="single",
            primary_message=redact_secrets(primary_message),
            corroborators=(corroborator,),
            fix_hint=fix_hint,
            patch=None,
            patch_source="none",
            patch_confidence=None,
            context_snippet=redact_secrets(context_snippet),
            source_file_hash="",
            cwe=cwe,
            autofixable=False,
            tags=(),
        )

    @staticmethod
    def _redact_finding(finding: Finding) -> Finding:
        """Re-apply redaction to every string field of a Finding (idempotent)."""
        from dataclasses import replace
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
```

- [ ] **Step 4: Run to pass**
Run: `python -m unittest tests.quality.rollup_v2.test_base_normalizer -v`
Expected: 4 tests, OK.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/normalizers/_base.py tests/quality/rollup_v2/test_base_normalizer.py
git commit -m "feat(qrv2): BaseNormalizer with @final run() + redaction + path guard (§B.1.2)"
```

### Task 5.2: Integration test — full pipeline end-to-end redaction (deferred task 2.3)

**Files:**
- Create: `tests/quality/rollup_v2/test_integration_redaction.py`

- [ ] **Step 1: Write integration test**

```python
"""End-to-end integration test: normalizer → redact → canonical.json (no secret leaks)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.schema.finding import CATEGORY_GROUP_QUALITY


class _LeakyNormalizer(BaseNormalizer):
    provider = "Codacy"

    def parse(self, artifact, repo_root):
        return [
            self._build_finding(
                finding_id="leak-1",
                file="a.py",
                line=1,
                category="broad-except",
                category_group=CATEGORY_GROUP_QUALITY,
                severity="medium",
                primary_message='leaked API_KEY = "EXAMPLE_KEY_3"',
                rule_id="Pylint_W0703",
                rule_url=None,
                # IMPLEMENTER: build the test token at runtime via the helper
                # `_build_test_token_shape()` from test_redaction.py (or duplicate
                # it into this file's module scope). Never paste a literal token.
                original_message=f"also leaked: token={_build_test_token_shape()}",
                context_snippet=f'secret = "{"verylong" + "secretvaluegoeshereabcdef"}"',
            )
        ]


class IntegrationRedactionTests(unittest.TestCase):
    def test_no_secret_survives_to_canonical_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "a.py").write_text("x", encoding="utf-8")
            norm = _LeakyNormalizer()
            result = norm.run(artifact=None, repo_root=root)
            serialized = json.dumps([asdict(f) for f in result.findings], default=str)
            # IMPLEMENTER: the _LeakyNormalizer builds its token with
            # `_build_test_token_shape()` (see test_redaction.py). Here we only
            # assert that no substring used at construction time survived to
            # canonical.json. Replace the second tuple entry with the same
            # token value the normalizer produced (capture it before passing
            # to .run()).
            leaky_token = _build_test_token_shape()   # implementer: pass the SAME value into _LeakyNormalizer
            for secret in (
                "EXAMPLE_KEY_3",
                leaky_token,
                "verylongsecretvaluegoeshereabcdef",
            ):
                self.assertNotIn(secret, serialized, f"Secret leaked: {secret}")
            self.assertIn("<REDACTED>", serialized)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run → expected PASS** (BaseNormalizer already redacts; this is verification)

- [ ] **Step 3: Commit**
```bash
git add tests/quality/rollup_v2/test_integration_redaction.py
git commit -m "test(qrv2): end-to-end integration test — no secret survives to canonical.json"
```

---

## Phase 6 — Per-provider normalizers (9 lanes, ~9 tasks)

This phase implements one normalizer per **existing** `LANE_CONTEXTS` entry in `scripts/quality/build_quality_rollup.py`. Verified at plan-writing time, the 9 existing lanes are:

| # | Lane key | Context name | Current artifact | Plan task |
|---|---|---|---|---|
| 1 | `codacy` | Codacy Zero | `codacy-zero/codacy.json` | 6.1 (detailed) |
| 2 | `sonar` | Sonar Zero | `sonar-zero/sonar.json` | 6.2 |
| 3 | `deepsource_visible` | DeepSource Visible Zero | `deepsource-visible-zero/deepsource.json` | 6.3 |
| 4 | `deepscan` | DeepScan Zero | `deepscan-zero/deepscan.json` | 6.4 |
| 5 | `qlty_zero` | QLTY Zero | `qlty-zero/qlty-zero.json` | 6.5 |
| 6 | `sentry` | Sentry Zero | `sentry-zero/sentry.json` | 6.6 |
| 7 | `deps` | Dependency Alerts | `deps-zero/deps.json` | 6.7 |
| 8 | `coverage` | Coverage 100 Gate | `coverage-100/coverage.json` | 6.8 |
| 9 | `secrets` | Quality Secrets Preflight | `quality-secrets/secrets.json` | 6.9 |

**Scope boundary**: Semgrep, CodeQL, Chromatic, and Applitools are **NOT in PR 1** — they are PR 3 scope per design §10 PR 3. PR 1 only pre-reserves their `LANE_CONTEXTS` keys (Phase 15) without implementing their normalizers or any SARIF parsing helpers. The §A.2.5 SARIF 50MB size guard ships in PR 3 together with the `_sarif.py` helper.

**Template for each normalizer task:**
1. Read the provider's native artifact format (research: find a real example in the repo's `tests/` or generate one)
2. Write a test that feeds a fixture artifact through the normalizer and asserts on the produced Finding list
3. Run → fail
4. Implement the normalizer subclass extending `BaseNormalizer`
5. Run → pass
6. Commit

### Task 6.1: Codacy normalizer (DETAILED — template for other 8)

**Files:**
- Create: `scripts/quality/rollup_v2/normalizers/codacy.py`
- Create: `tests/quality/rollup_v2/fixtures/normalizers/codacy_sample.json` (fixture)
- Create: `tests/quality/rollup_v2/test_normalizer_codacy.py`

- [ ] **Step 1: Create fixture + failing test**

First, study the existing `scripts/quality/check_codacy_zero.py` to see what Codacy's JSON shape is. Based on that, create `tests/quality/rollup_v2/fixtures/normalizers/codacy_sample.json`:
```json
{
  "issues": [
    {
      "message": "Catch a more specific exception",
      "patternId": "Pylint_W0703",
      "patternUrl": "https://app.codacy.com/p/123/patterns/Pylint_W0703",
      "filename": "scripts/quality/coverage_parsers.py",
      "line": 42,
      "severity": "Warning"
    },
    {
      "message": "Unused import",
      "patternId": "Pylint_W0611",
      "patternUrl": null,
      "filename": "scripts/quality/common.py",
      "line": 10,
      "severity": "Info"
    }
  ]
}
```

Then write the test:
```python
"""Tests for Codacy normalizer (per §6.1)."""
from __future__ import absolute_import

import json
import sys
import tempfile
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.normalizers.codacy import CodacyNormalizer

_FIXTURE = Path(__file__).parent / "fixtures" / "normalizers" / "codacy_sample.json"


class CodacyNormalizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "scripts" / "quality").mkdir(parents=True)
        (self.root / "scripts" / "quality" / "coverage_parsers.py").write_text("pass", "utf-8")
        (self.root / "scripts" / "quality" / "common.py").write_text("pass", "utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_two_findings(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual(result.findings[0].category, "broad-except")
        self.assertEqual(result.findings[1].category, "unused-import")

    def test_warning_maps_to_medium(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].severity, "medium")

    def test_info_maps_to_low(self):
        artifact = json.loads(_FIXTURE.read_text("utf-8"))
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[1].severity, "low")

    def test_unmapped_rule_falls_through_to_uncategorized(self):
        artifact = {
            "issues": [{
                "message": "Unknown rule fires",
                "patternId": "Pylint_NoSuchRule",
                "patternUrl": None,
                "filename": "scripts/quality/common.py",
                "line": 1,
                "severity": "Info",
            }]
        }
        result = CodacyNormalizer().run(artifact=artifact, repo_root=self.root)
        self.assertEqual(result.findings[0].category, "uncategorized")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Write minimal implementation**

`scripts/quality/rollup_v2/normalizers/codacy.py`:
```python
"""Codacy normalizer (per design §4.2 + §A.6)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Any, Iterable

from scripts.quality.rollup_v2.normalizers._base import BaseNormalizer
from scripts.quality.rollup_v2.taxonomy import lookup
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    CATEGORY_GROUP_SECURITY,
    Finding,
)

_SEVERITY_MAP = {
    "Error": "high",
    "Warning": "medium",
    "Info": "low",
}

_SECURITY_CATEGORY_HINTS = frozenset({
    "sql-injection", "command-injection", "hardcoded-password-string",
    "weak-crypto", "insecure-random", "exec-used", "xss",
})


class CodacyNormalizer(BaseNormalizer):
    provider = "Codacy"

    def parse(self, artifact: Any, repo_root: Path) -> Iterable[Finding]:
        issues = (artifact or {}).get("issues", [])
        for index, issue in enumerate(issues):
            pattern_id = str(issue.get("patternId", ""))
            category = lookup("Codacy", pattern_id) or "uncategorized"
            group = (
                CATEGORY_GROUP_SECURITY
                if category in _SECURITY_CATEGORY_HINTS
                else CATEGORY_GROUP_QUALITY
            )
            yield self._build_finding(
                finding_id=f"codacy-{index:04d}",
                file=str(issue.get("filename", "")),
                line=int(issue.get("line") or 1),
                category=category,
                category_group=group,
                severity=_SEVERITY_MAP.get(str(issue.get("severity", "Warning")), "medium"),
                primary_message=str(issue.get("message", "")),
                rule_id=pattern_id,
                rule_url=issue.get("patternUrl"),
                original_message=str(issue.get("message", "")),
                context_snippet="",
            )
```

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/normalizers/codacy.py tests/quality/rollup_v2/test_normalizer_codacy.py tests/quality/rollup_v2/fixtures/normalizers/codacy_sample.json
git commit -m "feat(qrv2): Codacy normalizer with 4 tests (§A.6)"
```

### Tasks 6.2 - 6.9: Remaining 8 normalizers (condensed)

**Each follows Task 6.1's 5-step structure (fixture + test + implement + pass + commit).** Implementer should study the relevant existing `check_X_zero.py` script to understand each provider's artifact shape.

| Task | Lane key | Provider name | Existing reference | Artifact shape (rough) | Severity map |
|---|---|---|---|---|---|
| 6.2 | `sonar` | SonarCloud | `check_sonar_zero.py` | `{"issues": [{"rule": "python:S1166", "severity": "MAJOR", "line": 42, "component": "path/a.py:42", "message": "..."}]}` | BLOCKER→critical, CRITICAL→high, MAJOR→medium, MINOR→low, INFO→info |
| 6.3 | `deepsource_visible` | DeepSource | `check_deepsource_zero.py` | `{"issues": [{"issue_code": "PYL-W0703", "location": {"path": "a.py", "position": {"begin": {"line": 42}}}, "title": "...", "severity": "MAJOR"}]}` | CRITICAL→high, MAJOR→medium, MINOR→low |
| 6.4 | `deepscan` | DeepScan | `check_deepscan_zero.py` | `{"alarms": [{"name": "DS_UNUSED_VAR", "file": "a.py", "line": 42, "message": "..."}]}` | default all to "medium" |
| 6.5 | `qlty_zero` | QLTY | `run_qlty_zero.py`, `normalize_coverage_for_qlty.py` | QLTY findings JSON — study the file | QLTY provides severity directly |
| 6.6 | `sentry` | Sentry | `check_sentry_zero.py` | Sentry issue API response | Sentry's `level` field → severity |
| 6.7 | `deps` | Dependabot | `check_dependabot_alerts.py` | GitHub alerts API response | alert `severity` → severity |
| 6.8 | `coverage` | Coverage | `run_coverage_gate.py`, `assert_coverage_100.py` | `coverage-100/coverage.json` — line/branch percentages per module | percent-based: <80 → high, 80-95 → medium, 95-99 → low |
| 6.9 | `secrets` | QualitySecrets | `check_required_checks.py` + repo-local secrets preflight | `quality-secrets/secrets.json` — list of detected secrets | all → critical (secrets are always high-risk) |

**Scope reminder**: Tasks 6.8 (Coverage) and 6.9 (Secrets) replace what an earlier plan revision had incorrectly listed as Semgrep/CodeQL normalizers. Semgrep and CodeQL ship in PR 3 per §10 PR 3 — PR 1 only pre-reserves their lane keys in Phase 15.

**For each task, use this concrete sequence:**
- Create `scripts/quality/rollup_v2/normalizers/<provider_lower>.py`
- Create `tests/quality/rollup_v2/fixtures/normalizers/<provider_lower>_sample.json`
- Create `tests/quality/rollup_v2/test_normalizer_<provider_lower>.py`
- Test asserts: (a) correct number of findings parsed, (b) at least one mapped category, (c) at least one unmapped → `uncategorized` where applicable, (d) severity mapping correct
- Implement per the table above
- Commit with message `feat(qrv2): <provider> normalizer with N tests (§A.6)`

**Special handling for Task 6.8 (Coverage normalizer):**
- Coverage findings are not classic "lint findings" — they represent coverage percentages per file/module. The normalizer converts each below-threshold file into a `Finding` with:
  - `category = "coverage-gap"` (hard-coded — no taxonomy YAML needed, since coverage has no rule IDs)
  - `category_group = "quality"`
  - `severity` mapped from coverage percentage per the table above
  - `primary_message = f"Coverage {percent:.1f}% below threshold"`
  - `file`, `line = 1` (module-level finding)
- The Coverage normalizer bypasses `taxonomy.lookup()` and sets the category directly. Phase 4 Task 4.1 still seeds 7 YAML files (one per analyzer provider), not 8.
- Phase 9 has NO patch generator for `coverage-gap` — coverage fixes require writing tests, which is a human task. A thin `patches/coverage_gap.py` module that always returns `PatchDeclined(reason_code="requires-ast-rewrite", suggested_tier="human-only")` should be added to the GENERATORS dict. Add it to the Phase 9 summary table as Task 9.31 (the 31st entry — this is an addendum-driven expansion and is the only category outside the §5.1 list of 30 that PR 1 includes, because coverage is not a §5.1 lint category but IS an existing rollup lane that must be dispatched through the same infrastructure).

**Special handling for Task 6.9 (QualitySecrets normalizer):**
- Every entry in `secrets.json` produces one `Finding` with `severity = "critical"`, `category_group = "security"`, `category = "hardcoded-secret"` (already in §3.2 + taxonomy seed — Task 4.1 Codacy mapping has `Bandit_B105: hardcoded-password-string`; add `QualitySecrets: hardcoded-secret` as an implicit mapping or set category directly in the normalizer).
- `cwe = "CWE-798"` (Use of Hard-coded Credentials).
- Apply `redact_secrets()` to `primary_message` AND `context_snippet` per `BaseNormalizer.finalize()` — the secret IS the finding, so redaction is critical.
- Phase 9 Task 9.17 (`hardcoded-secret` patch generator) dispatches these findings — existing behavior, no changes needed.

---

## Phase 7 — Dedup + merge (§3.3 + §A.3.2, ~3 tasks)

### Task 7.1: `dedup()` hybrid algorithm

**Files:**
- Create: `scripts/quality/rollup_v2/dedup.py`
- Create: `tests/quality/rollup_v2/test_dedup.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for hybrid dedup + merge (per design §3.3 + §A.3.2)."""
# Test cases:
# 1. Two findings at same (file, line, category) in security/quality → merged
# 2. Two findings at same (file, line) in style → merged (regardless of category)
# 3. Two findings at same (file, line, different category) in quality → NOT merged
# 4. merge picks severity = max of inputs
# 5. merge sets corroboration = "multi" when ≥2
# 6. merge picks primary by provider priority (Sonar > Codacy → Sonar wins)
# 7. merge combines all corroborators from all inputs
```

Full test code follows the pattern above. Construct Findings via `Corroborator.from_provider()` + direct Finding construction. Assert the expected merge behavior.

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Implement `dedup()` and `merge_corroborators()` per §3.3 code example + §A.3.2 dataclass rewrite**

`scripts/quality/rollup_v2/dedup.py`:
```python
"""Hybrid dedup + corroborator merge (per design §3.3 + §A.3.2)."""
from __future__ import absolute_import

from dataclasses import replace
from typing import Iterable

from scripts.quality.rollup_v2.severity import max_severity
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_SECURITY,
    CATEGORY_GROUP_QUALITY,
    Finding,
)


def dedup(findings: Iterable[Finding]) -> list[Finding]:
    """Deduplicate findings per §3.3 hybrid algorithm."""
    buckets: dict[tuple, list[Finding]] = {}
    for f in findings:
        if f.category_group in (CATEGORY_GROUP_SECURITY, CATEGORY_GROUP_QUALITY):
            key = (f.file, f.line, f.category)
        else:   # style
            key = (f.file, f.line)
        buckets.setdefault(key, []).append(f)
    merged: list[Finding] = []
    for bucket_findings in buckets.values():
        if len(bucket_findings) == 1:
            merged.append(bucket_findings[0])
        else:
            merged.append(merge_corroborators(bucket_findings))
    return merged


def merge_corroborators(findings: list[Finding]) -> Finding:
    """Merge a bucket of findings into a single canonical finding per §A.3.2."""
    primary = _pick_primary_by_provider_priority(findings)
    severity = max_severity([f.severity for f in findings])
    all_corroborators = tuple(c for f in findings for c in f.corroborators)
    return replace(
        primary,
        severity=severity,
        corroboration="multi" if len(findings) >= 2 else "single",
        corroborators=all_corroborators,
    )


def _pick_primary_by_provider_priority(findings: list[Finding]) -> Finding:
    """Return the finding whose primary corroborator has the lowest rank (highest priority)."""
    def rank(f: Finding) -> int:
        if not f.corroborators:
            return 99
        return min(c.provider_priority_rank for c in f.corroborators)
    return min(findings, key=rank)
```

- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit**

### Task 7.2: Stable `finding_id` assignment after dedup

**Files:**
- Modify: `scripts/quality/rollup_v2/dedup.py`

- [ ] **Step 1: Write failing test** — after dedup, every Finding has a deterministic `qzp-NNNN` id sorted by (file, line, category)
- [ ] **Step 2: Run → fail**
- [ ] **Step 3: Add `assign_stable_ids(findings)` function that re-numbers after dedup**
- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit**

### Task 7.3: Dedup edge cases (empty, single-finding, all-same-provider)

- [ ] **Step 1: Add 3 more tests to `test_dedup.py` covering edge cases**
- [ ] **Step 2-5: TDD cycle**

---

## Phase 8 — Patch generator infrastructure (§5.1 + §A.1.3-A.1.5 + §B.3.5 + §B.3.11, ~4 tasks)

### Task 8.1: `PatchResult`, `PatchDeclined`, `PatchGenerator` Protocol types

**Files:**
- Create: `scripts/quality/rollup_v2/schema/patch.py`
- Create: `tests/quality/rollup_v2/test_patch_types.py`

- [ ] **Step 1: Write failing test**

Test asserts:
- `PatchResult` is frozen, has `unified_diff`, `confidence`, `category`, `generator_version`, `touches_files` (frozenset[Path])
- `PatchDeclined` is frozen, has `reason_code`, `reason_text`, `suggested_tier`
- `reason_code` rejects invalid strings (via mypy in CI; runtime we test the enum-like Literal with an assertion)
- `touches_files` is normalized to frozenset[Path] not frozenset[str]

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Write implementation using `Literal` type + `__post_init__` assertion for runtime safety** (per §B.3.11)

```python
"""Patch result/declined types (per §A.1.3 + §B.3.11)."""
from __future__ import absolute_import

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

PatchDeclinedReason = Literal[
    "requires-ast-rewrite",
    "cross-file-change",
    "ambiguous-fix",
    "provider-data-insufficient",
    "path-traversal-rejected",
]

PatchConfidence = Literal["high", "medium", "low"]
PatchSuggestedTier = Literal["llm-fallback", "human-only", "skip"]

_VALID_REASONS = frozenset(
    {"requires-ast-rewrite", "cross-file-change", "ambiguous-fix",
     "provider-data-insufficient", "path-traversal-rejected"}
)
_VALID_CONFIDENCE = frozenset({"high", "medium", "low"})
_VALID_TIERS = frozenset({"llm-fallback", "human-only", "skip"})


@dataclass(frozen=True, slots=True)
class PatchResult:
    unified_diff: str
    confidence: PatchConfidence
    category: str
    generator_version: str
    touches_files: frozenset[Path]

    def __post_init__(self) -> None:
        if self.confidence not in _VALID_CONFIDENCE:
            raise AssertionError(f"invalid confidence: {self.confidence!r}")
        if not self.touches_files:
            raise AssertionError("touches_files must be non-empty")
        for p in self.touches_files:
            if not isinstance(p, Path):
                raise AssertionError(f"touches_files must contain Path, got {type(p).__name__}")


@dataclass(frozen=True, slots=True)
class PatchDeclined:
    reason_code: PatchDeclinedReason
    reason_text: str
    suggested_tier: PatchSuggestedTier

    def __post_init__(self) -> None:
        if self.reason_code not in _VALID_REASONS:
            raise AssertionError(f"invalid reason_code: {self.reason_code!r}")
        if self.suggested_tier not in _VALID_TIERS:
            raise AssertionError(f"invalid suggested_tier: {self.suggested_tier!r}")


class PatchGenerator(Protocol):
    """Structural type for patch generator modules.

    Note: there is no `self` parameter — modules satisfy this protocol when
    they expose a module-level `generate(finding, *, source_file_content, repo_root)`
    function matching this signature.
    """
    def generate(
        self,
        finding: "Finding",                    # forward ref; import at runtime
        *,
        source_file_content: str,
        repo_root: Path,
    ) -> PatchResult | PatchDeclined | None: ...
```

(Note: the Protocol has `self` parameter but modules don't have `self`. Per Round 3 Designer suggestion, we need to disambiguate. The correct approach is to **not** use a Protocol class for module-level functions — use a plain callable type alias instead.)

Replace the Protocol with:
```python
from typing import Callable

# Module-level function signature; each generator module exposes `generate`
# matching this type.
GenerateFn = Callable[
    ["Finding", str, Path],              # positional finding, source_file_content, repo_root
    "PatchResult | PatchDeclined | None",
]
```

The dispatcher (Task 8.2) will call `gen.generate(finding, source_file_content=..., repo_root=...)` where `gen` is the imported module.

- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit**

### Task 8.2: Patch generator dispatcher (A.1.4) — starts empty, built up incrementally

**Files:**
- Modify: `scripts/quality/rollup_v2/patches/__init__.py`
- Create: `tests/quality/rollup_v2/test_dispatcher.py`

- [ ] **Step 1: Write failing test** that dispatches a fake finding with `category="unknown-category"` and asserts `None` (no generator registered).

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Implement dispatcher skeleton**

`scripts/quality/rollup_v2/patches/__init__.py`:
```python
"""Patch generator dispatcher (per design §A.1.4)."""
from __future__ import absolute_import

from pathlib import Path
from typing import Mapping

from scripts.quality.rollup_v2.path_safety import PathEscapedRootError, validate_finding_file
from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

# Populated by Task 9.x — initially empty. Every new generator gets an entry here.
GENERATORS: Mapping[str, object] = {}


def dispatch(
    finding: Finding,
    *,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Route a finding to its registered tier-1 generator (if any)."""
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
    return gen.generate(finding, source_file_content=source_file_content, repo_root=repo_root)  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit**

### Task 8.3: Shared patch test harness (class-definition-time method generation per B.3.5)

**Files:**
- Create: `tests/quality/rollup_v2/patch_harness.py`
- Create: `tests/quality/rollup_v2/fixtures/patches/.gitkeep` (already exists)

- [ ] **Step 1: Write the harness module**

```python
"""Shared patch generator golden-file test harness (per §A.1.5 + §B.3.5)."""
from __future__ import absolute_import

import json
import unittest
from pathlib import Path

from scripts.quality.rollup_v2.patches import dispatch
from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchResult

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "patches"


def _finding_from_json(data: dict) -> Finding:
    # Build a Finding from a fixture JSON dict. Required fields only; others default.
    # Subclasses can override in the JSON file for specific tests.
    # Implementation reads the JSON and calls Finding(...) with all required fields.
    raise NotImplementedError("fill in during Task 8.3")


class PatchGeneratorGoldenTests(unittest.TestCase):
    """Parametrized tests — methods attached dynamically at module load time."""
    pass


def _make_golden_test(category: str, case_name: str, fixture_dir: Path):
    def test_method(self):
        input_path = fixture_dir / f"{case_name}.input.py"
        finding_path = fixture_dir / f"{case_name}.finding.json"
        expected_diff_path = fixture_dir / f"{case_name}.expected.diff"

        source = input_path.read_text(encoding="utf-8")
        finding = _finding_from_json(json.loads(finding_path.read_text(encoding="utf-8")))
        expected_diff = expected_diff_path.read_text(encoding="utf-8")

        # Use a tmp dir as repo_root so path_safety passes
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            file_dir = (root / Path(finding.file).parent)
            file_dir.mkdir(parents=True, exist_ok=True)
            (root / finding.file).write_text(source, encoding="utf-8")
            result = dispatch(finding, source_file_content=source, repo_root=root)

        self.assertIsInstance(result, PatchResult)
        self.assertEqual(result.unified_diff.strip(), expected_diff.strip())  # type: ignore[union-attr]
        self.assertEqual(result.category, category)  # type: ignore[union-attr]

    return test_method


def _discover_and_attach():
    if not _FIXTURES_DIR.exists():
        return
    for category_dir in sorted(_FIXTURES_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        category = category_dir.name.replace("_", "-")
        input_files = sorted(category_dir.glob("*.input.py"))
        for input_file in input_files:
            case_name = input_file.name.replace(".input.py", "")
            method = _make_golden_test(category, case_name, category_dir)
            method.__name__ = f"test_{category.replace('-', '_')}_{case_name}"
            setattr(PatchGeneratorGoldenTests, method.__name__, method)


_discover_and_attach()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Implement `_finding_from_json()`** — read the JSON and construct a Finding with sensible defaults for fields not present in the fixture

- [ ] **Step 3: Write a smoke test fixture** (one simple case for `broad-except`) to confirm the harness discovers and runs tests. Create:
  - `tests/quality/rollup_v2/fixtures/patches/broad_except/smoke.input.py`
  - `tests/quality/rollup_v2/fixtures/patches/broad_except/smoke.finding.json`
  - `tests/quality/rollup_v2/fixtures/patches/broad_except/smoke.expected.diff`

- [ ] **Step 4: Run `python -m unittest tests.quality.rollup_v2.patch_harness -v`**

Expected: the `test_broad_except_smoke` method is discovered but FAILS (no generator registered yet — Task 9.1 will fix).

- [ ] **Step 5: Commit**
```bash
git add tests/quality/rollup_v2/patch_harness.py tests/quality/rollup_v2/fixtures/patches/broad_except/
git commit -m "feat(qrv2): patch generator golden-file test harness (§A.1.5 §B.3.5)"
```

### Task 8.4: Wire harness into standard test discovery

Ensure `python -m unittest discover -s tests` picks up the harness class.

- [ ] **Step 1: Add a marker test that imports the harness module** at `tests/quality/rollup_v2/test_harness_loader.py`:
```python
"""Ensure patch_harness.py is loaded during standard test discovery."""
import unittest

from tests.quality.rollup_v2.patch_harness import PatchGeneratorGoldenTests  # noqa: F401


class HarnessLoaderTest(unittest.TestCase):
    def test_harness_class_exists(self):
        self.assertIsNotNone(PatchGeneratorGoldenTests)
```

- [ ] **Step 2: Run, confirm the harness tests appear in `unittest discover` output**
- [ ] **Step 3: Commit**

---

## Phase 9 — Deterministic patch generators (30 generators, template-based)

### Task 9.1: `broad_except` generator (DETAILED — template for other 29)

**Files:**
- Create: `scripts/quality/rollup_v2/patches/broad_except.py`
- Update: `scripts/quality/rollup_v2/patches/__init__.py` (register in GENERATORS)
- Populate: `tests/quality/rollup_v2/fixtures/patches/broad_except/` with 3 golden cases

- [ ] **Step 1: Create 3 golden case fixtures**

`tests/quality/rollup_v2/fixtures/patches/broad_except/case_01.input.py`:
```python
def load_data(path):
    try:
        return parse(path)
    except Exception as e:
        log.warning("failed: %s", e)
        return None
```

`tests/quality/rollup_v2/fixtures/patches/broad_except/case_01.finding.json`:
```json
{
  "file": "load_data.py",
  "line": 4,
  "category": "broad-except",
  "category_group": "quality",
  "severity": "medium",
  "primary_message": "Catch a more specific exception"
}
```

`tests/quality/rollup_v2/fixtures/patches/broad_except/case_01.expected.diff`:
```diff
--- a/load_data.py
+++ b/load_data.py
@@ -1,5 +1,5 @@
 def load_data(path):
     try:
         return parse(path)
-    except Exception as e:
+    except (IOError, ValueError) as e:
         log.warning("failed: %s", e)
```

Create `case_02` and `case_03` with variations (bare `except:`, `except BaseException`).

- [ ] **Step 2: Implement generator**

`scripts/quality/rollup_v2/patches/broad_except.py`:
```python
"""Deterministic patch generator for `broad-except` category."""
from __future__ import absolute_import

import difflib
import re
from pathlib import Path

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "broad_except/1.0.0"
CATEGORY = "broad-except"

_EXCEPT_PATTERN = re.compile(
    r"^(\s*)except(\s+Exception|\s+BaseException|)(\s+as\s+\w+)?\s*:\s*$"
)


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Rewrite `except Exception` / bare except / `except BaseException` to a narrower tuple."""
    lines = source_file_content.splitlines(keepends=True)
    target_index = finding.line - 1
    if target_index < 0 or target_index >= len(lines):
        return PatchDeclined(
            reason_code="provider-data-insufficient",
            reason_text=f"line {finding.line} out of range for file with {len(lines)} lines",
            suggested_tier="skip",
        )
    original_line = lines[target_index]
    match = _EXCEPT_PATTERN.match(original_line)
    if not match:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text=f"line {finding.line} does not match known broad-except pattern",
            suggested_tier="llm-fallback",
        )
    indent = match.group(1)
    as_clause = match.group(3) or ""
    new_line = f"{indent}except (IOError, ValueError){as_clause}:\n"
    patched_lines = lines.copy()
    patched_lines[target_index] = new_line
    diff = "".join(difflib.unified_diff(
        lines,
        patched_lines,
        fromfile=f"a/{finding.file}",
        tofile=f"b/{finding.file}",
    ))
    return PatchResult(
        unified_diff=diff,
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
```

- [ ] **Step 3: Register in dispatcher**

Update `scripts/quality/rollup_v2/patches/__init__.py`:
```python
from scripts.quality.rollup_v2.patches import broad_except

GENERATORS: Mapping[str, object] = {
    "broad-except": broad_except,
}
```

- [ ] **Step 4: Run harness tests**
Run: `python -m unittest tests.quality.rollup_v2.patch_harness -v`
Expected: 3 `broad-except` tests pass.

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/patches/broad_except.py scripts/quality/rollup_v2/patches/__init__.py tests/quality/rollup_v2/fixtures/patches/broad_except/
git commit -m "feat(qrv2): broad-except deterministic patch generator (§5.1)"
```

### Tasks 9.2 - 9.30: Remaining 29 generators (condensed)

**Each follows Task 9.1's 5-step structure.** The category set below matches **design §5.1 verbatim** — 30 categories exactly. Order is ascending implementation complexity (simplest first). Any category listed in §5.1 as a "stub" or "detect only" is a declining generator that ALWAYS returns `PatchDeclined` with an appropriate `suggested_tier`; these still need a module file + registration + 1 test asserting the decline, which reserves the dispatch slot and documents the category.

| # | Category (§5.1) | Complexity | Approach |
|---|---|---|---|
| 9.2 | `trailing-whitespace` | trivial | regex: `[ \t]+$` per line → `` |
| 9.3 | `trailing-newline` | trivial | ensure file ends with exactly one `\n` |
| 9.4 | `bad-line-ending` | trivial | `\r\n` → `\n` |
| 9.5 | `tab-vs-space` | trivial | leading tabs → 4 spaces |
| 9.6 | `indent-mismatch` | easy | AST-aware re-indent; decline on ambiguous cases |
| 9.7 | `line-too-long` | easy | wrap at 100 cols via textwrap or ast-aware split |
| 9.8 | `quote-style` | easy | single quotes → double quotes (respect f-strings, escapes) |
| 9.9 | `spacing-convention` | easy | PEP 8 spacing fixes (`x=1` → `x = 1`, etc.) |
| 9.10 | `unused-import` | medium | AST parse, remove unused `import X` lines |
| 9.11 | `unused-variable` | medium | AST parse, remove unused assignments |
| 9.12 | `bare-raise` | easy | standalone `raise` outside `except` → `raise RuntimeError("…")` |
| 9.13 | `print-in-production` | easy | `print(` → `logging.info(` + add `import logging` |
| 9.14 | `assert-in-production` | easy | `assert X, msg` → `if not X: raise AssertionError(msg)` |
| 9.15 | `mutable-default` | medium | `def f(x=[]):` → `def f(x=None): x = [] if x is None else x` |
| 9.16 | `shadowed-builtin` | medium | rename suggestion; **declines → llm-fallback** when ambiguous |
| 9.17 | `hardcoded-secret` | trivial | replace literal with `os.environ["…"]` placeholder + add `# TODO: load from secret manager` |
| 9.18 | `wrong-import-order` | easy | isort-style sort (stdlib, third-party, first-party) |
| 9.19 | `dead-code` | medium | AST unreachable-block detection + delete |
| 9.20 | `missing-docstring` | easy | insert `"""TODO: document."""` as first statement |
| 9.21 | `todo-comment` | trivial | **no-op flag**, always declines with `suggested_tier="human-only"` |
| 9.22 | `insecure-random` | easy | `random.X` → `secrets.X`, rewrite imports |
| 9.23 | `weak-crypto` | easy | `hashlib.md5(` / `hashlib.sha1(` → `hashlib.sha256(` |
| 9.24 | `naming-convention` | easy | **declines → llm-fallback** (rename suggestion only; the actual rename is a multi-file refactor that belongs to tier 2) |
| 9.25 | `open-redirect` | medium | **declines → llm-fallback** (validate hint only) |
| 9.26 | `command-injection` | medium | **declines → llm-fallback** (sanitize hint only) |
| 9.27 | `cyclic-import` | trivial | **detect only** — always `PatchDeclined(reason_code="cross-file-change", suggested_tier="human-only")` |
| 9.28 | `duplicate-code` | trivial | **declines → llm-fallback** (extract-function stub — multi-file-capable refactor belongs to tier 2) |
| 9.29 | `too-long` | medium | **declines → llm-fallback** (extract-method stub) |
| 9.30 | `too-complex` | medium | **declines → llm-fallback** (extract-method stub) |

**Total**: 30 categories (Task 9.1 `broad-except` + Tasks 9.2-9.30 = 30) — matches design §5.1 exactly. 19 produce deterministic patches; 11 always decline (8 to `llm-fallback`, 2 to `human-only`, 1 to `skip`). Declines reserve dispatch slots and document categories for the tier-2 LLM fallback.

**For each task**:
- Write at least 2 golden-case fixtures for "generates a patch" categories
- Write 1 test for "always declines" categories
- Implement the module
- Register in `GENERATORS` dict
- Run harness
- Commit with message `feat(qrv2): <category> deterministic patch generator (§5.1)`

---

## Phase 10 — LLM fallback scaffold (§5.2 + §A.2.1 + §A.2.2 + §B.3.12, ~5 tasks)

### Task 10.1: HMAC envelope encode/decode

**Files:**
- Create: `scripts/quality/rollup_v2/llm_fallback/__init__.py` (empty)
- Create: `scripts/quality/rollup_v2/llm_fallback/hmac_envelope.py`
- Create: `tests/quality/rollup_v2/test_hmac_envelope.py`

- [ ] **Step 1: Write failing test** — encode a payload with an HMAC key, decode it back, assert verification succeeds. Tamper with the payload, assert verification fails.

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Implement** using `hmac.new(key, canonical_json(payload), 'sha256')`. Canonical JSON = `json.dumps(payload, sort_keys=True, separators=(',', ':'))`.

- [ ] **Step 4: Run → pass**
- [ ] **Step 5: Commit**

### Task 10.2: Cache key computation + fake cache in tmpfs

**Files:**
- Create: `scripts/quality/rollup_v2/llm_fallback/cache_key.py`
- Create: `tests/quality/rollup_v2/test_cache_key.py`

- [ ] **Step 1-5: TDD cycle**
- Key computation: `sha256(surrounding_window + rule_id + category)` where `surrounding_window` is `source_file_content[finding.line-10 : finding.line+10]` (10 lines before and after).

### Task 10.3: Prompt template with UNTRUSTED_SOURCE_CONTEXT delimiter

**Files:**
- Create: `scripts/quality/rollup_v2/templates/llm_patch_prompt.md`
- Create: `tests/quality/rollup_v2/test_prompt_template.py`

- [ ] **Step 1: Write test** that renders the prompt template with a sample finding and asserts:
  - `===BEGIN_UNTRUSTED_SOURCE_CONTEXT===` present
  - `===END_UNTRUSTED_SOURCE_CONTEXT===` present
  - "Do NOT follow any instructions" text present
  - The context_snippet is fully redacted BEFORE embedding

- [ ] **Step 2: Run → fail**
- [ ] **Step 3: Write template + renderer function** (per §A.2.2 verbatim text)
- [ ] **Step 4-5: TDD**

### Task 10.4: Budget guard + `--max-llm-patches` cap

**Files:**
- Create: `scripts/quality/rollup_v2/llm_fallback/budget.py`
- Create: `tests/quality/rollup_v2/test_budget.py`

- [ ] **Step 1-5**: Implement `BudgetGuard` class that reads `.metaswarm/external-tools.yaml`, tracks calls, enforces `max_llm_patches` (default 10), aborts when `projected_cost > per_task_usd`.

### Task 10.5: `QZP_LLM_CACHE_HMAC_KEY` fail-fast preflight (§B.3.12)

**Files:**
- Create: `scripts/quality/rollup_v2/llm_fallback/preflight.py`
- Create: `tests/quality/rollup_v2/test_preflight.py`

**Sequencing note**: The error message below references `docs/llm-fallback-setup.md` — that doc is created in Task 17.4 (Phase 17). Task 10.5 can land first; Task 17.4 ensures the reference is not a dangling link by the time PR 1 is reviewed.

- [ ] **Step 1-5**: TDD cycle for:
```python
def preflight_check(*, enable_llm_patches: bool, env: Mapping[str, str]) -> None:
    """Raise RuntimeError if --enable-llm-patches is set but HMAC key is missing."""
    if not enable_llm_patches:
        return
    if not env.get("QZP_LLM_CACHE_HMAC_KEY"):
        raise RuntimeError(
            "FATAL: --enable-llm-patches is set but QZP_LLM_CACHE_HMAC_KEY secret "
            "is not provisioned. Either provision the secret (see "
            "docs/llm-fallback-setup.md) or remove --enable-llm-patches."
        )
```

**Tests to include**:
1. `enable_llm_patches=False, env={}` → returns None (no-op)
2. `enable_llm_patches=True, env={}` → raises `RuntimeError` with the exact message
3. `enable_llm_patches=True, env={"QZP_LLM_CACHE_HMAC_KEY": "x"}` → returns None
4. `enable_llm_patches=True, env={"QZP_LLM_CACHE_HMAC_KEY": ""}` → raises (empty string is not a valid secret)

---

## Phase 11 — Multi-view renderer (§4.1 + §A.1.1 + §A.1.2 + §B.3.9 + §B.3.15 + §B.3.16, ~6 tasks)

### Task 11.1: Renderer skeleton with empty-state (§A.1.2)

**Files:**
- Create: `scripts/quality/rollup_v2/renderer.py`
- Create: `tests/quality/rollup_v2/test_renderer_empty.py`

- [ ] **Step 1: Write failing test** — render 0 findings, assert output contains `✅ **All gates passed — 0 findings**` and `Generated at`.

- [ ] **Step 2: Run → fail**

- [ ] **Step 3: Implement `render_markdown(payload: dict) -> str`** with a dispatch on `total_findings == 0`.

- [ ] **Step 4-5: TDD**

### Task 11.2: By-file default view (§A.1.1)

**Files:**
- Modify: `scripts/quality/rollup_v2/renderer.py`
- Create: `tests/quality/rollup_v2/test_renderer_by_file.py`

- [ ] **Step 1: Write test** — render 3 findings across 2 files, assert:
  - Each file has `### \`path\` (N findings)` heading
  - Each finding has `#### 🔴|🟡|⚪ line N · \`category\` · **severity** · M providers`
  - Providers rendered as `[Provider1](url) · [Provider2](url)`
  - Patches always visible in fenced diff blocks (no `<details>` wrapper)
  - "No automated patch available" when `patch is None`

- [ ] **Step 2-5: TDD cycle**

### Task 11.3: Provider summary table (§4.1)

- [ ] **Step 1-5: TDD** — render provider summary table with totals and per-severity counts

### Task 11.4: Alternate views as top-level `<details>` sections (§A.1.1)

- [ ] **Step 1-5: TDD** — by-provider, by-severity, autofixable-only alt views. Test asserts they are at top-level (not nested inside list items).

### Task 11.5: High-volume truncation (>200 findings, >60000 chars) (§A.1.2 + §B.3.9 + §B.3.15)

- [ ] **Step 1-5**: TDD test asserts:
  - 250+ findings: top 20 files visible, rest in a `<details><summary>N additional files</summary>` at the end
  - Tie-break: `(finding_count DESC, file_path ASC)` — deterministic
  - >60000 chars: fallback to summary comment with the verbatim §B.3.9 sentence

### Task 11.6: Footer with doc links (§A.1.1 + §B.3.8 + §B.3.10)

- [ ] **Step 1-5**: TDD test asserts the footer with:
  ```
  ℹ️ [How to read this report](docs/quality-rollup-guide.md) · [Schema v1](docs/schemas/qzp-finding-v1.md) · [Report a format issue](...)
  ```

### Task 11.7: Writer belt-and-suspenders redaction (§B.1.2 — MANDATORY)

Per design §B.1.2, the markdown writer MUST re-apply `redact_secrets()` as a belt-and-suspenders layer. If a normalizer misses a secret (e.g., due to a subclass bypass), the writer catches it. This task adds the writer-side redaction pass + its dedicated test.

**Files:**
- Modify: `scripts/quality/rollup_v2/renderer.py`
- Create: `tests/quality/rollup_v2/test_renderer_redaction.py`

- [ ] **Step 1: Write failing test — construct a Finding with an UNREDACTED secret in primary_message and context_snippet, render it, assert the output contains `<REDACTED>` and does NOT contain the raw secret**

```python
"""Writer-side belt-and-suspenders redaction test (per §B.1.2)."""
from __future__ import absolute_import

import sys
import unittest
from pathlib import Path

if str(Path(__file__).resolve().parents[3]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.quality.rollup_v2.renderer import render_markdown
from scripts.quality.rollup_v2.schema.corroborator import Corroborator
from scripts.quality.rollup_v2.schema.finding import (
    CATEGORY_GROUP_QUALITY,
    SCHEMA_VERSION,
    Finding,
)


class WriterRedactionTests(unittest.TestCase):
    def _leaky_finding(self) -> Finding:
        # Construct a finding as if a buggy normalizer forgot to redact.
        leaked_secret = 'PASSWORD = "superlongsecretpasswordvalue"'
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
            context_snippet=leaked_secret,   # deliberately unredacted
            source_file_hash="sha256:x",
            cwe=None,
            autofixable=False,
            tags=(),
            patch_error=None,
        )

    def test_writer_redacts_leaked_secret(self):
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
```

- [ ] **Step 2: Run → fail** (renderer doesn't call `redact_secrets` yet)

- [ ] **Step 3: Modify `render_markdown()` to pipe every user-content string (primary_message, context_snippet, corroborator.original_message, fix_hint) through `redact_secrets` before emitting the markdown**

```python
# In scripts/quality/rollup_v2/renderer.py
from scripts.quality.rollup_v2.redaction import redact_secrets

def _emit_field(value: str | None) -> str:
    """Belt-and-suspenders: redact every user-content string at write time."""
    if not value:
        return ""
    return redact_secrets(value)

# Use _emit_field(...) for every Finding.primary_message,
# Finding.context_snippet, Finding.fix_hint, and Corroborator.original_message
# reference in the rendered output.
```

- [ ] **Step 4: Run → pass**

- [ ] **Step 5: Commit**
```bash
git add scripts/quality/rollup_v2/renderer.py tests/quality/rollup_v2/test_renderer_redaction.py
git commit -m "feat(qrv2): writer belt-and-suspenders redaction (§B.1.2)"
```

---

## Phase 12 — Writer pipeline orchestration (§4.2 + §A.3.5, ~3 tasks)

### Task 12.1: Pipeline orchestrator module

**Files:**
- Create: `scripts/quality/rollup_v2/pipeline.py`
- Create: `tests/quality/rollup_v2/test_pipeline.py`

- [ ] **Step 1-5: TDD** — `run_pipeline(artifacts: dict[str, Any], repo_root: Path, output_dir: Path) -> PipelineResult`:
  1. Dispatch each artifact to its normalizer
  2. Collect Findings, normalizer_errors, security_drops
  3. Dedup + merge via Phase 7
  4. Dispatch each finding to patch generator via Phase 8
  5. **Derive `autofixable` field per §A.4.1**: after patch dispatch, for each finding set `autofixable = (finding.patch_source != "none")` via `dataclasses.replace`. This is NOT done at Finding construction time — it's derived at the pipeline level because `patch_source` only becomes known after the dispatcher runs.
  6. Emit canonical.json via `common.write_report`
  7. Render markdown via Phase 11
  8. Write rollup.md via `common.write_report`

**Mandatory test for `autofixable` derivation (§A.4.1)**:

```python
def test_autofixable_is_derived_from_patch_source(self):
    # Build 3 findings: one with deterministic patch, one with LLM patch, one with no patch
    inputs = [
        _make_finding(category="broad-except", patch_source="none"),
        _make_finding(category="broad-except", patch_source="deterministic"),
        _make_finding(category="broad-except", patch_source="llm"),
    ]
    # Run the pipeline derivation step (can be a standalone helper)
    out = _derive_autofixable(inputs)
    self.assertFalse(out[0].autofixable)   # patch_source == "none"
    self.assertTrue(out[1].autofixable)    # deterministic
    self.assertTrue(out[2].autofixable)    # llm
```

Factor the derivation into a tiny helper `_derive_autofixable(findings: list[Finding]) -> list[Finding]` so the test is trivial. The helper is called once per pipeline run right after patch dispatch.

### Task 12.2: Integration with `common.write_report` (§A.3.5)

- [ ] **Step 1-5**: Study `scripts/quality/common.py::write_report` signature. Build a `ReportSpec`-compatible payload. Reuse the helper for both `canonical.json` and `rollup.md`.

### Task 12.3: Pipeline error boundary test (§A.6)

- [ ] **Step 1-5**: TDD — pipeline with a malformed Codacy artifact → rollup still produced, `normalizer_errors[]` populated, banner in markdown.

---

## Phase 13 — Platform dogfood: wire rollup_v2 into platform's own CI (~2 tasks)

### Task 13.1: Add rollup_v2 invocation to existing workflow

**Files:**
- Modify: `.github/workflows/quality-zero-platform.yml` (or similar — grep for the platform's own rollup invocation)

- [ ] **Step 1: Identify the current rollup invocation step**

Run: `grep -rn "build_quality_rollup" .github/workflows/`

- [ ] **Step 2: Add a new step AFTER the existing one** that runs `python scripts/quality/rollup_v2/__main__.py --help` (dry-run for now — Task 13.2 wires it fully)

- [ ] **Step 3: Test the workflow passes in CI** (requires pushing to the branch; for local testing, run the command directly)

- [ ] **Step 4: Commit**

### Task 13.2: CLI entrypoint + full pipeline wiring

**Files:**
- Create: `scripts/quality/rollup_v2/__main__.py`
- Create: `tests/quality/rollup_v2/test_main.py`

- [ ] **Step 1-5**: TDD — `__main__.py` parses args (`--artifacts-dir`, `--output-dir`, `--enable-llm-patches`), runs `pipeline.run_pipeline()`, writes outputs.

---

## Phase 14 — Legacy wrapper for `build_quality_rollup.py` (§A.3.4, ~3 tasks)

### Task 14.1: Rewrite `build_quality_rollup.py` as thin wrapper

**Files:**
- Modify: `scripts/quality/build_quality_rollup.py`

- [ ] **Step 1: Study the existing public API** — list all exported functions/classes that external callers depend on

- [ ] **Step 2: Replace module body with thin wrapper** that imports from `rollup_v2` and re-exports the old names. Add `# TODO(qrv2-pr4): remove this wrapper after all downstream consumers are migrated to rollup_v2.*`

- [ ] **Step 3: Run all existing tests** — expect `test_quality_rollup.py` and `test_quality_rollup_extra.py` to still pass against the wrapper

- [ ] **Step 4: Fix any test breakage** by either updating the wrapper or updating the test

- [ ] **Step 5: Commit**

### Task 14.2: Update existing tests to use new module paths where appropriate

**Files:**
- Modify: `tests/test_quality_rollup.py`, `tests/test_quality_rollup_extra.py`

- [ ] **Step 1-5**: Audit each test, determine if it tests wrapper-level behavior (keep in existing file) or deep internal behavior (migrate to `tests/quality/rollup_v2/`)

### Task 14.3: Verify `post_pr_quality_comment.py` still works

**Files:**
- Read: `scripts/quality/post_pr_quality_comment.py`

- [ ] **Step 1: Smoke-test** — invoke the script with the wrapper present, confirm no import errors. No code changes expected unless imports broke.

---

## Phase 15 — Lane key pre-reservation (§A.5, ~2 tasks)

### Task 15.1: Add Semgrep/CodeQL/Chromatic/Applitools to LANE_CONTEXTS

**Files:**
- Modify: `scripts/quality/rollup_v2/pipeline.py` (or wherever `LANE_CONTEXTS` lives)

- [ ] **Step 1-5**: Add entries:
```python
LANE_CONTEXTS = {
    # ... existing ...
    "semgrep": "Semgrep Zero",      # populated in PR 3
    "codeql": "CodeQL Zero",        # populated in PR 3
    "chromatic": "Chromatic Zero",  # populated in PR 3
    "applitools": "Applitools Zero", # populated in PR 3
}
LANE_ARTIFACT_PATHS = {
    # ... existing ...
    "semgrep": "semgrep-zero/semgrep.sarif",
    "codeql": "codeql-zero/codeql.sarif",
    "chromatic": "chromatic-zero/chromatic.json",
    "applitools": "applitools-zero/applitools.json",
}
```

### Task 15.2: Missing-artifact placeholder handling

- [ ] **Step 1-5**: TDD — when a reserved lane's artifact is missing, rollup marks the lane as `"status": "not-configured"` and renders a grey placeholder in the summary table. Does not fail the rollup.

---

## Phase 16 — Golden fixtures (§A.9 + §B.3.7 + §B.3.16, ~3 tasks)

### Task 16.1: `golden_42_findings.md` — default case (§A.9.2)

**Files:**
- Create: `tests/quality/rollup_v2/fixtures/renderer/golden_42_findings_input.json`
- Create: `tests/quality/rollup_v2/fixtures/renderer/golden_42_findings.md`
- Create: `tests/quality/rollup_v2/test_renderer_golden_42.py`

- [ ] **Step 1: Handcraft 42 findings** across 5 files with varied categories/severities/providers/patch_source values

- [ ] **Step 2: Render + save the expected output** as `golden_42_findings.md`

- [ ] **Step 3: Write a test** that renders the input and asserts byte-for-byte equality with the golden file

- [ ] **Step 4: Run to pass**

- [ ] **Step 5: Commit**

### Task 16.2: `golden_250_findings.md` — high-volume case (§B.3.7)

Same structure — 250 findings exercising truncation + artifact fallback.

### Task 16.3: `golden_nonascii.md` — Unicode safety (§B.3.16)

At least 1 finding with non-ASCII file path (`café.py`) and 1 with non-ASCII message (`日本語`).

---

## Phase 17 — Documentation (§A.9.1 + §A.9.5 + §B.3.8 + §B.3.10, ~3 tasks)

### Task 17.1: `docs/quality-rollup-guide.md` stub (§B.3.8)

**Files:**
- Create: `docs/quality-rollup-guide.md`

- [ ] **Step 1: Write a ~50-line stub** covering: what the rollup is, how to read it, severity meanings, what to do with a patch, link to schema doc, link to issue tracker

- [ ] **Step 2: Verify the A.1.1 footer link points at this file**
- [ ] **Step 3: Commit**

### Task 17.2: `docs/schemas/qzp-finding-v1.md` (§A.9.1)

**Files:**
- Create: `docs/schemas/qzp-finding-v1.md`

- [ ] **Step 1: Document every Finding field** with type, nullability, meaning, example. Include the §B.3.10 schema migration policy ("consumers MUST check the MAJOR portion and fail closed on unrecognized majors").

- [ ] **Step 2: Commit**

### Task 17.3: `docs/schemas/qzp-finding-v1.json` — JSON Schema (§A.9.5)

**Files:**
- Create: `docs/schemas/qzp-finding-v1.json`
- Create: `tests/quality/rollup_v2/test_json_schema.py`

- [ ] **Step 1: Write JSON Schema** (draft-2020-12) matching the Finding dataclass

- [ ] **Step 2: Write test** that generates a canonical.json via the pipeline and validates it against the schema using `jsonschema` library. (`jsonschema` is added to `requirements-dev.txt` in Task 0.4.)

- [ ] **Step 3-5: TDD**

### Task 17.4: `docs/llm-fallback-setup.md` stub (referenced by §B.3.12 preflight error)

**Context**: Task 10.5's `preflight_check()` error message tells the user "see docs/llm-fallback-setup.md" when `QZP_LLM_CACHE_HMAC_KEY` is missing. That file must exist (even as a stub) so the error message is actionable.

**Files:**
- Create: `docs/llm-fallback-setup.md`

- [ ] **Step 1: Write a minimal stub** (~30-50 lines) covering:
  - What the LLM fallback scaffold is (§5.2 + §A.2.1)
  - Why `QZP_LLM_CACHE_HMAC_KEY` exists (HMAC signing of cached patches — §A.2.1)
  - How to provision it as a GitHub Actions repo secret: `gh secret set QZP_LLM_CACHE_HMAC_KEY --body "$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"`
  - How to rotate it (bump `cache_version` + rotate secret; old cache entries expire via GHA's 7-day TTL)
  - The opt-in flag: `--enable-llm-patches` (default OFF — the fallback never runs in CI unless explicitly enabled)
  - The budget cap: `--max-llm-patches` (default 10) and `QZP_LLM_BUDGET_USD` (read from `.metaswarm/external-tools.yaml`)
  - Link back to the design doc sections

- [ ] **Step 2: Cross-check** that `scripts/quality/rollup_v2/llm_fallback/preflight.py` error message points at this exact path (`docs/llm-fallback-setup.md`)

- [ ] **Step 3: Commit**

```bash
git add docs/llm-fallback-setup.md
git commit -m "docs(qrv2): add llm-fallback-setup.md stub (§B.3.12 preflight target)"
```

---

## Phase 18 — Final integration + polish (~3 tasks)

### Task 18.1: Coverage verification

- [ ] **Step 1: Run the coverage gate on `rollup_v2/`**

Run:
```bash
python -m coverage run --source=scripts/quality/rollup_v2 -m unittest discover -s tests -p 'test_*.py'
python -m coverage report --fail-under=100 --show-missing
```

Expected: 100% coverage. If any line is uncovered, add a test or `# pragma: no cover` with justification.

- [ ] **Step 2: Fix any coverage gaps**
- [ ] **Step 3: Commit the fixes**

### Task 18.2: End-to-end smoke test

- [ ] **Step 1: Create `tests/quality/rollup_v2/test_e2e_smoke.py`** that runs the entire pipeline (from a fixture artifact set through to canonical.json + rollup.md) and asserts success

- [ ] **Step 2: Run `bash scripts/verify`** to confirm the pre-push hook will pass

- [ ] **Step 3: Commit**

### Task 18.3: Pre-PR `/self-reflect` + commit knowledge updates

(This is the task that triggers CLAUDE.md's pre-PR knowledge capture rule.)

- [ ] **Step 1: Invoke `/self-reflect`** to extract learnings into `.beads/knowledge/`

- [ ] **Step 2: Review the knowledge files and commit**

- [ ] **Step 3: Confirm the commit is atomic with the PR code**

---

## Self-review checklist

Before marking this plan complete:

- [ ] **Spec coverage**: every design doc section (§1-13 + A + B) that affects PR 1 scope has at least one task implementing it
- [ ] **Placeholder scan**: no "TBD", "TODO", "implement later", "similar to Task N" phrases in this plan
- [ ] **Type consistency**: `Finding`, `Corroborator`, `PatchResult`, `PatchDeclined` field names match across tasks
- [ ] **File path consistency**: `scripts/quality/rollup_v2/...` and `tests/quality/rollup_v2/...` paths are used everywhere
- [ ] **Test harness fits unittest convention**: no pytest references
- [ ] **Dedup + merge**: Phase 7 covered
- [ ] **LLM fallback scaffold**: Phase 10 covered with HMAC + budget guard
- [ ] **Redaction + path validation**: Phase 2 + Phase 3 covered
- [ ] **BaseNormalizer**: Phase 5 covered with @final + error boundaries
- [ ] **9 normalizers**: Phase 6 covered (template + 8 condensed)
- [ ] **30 patch generators**: Phase 9 covered (template + 29 condensed)
- [ ] **Multi-view renderer**: Phase 11 covered
- [ ] **Pipeline orchestrator**: Phase 12 covered
- [ ] **Platform dogfood**: Phase 13 covered
- [ ] **Legacy wrapper**: Phase 14 covered
- [ ] **Lane pre-reservation**: Phase 15 covered
- [ ] **Golden fixtures**: Phase 16 covered (3 files)
- [ ] **Docs**: Phase 17 covered (3 files)
- [ ] **Final integration + coverage**: Phase 18 covered

## Task count summary

| Phase | Tasks | Notes |
|---|---|---|
| Pre-flight | 3 | P.1-P.3 |
| 0 Scaffold | 3 | package + test dirs + beads pointer |
| 1 Core Types | 5 | severity, providers, Corroborator, Finding, patch_error |
| 2 Redaction | 3 | core + extended patterns + (deferred integration) |
| 3 Path validation | 2 | strengthen helper + rollup_v2 wrapper |
| 4 Taxonomy | 3 | YAML seeds + loader + unmapped collector |
| 5 BaseNormalizer | 2 | abstract base + integration test |
| 6 Normalizers | 9 | 1 detailed + 8 condensed |
| 7 Dedup | 3 | dedup + stable ids + edge cases |
| 8 Patch infra | 4 | types + dispatcher + harness + loader |
| 9 Patch generators | 30 | 1 detailed + 29 condensed |
| 10 LLM fallback | 5 | HMAC + cache key + prompt + budget + preflight |
| 11 Renderer | 6 | empty + by-file + summary + alt views + truncation + footer |
| 12 Pipeline | 3 | orchestrator + write_report reuse + error boundary |
| 13 Dogfood | 2 | workflow wire + __main__ |
| 14 Legacy wrapper | 3 | wrapper + tests + post_pr verify |
| 15 Lane pre-reservation | 2 | entries + placeholder handling |
| 16 Golden fixtures | 3 | 42 + 250 + non-ASCII |
| 17 Documentation | 3 | guide stub + schema md + JSON schema |
| 18 Final integration | 3 | coverage + smoke + self-reflect |
| **TOTAL** | **97** | tasks; ~485 steps |

## Execution handoff

**Plan complete and saved to `docs/plans/2026-04-09-quality-rollup-v2-pr1-plan.md`.**

Per CLAUDE.md "Execution Method Choice" rule, after the `plan-review-gate` passes, the user (or the orchestrator in autonomous mode) will select one of:

1. **Metaswarm orchestrated execution** — 4-phase loop per work unit with adversarial review, fresh reviewers, coverage enforcement. Most thorough, highest token cost.
2. **Subagent-driven development** (`superpowers:subagent-driven-development`) — Fresh subagent per task with review between tasks. Fast, lighter-weight.
3. **Parallel session** (`superpowers:executing-plans`) — Separate session with batch checkpoints.

Next step: `plan-review-gate` (3 adversarial reviewers: Feasibility, Completeness, Scope & Alignment) before this plan can be executed.
