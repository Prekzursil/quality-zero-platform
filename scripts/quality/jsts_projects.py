#!/usr/bin/env python3
"""Plan which JS/TS projects gate 3 must measure.

The jsts coverage lane used to measure ONLY the root project whenever a root
``package.json`` existed: nested discovery ran only when the root had none. So a
nested project (``frontend/``, ``packages/ui``) with its own tests and its own
coverage script was never run, and a nested project with tests and NO coverage
script was never flagged - while the lane printed PASS.

This module turns ``git ls-files`` output into the full project plan:

* every ``package.json`` outside vendored / build trees is a project;
* every JS/TS test file is attributed to its DEEPEST enclosing project. A test
  file is a ``*.test.*`` / ``*.spec.*`` file, anything under ``__tests__/``, or
  (Mocha-style layouts) a JS/TS file under a ``test/`` or ``tests/`` directory
  inside a project;
* a project that the root ``package.json`` ``workspaces`` (or a root
  ``pnpm-workspace.yaml``) declares is marked as a workspace member, so the
  lane can say why a root workspace run does not count for it.

Output is one tab-separated line per project, the root first and then the rest
sorted by path::

    <dir>\t<has_tests: yes|no>\t<workspace_member: yes|no>

``has_tests`` is the project's OWN test files. Every project with tests is
measured by its OWN coverage lane, workspace members included: a root
workspace run can show that a member's code was covered, never that the
member's own tests ran (a root ``jest src --coverage`` that merely imports the
member covers its code and runs none of its tests), and a coverage report on
disk may predate the run. A member therefore needs its own ``test:coverage``.

``*.test.*`` / ``*.spec.*`` / ``__tests__`` files that belong to no project at
all are reported on stderr and, with ``--orphans-out``, written to a file so the
lane can fail on them: no coverage lane ever runs them. A file under a
``test/`` directory outside every project is not an orphan: outside a JS/TS
project that name says nothing about JavaScript tests.

The module is pure standard library because the lane runs it with the runner's
bare ``python3``.
"""

from __future__ import absolute_import

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

VENDORED = re.compile(r"(^|/)(node_modules|dist|out|build|\.venv|vendor)/")
"""Trees that are never first-party projects or first-party tests."""

JSTS_TEST = re.compile(r"(\.(test|spec)\.([cm]?[jt]s|[jt]sx)$)|((^|/)__tests__/.+\.([cm]?[jt]s|[jt]sx)$)")
"""A JS/TS test file by name: ``*.test.*`` / ``*.spec.*`` or anything under ``__tests__/``."""

JSTS_TEST_DIR = re.compile(r"(^|/)tests?/.+\.([cm]?[jt]s|[jt]sx)$")
"""A JS/TS file under a ``test/`` or ``tests/`` directory: Mocha-style layouts (``test/smoke.js``)."""

ROOT = "."

PlanRow = Tuple[str, bool, bool]


def project_dirs(paths: Iterable[str]) -> List[str]:
    """Return every project directory: the directory of each first-party package.json."""
    dirs = set()
    for path in paths:
        if VENDORED.search(path):
            continue
        if path == "package.json":
            dirs.add(ROOT)
        elif path.endswith("/package.json"):
            dirs.add(path[: -len("/package.json")])
    return sorted(dirs, key=lambda item: (item != ROOT, item))


def owning_project(path: str, dirs: Sequence[str]) -> Optional[str]:
    """Return the DEEPEST project directory that contains ``path``, if any."""
    best: Optional[str] = None
    for candidate in dirs:
        inside = candidate == ROOT or path.startswith(candidate + "/")
        if inside and (best is None or best == ROOT or len(candidate) > len(best)):
            best = candidate
    return best


def _pnpm_globs(text: str) -> List[str]:
    """Read the ``packages:`` list of a ``pnpm-workspace.yaml`` without a YAML parser."""
    globs: List[str] = []
    in_packages = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t", "-")):
            in_packages = line.strip() == "packages:"
            continue
        item = line.strip()
        if in_packages and item.startswith("-"):
            value = item[1:].strip().strip("'\"")
            if value:
                globs.append(value)
    return globs


def workspace_globs(root_package_json: str = "", pnpm_workspace: str = "") -> List[str]:
    """Return the workspace globs a root package.json / pnpm-workspace.yaml declares."""
    globs: List[str] = []
    if root_package_json:
        try:
            document = json.loads(root_package_json)
        except ValueError:
            document = {}
        workspaces = document.get("workspaces") if isinstance(document, dict) else None
        if isinstance(workspaces, dict):
            workspaces = workspaces.get("packages")
        if isinstance(workspaces, list):
            globs.extend(str(item) for item in workspaces if isinstance(item, str) and item.strip())
    if pnpm_workspace:
        globs.extend(_pnpm_globs(pnpm_workspace))
    return globs


