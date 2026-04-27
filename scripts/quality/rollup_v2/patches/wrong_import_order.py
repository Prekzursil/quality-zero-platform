"""Deterministic patch generator for `wrong-import-order` category."""

from __future__ import absolute_import

import difflib
import re
from pathlib import Path
from typing import List, Optional, Tuple

from scripts.quality.rollup_v2.schema.finding import Finding
from scripts.quality.rollup_v2.schema.patch import PatchDeclined, PatchResult

GENERATOR_VERSION = "wrong_import_order/1.0.0"
CATEGORY = "wrong-import-order"

_IMPORT_LINE = re.compile(r"^\s*(import\s+\S+|from\s+\S+\s+import\s+.+)")

# Known stdlib top-level modules (subset for classification)
_STDLIB_PREFIXES = frozenset(
    {
        "abc",
        "argparse",
        "ast",
        "asyncio",
        "base64",
        "bisect",
        "builtins",
        "calendar",
        "codecs",
        "collections",
        "configparser",
        "contextlib",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "email",
        "enum",
        "errno",
        "fnmatch",
        "fractions",
        "functools",
        "gc",
        "getpass",
        "glob",
        "gzip",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "http",
        "importlib",
        "inspect",
        "io",
        "itertools",
        "json",
        "keyword",
        "linecache",
        "locale",
        "logging",
        "math",
        "mimetypes",
        "multiprocessing",
        "numbers",
        "operator",
        "os",
        "pathlib",
        "platform",
        "pprint",
        "profile",
        "queue",
        "random",
        "re",
        "secrets",
        "select",
        "shlex",
        "shutil",
        "signal",
        "socket",
        "sqlite3",
        "ssl",
        "stat",
        "string",
        "struct",
        "subprocess",
        "sys",
        "sysconfig",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "timeit",
        "trace",
        "traceback",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uuid",
        "warnings",
        "weakref",
        "xml",
        "zipfile",
        "zipimport",
        "zlib",
        "__future__",
    }
)


def _classify_import(line: str) -> int:
    """Classify an import line: 0=future, 1=stdlib, 2=third-party, 3=first-party."""
    stripped = line.strip()
    if "from __future__" in stripped:  # pragma: no cover -- future imports are rare in findings
        return 0
    # Extract the module name
    if stripped.startswith("from "):
        module = stripped.split()[1].split(".")[0]
    elif stripped.startswith("import "):
        module = stripped.split()[1].split(".")[0].split(",")[0]
    else:  # pragma: no cover -- non-import lines are filtered before classify is called
        return 3
    if module in _STDLIB_PREFIXES:
        return 1
    return 2  # Assume third-party (conservative)


def _find_import_block(lines: List[str]) -> Optional[Tuple[int, int]]:
    """Find the contiguous import block at the top of the file.

    Returns (start, end) line indices or None if no imports found.
    """
    import_start = None
    import_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _IMPORT_LINE.match(line):
            if import_start is None:
                import_start = i
            import_end = i + 1
        elif stripped == "" and import_start is not None or stripped.startswith("#") and import_start is None:
            continue
        elif import_start is not None:
            break
    if import_start is None or import_end is None:
        return None
    return (import_start, import_end)


def _build_sorted_block(import_lines: List[str]) -> List[str]:
    """Sort import lines by group and build a block with group separators."""
    sorted_imports = sorted(import_lines, key=lambda l: (_classify_import(l), l.strip()))
    grouped: List[str] = []
    prev_group = -1
    for imp in sorted_imports:
        group = _classify_import(imp)
        if prev_group >= 0 and group != prev_group:
            grouped.append("\n")
        grouped.append(imp if imp.endswith("\n") else imp + "\n")
        prev_group = group
    return grouped


def generate(
    finding: Finding,
    source_file_content: str,
    repo_root: Path,
) -> PatchResult | PatchDeclined | None:
    """Sort imports in isort-style order: future, stdlib, third-party, first-party."""
    lines = source_file_content.splitlines(keepends=True)

    block = _find_import_block(lines)
    if block is None:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="no import block found",
            suggested_tier="skip",
        )

    import_start, import_end = block
    import_lines = [lines[i] for i in range(import_start, import_end) if lines[i].strip()]
    grouped = _build_sorted_block(import_lines)

    patched_lines = lines[:import_start] + grouped + lines[import_end:]
    if patched_lines == lines:
        return PatchDeclined(
            reason_code="ambiguous-fix",
            reason_text="imports already in correct order",
            suggested_tier="skip",
        )

    diff = "".join(
        difflib.unified_diff(
            lines,
            patched_lines,
            fromfile=f"a/{finding.file}",
            tofile=f"b/{finding.file}",
        )
    )
    return PatchResult(
        unified_diff=diff,
        confidence="medium",
        category=CATEGORY,
        generator_version=GENERATOR_VERSION,
        touches_files=frozenset({Path(finding.file)}),
    )
