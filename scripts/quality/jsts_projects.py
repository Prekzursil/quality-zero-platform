#!/usr/bin/env python3
"""Plan which JS/TS projects gate 3 must measure.

The jsts coverage lane used to measure ONLY the root project whenever a root
``package.json`` existed: nested discovery ran only when the root had none. So a
nested project (``frontend/``, ``packages/ui``) with its own tests and its own
coverage script was never run, and a nested project with tests and NO coverage
script was never flagged - while the lane printed PASS.

This module turns ``git ls-files`` output into the full project plan:

* every ``package.json`` outside vendored / build trees is a project;
* every JS/TS test file is attributed to its DEEPEST enclosing project;
* a project that the root ``package.json`` ``workspaces`` (or a root
  ``pnpm-workspace.yaml``) declares is marked as a workspace member, because a
  root coverage run over a workspace measures its members.

Output is one tab-separated line per project, the root first and then the rest
sorted by path::

    <dir>\t<has_tests: yes|no>\t<workspace_member: yes|no>

JS/TS test files that belong to no project at all are reported on stderr and,
with ``--orphans-out``, written to a file so the lane can fail on them: no
coverage lane ever runs a test that lives outside every project.

``--member-evidence <dir>`` answers the one question a plan cannot: did the
root's coverage run actually measure that workspace member? Only a coverage
report that names the member's files says so; a ``workspaces`` entry alone
does not.

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
"""A JS/TS test file: ``*.test.*`` / ``*.spec.*`` or anything under ``__tests__/``."""

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

    A workspace member's tests also count as tests of the ROOT project: a
    root coverage run over a workspace is how they get measured, so a root
    whose only tests live in its members still has a test surface.
    """
    dirs = project_dirs(paths)
    with_tests = set()
    orphans: List[str] = []
    for path in paths:
        if VENDORED.search(path) or not JSTS_TEST.search(path):
            continue
        owner = owning_project(path, dirs)
        if owner is None:
            orphans.append(path)
        else:
            with_tests.add(owner)
    globs = workspace_globs(root_package_json, pnpm_workspace)
    members = {directory for directory in dirs if is_workspace_member(directory, globs)}
    if with_tests & members:
        with_tests.add(ROOT)
    rows = [(directory, directory in with_tests, directory in members) for directory in dirs]
    return rows, orphans


def render(rows: Sequence[PlanRow]) -> str:
    """Render the plan as the tab-separated lines the lane reads."""
    return "".join(
        f"{directory}\t{'yes' if has_tests else 'no'}\t{'yes' if member else 'no'}\n"
        for directory, has_tests, member in rows
    )


def coverage_report_paths(project_dir: Path) -> List[str]:
    """Return every source path the project's machine-readable coverage reports name.

    Read from ``coverage/**/lcov.info`` (``SF:`` records) and the istanbul
    ``coverage-summary.json`` / ``coverage-final.json`` keys - the same
    reporters the coverage-number assertion reads.
    """
    paths = set()
    coverage_dir = Path(project_dir) / "coverage"
    for report in sorted(coverage_dir.glob("**/lcov.info")):
        for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("SF:"):
                paths.add(line[3:].strip().replace("\\", "/"))
    for name in ("coverage-summary.json", "coverage-final.json"):
        for report in sorted(coverage_dir.glob(f"**/{name}")):
            try:
                document = json.loads(report.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(document, dict):
                paths.update(str(key).replace("\\", "/") for key in document if key != "total")
    return sorted(paths)


def measured_by(member: str, report_paths: Iterable[str]) -> bool:
    """True when a coverage report names at least one file inside ``member``.

    A workspace declaration alone does not prove the root's coverage run
    measured a member (a root ``jest src --coverage`` never does); a report
    that lists the member's files does.
    """
    needle = "/" + member.strip("/") + "/"
    return any(needle in "/" + path.lstrip("./") for path in report_paths)


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
    parser.add_argument(
        "--member-evidence",
        default="",
        help="Instead of planning: exit 0 if --project-dir's coverage reports name a file in this member dir.",
    )
    parser.add_argument("--project-dir", default=".", help="Project whose coverage reports --member-evidence reads.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the project plan, or answer a workspace-member evidence question."""
    args = _build_parser().parse_args(argv)
    if args.member_evidence:
        evidence = measured_by(args.member_evidence, coverage_report_paths(Path(args.project_dir)))
        return 0 if evidence else 1
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