def _match_segments(parts: Sequence[str], patterns: Sequence[str]) -> bool:
    """Match path segments against glob segments; ``**`` spans any number of them."""
    if not patterns:
        return not parts
    head, rest = patterns[0], patterns[1:]
    if head == "**":
        return any(_match_segments(parts[index:], rest) for index in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], head) and _match_segments(parts[1:], rest)


def glob_match(directory: str, pattern: str) -> bool:
    """Workspace-glob semantics: ``*`` stays inside ONE path segment, ``**`` crosses them.

    ``fnmatch`` alone lets ``*`` match ``/``, so ``packages/*`` would also claim
    ``packages/a/b`` - a nested project npm/pnpm would NOT treat as a member,
    which the lane would then pass as "measured by the root" without measuring.
    """
    return _match_segments(directory.split("/"), pattern.split("/"))


def is_workspace_member(directory: str, globs: Sequence[str]) -> bool:
    """True when ``directory`` matches an include glob and no ``!``-exclusion glob."""
    if directory == ROOT:
        return False
    included = False
    for glob in globs:
        pattern = glob.strip().rstrip("/")
        excluded = pattern.startswith("!")
        if excluded:
            pattern = pattern[1:]
        if pattern.startswith("./"):
            pattern = pattern[2:]
        if not glob_match(directory, pattern):
            continue
        if excluded:
            return False
        included = True
    return included


def plan(
    paths: Sequence[str],
    *,
    root_package_json: str = "",
    pnpm_workspace: str = "",
) -> Tuple[List[PlanRow], List[str]]:
    """Return ``(rows, orphan_tests)`` for the tracked file list ``paths``.

    ``has_tests`` is the project's OWN test files only: a workspace root does
    not inherit its members' tests, and an aggregator root without tests of its
    own has nothing to measure.
    """
    dirs = project_dirs(paths)
    with_tests = set()
    orphans: List[str] = []
    for path in paths:
        named = JSTS_TEST.search(path)
        if VENDORED.search(path) or not (named or JSTS_TEST_DIR.search(path)):
            continue
        owner = owning_project(path, dirs)
        if owner is not None:
            with_tests.add(owner)
        elif named:
            orphans.append(path)
    globs = workspace_globs(root_package_json, pnpm_workspace)
    rows = [(directory, directory in with_tests, is_workspace_member(directory, globs)) for directory in dirs]
    return rows, orphans


def render(rows: Sequence[PlanRow]) -> str:
    """Render the plan as the tab-separated lines the lane reads."""
    return "".join(
        f"{directory}\t{'yes' if has_tests else 'no'}\t{'yes' if member else 'no'}\n"
        for directory, has_tests, member in rows
    )


def _read_optional(path: str) -> str:
    """Read ``path`` as UTF-8, or return an empty string when no path was given."""
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8")


def _split_paths(text: str) -> List[str]:
    """Split ``git ls-files`` output: NUL-separated (``-z``) or one path per line."""
    separator = "\0" if "\0" in text else "\n"
    return [item.strip() for item in text.split(separator) if item.strip()]


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="Plan the JS/TS projects gate 3 must measure.")
    parser.add_argument("--files", default="", help="File holding `git ls-files -z` (or one-per-line) output.")
    parser.add_argument("--root-package-json", default="", help="The root package.json, if the repo has one.")
    parser.add_argument("--pnpm-workspace", default="", help="The root pnpm-workspace.yaml, if the repo has one.")
    parser.add_argument("--orphans-out", default="", help="Write test files outside every project here.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the project plan for the file list given with ``--files``."""
    args = _build_parser().parse_args(argv)
    if not args.files:
        print("ERROR jsts-projects: --files is required to plan", file=sys.stderr)
        return 2
    try:
        paths = _split_paths(Path(args.files).read_text(encoding="utf-8"))
        root_package_json = _read_optional(args.root_package_json)
        pnpm_workspace = _read_optional(args.pnpm_workspace)
    except OSError as exc:
        print(f"ERROR jsts-projects: cannot read the plan inputs: {exc}", file=sys.stderr)
        return 2
    rows, orphans = plan(paths, root_package_json=root_package_json, pnpm_workspace=pnpm_workspace)
    sys.stdout.write(render(rows))
    for orphan in orphans:
        print(f"WARN jsts-projects: test file outside every package.json project: {orphan}", file=sys.stderr)
    if args.orphans_out:
        Path(args.orphans_out).write_text("".join(f"{orphan}\n" for orphan in orphans), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
