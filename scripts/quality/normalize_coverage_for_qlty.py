#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Dict, Iterable, List

from scripts.quality.coverage_paths import (
    _coverage_source_candidates,
    _normalize_source_path,
)


_XML_FILENAME_RE = re.compile(
    r'(?P<prefix><[^>]+\bfilename=(?P<quote>["\']))(?P<value>.*?)(?P=quote)'
)
_XML_SOURCE_RE = re.compile(r"<source>(?P<value>.*?)</source>")


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for QLTY coverage normalization."""
    parser = argparse.ArgumentParser(
        description="Normalize coverage report paths for QLTY uploads."
    )
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("inputs", nargs="+")
    return parser.parse_args()


@contextmanager
def _working_directory(path: Path):
    """Temporarily switch the process working directory."""
    previous = Path.cwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


def _is_xml_report(path: Path) -> bool:
    """Return True when the report uses an XML-based coverage format."""
    return path.suffix.lower() == ".xml"


def _is_lcov_report(path: Path) -> bool:
    """Return True when the report uses an LCOV-compatible file name."""
    lowered = path.name.lower()
    return path.suffix.lower() in {".info", ".lcov"} or lowered in {"lcov", "lcov.info"}


def _existing_candidate(raw_path: str, source_roots: Iterable[str]) -> str:
    """Resolve the first existing repo-relative candidate for a coverage source."""
    for candidate in _coverage_source_candidates(raw_path, list(source_roots)):
        if Path(candidate).is_file():
            return Path(candidate).as_posix()
    normalized = _normalize_source_path(raw_path)
    if normalized and Path(normalized).is_file():
        return Path(normalized).as_posix()
    return ""


def _xml_source_roots(text: str) -> List[str]:
    """Collect source roots declared inside a coverage XML payload."""
    return [
        match.group("value").strip()
        for match in _XML_SOURCE_RE.finditer(text)
        if match.group("value").strip()
    ]


def _path_within_base(base_dir: Path, candidate: Path) -> Path:
    """Resolve a path and reject candidates that escape the trusted base."""
    resolved_base = base_dir.resolve()
    resolved_candidate = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (resolved_base / candidate).resolve(strict=False)
    )
    if os.path.commonpath([str(resolved_base), str(resolved_candidate)]) != str(
        resolved_base
    ):
        raise ValueError(f"Path escapes normalized coverage workspace: {candidate}")
    return resolved_candidate


def _output_path(out_dir: Path, *, report_id: int, extension: str) -> Path:
    """Build a normalized artifact path inside the validated output directory."""
    out_path = _path_within_base(out_dir, Path(f"report-{report_id}{extension}"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    return out_path


def _copy_report(path: Path, out_path: Path) -> Path:
    """Copy an already-normalized artifact into its upload location."""
    shutil.copy2(path, out_path)
    return out_path


def normalize_xml_report(
    path: Path,
    repo_dir: Path,
    out_dir: Path,
    *,
    report_id: int,
) -> Dict[str, object]:
    """Rewrite Cobertura-style XML reports to repo-relative paths."""
    text = path.read_text(encoding="utf-8")
    source_roots = _xml_source_roots(text)
    rewritten = 0

    def _replace_filename(match: re.Match[str]) -> str:
        """Rewrite one XML filename attribute when it maps to a repo file."""
        nonlocal rewritten
        raw_filename = match.group("value")
        normalized = _existing_candidate(raw_filename, source_roots)
        if normalized and normalized != raw_filename.replace("\\", "/"):
            rewritten += 1
            return f"{match.group('prefix')}{normalized}{match.group('quote')}"
        return match.group(0)

    repo_root_text = repo_dir.resolve().as_posix()
    normalized_text = _XML_FILENAME_RE.sub(_replace_filename, text)
    normalized_text = _XML_SOURCE_RE.sub(
        f"<source>{repo_root_text}</source>",
        normalized_text,
    )
    out_path = _output_path(out_dir, report_id=report_id, extension=".xml")
    out_path.write_text(normalized_text, encoding="utf-8")
    return {
        "input": path.as_posix(),
        "normalized": out_path.as_posix(),
        "rewritten_paths": rewritten,
        "format": "xml",
    }


def normalize_lcov_report(
    path: Path,
    out_dir: Path,
    *,
    report_id: int,
) -> Dict[str, object]:
    """Rewrite LCOV source file entries to repo-relative paths."""
    rewritten = 0
    lines: List[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("SF:"):
            candidate = _existing_candidate(raw_line.split(":", 1)[1], [])
            if candidate and candidate != raw_line.split(":", 1)[1].replace("\\", "/"):
                raw_line = f"SF:{candidate}"
                rewritten += 1
        lines.append(raw_line)

    out_path = _output_path(out_dir, report_id=report_id, extension=".info")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")  # NOSONAR
    return {
        "input": path.as_posix(),
        "normalized": out_path.as_posix(),
        "rewritten_paths": rewritten,
        "format": "lcov",
    }


def normalize_reports(
    inputs: Iterable[str],
    *,
    repo_dir: Path,
    out_dir: Path,
) -> List[Dict[str, object]]:
    """Normalize every declared coverage input into deterministic temp artifacts."""
    normalized: List[Dict[str, object]] = []
    repo_dir = repo_dir.resolve()
    out_dir = _path_within_base(repo_dir, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with _working_directory(repo_dir):
        for index, raw_input in enumerate(inputs, start=1):
            path = _path_within_base(repo_dir, Path(raw_input))
            if _is_xml_report(path):
                normalized.append(
                    normalize_xml_report(
                        path,
                        repo_dir,
                        out_dir,
                        report_id=index,
                    )
                )
            elif _is_lcov_report(path):
                normalized.append(
                    normalize_lcov_report(
                        path,
                        out_dir,
                        report_id=index,
                    )
                )
            else:
                copied = _copy_report(
                    path,
                    _output_path(
                        out_dir,
                        report_id=index,
                        extension=".artifact",
                    ),
                )
                normalized.append(
                    {
                        "input": path.as_posix(),
                        "normalized": copied.as_posix(),
                        "rewritten_paths": 0,
                        "format": "copy",
                    }
                )
    return normalized


def main() -> int:
    """Normalize requested coverage inputs and print a JSON manifest."""
    args = _parse_args()
    repo_dir = Path(args.repo_dir).resolve()
    out_dir = Path(args.out_dir)
    payload = normalize_reports(args.inputs, repo_dir=repo_dir, out_dir=out_dir)
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())  # pragma: no cover
