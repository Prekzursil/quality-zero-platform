#!/usr/bin/env python3
from __future__ import absolute_import

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sys
from typing import Dict, Iterable, List
from xml.etree import ElementTree

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.quality.coverage_paths import _coverage_source_candidates, _normalize_source_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Normalize coverage report paths for QLTY uploads.")
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("inputs", nargs="+")
    return parser.parse_args()


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    try:
        import os

        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


def _is_xml_report(path: Path) -> bool:
    return path.suffix.lower() == ".xml"


def _is_lcov_report(path: Path) -> bool:
    lowered = path.name.lower()
    return path.suffix.lower() in {".info", ".lcov"} or lowered in {"lcov", "lcov.info"}


def _existing_candidate(raw_path: str, source_roots: Iterable[str]) -> str:
    for candidate in _coverage_source_candidates(raw_path, list(source_roots)):
        if Path(candidate).is_file():
            return Path(candidate).as_posix()
    normalized = _normalize_source_path(raw_path)
    if normalized and Path(normalized).is_file():
        return Path(normalized).as_posix()
    return ""


def _xml_source_elements(root: ElementTree.Element) -> List[ElementTree.Element]:
    return [
        element
        for element in root.iter()
        if isinstance(element.tag, str) and element.tag.rsplit("}", 1)[-1] == "source"
    ]


def _copy_report(path: Path, out_dir: Path, *, repo_dir: Path, suffix: str = "") -> Path:
    try:
        relative_path = path.relative_to(repo_dir)
    except ValueError:
        relative_path = path
    safe_name = "__".join(part for part in relative_path.parts if part not in {".", ""})
    out_path = out_dir / f"{Path(safe_name).stem}{suffix}{path.suffix}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, out_path)
    return out_path


def normalize_xml_report(path: Path, repo_dir: Path, out_dir: Path) -> Dict[str, object]:
    tree = ElementTree.parse(path)
    root = tree.getroot()
    source_nodes = _xml_source_elements(root)
    source_roots = [str(node.text or "").strip() for node in source_nodes if str(node.text or "").strip()]
    rewritten = 0

    for element in root.iter():
        raw_filename = element.get("filename")
        if not raw_filename:
            continue
        normalized = _existing_candidate(raw_filename, source_roots)
        if normalized and normalized != raw_filename.replace("\\", "/"):
            element.set("filename", normalized)
            rewritten += 1

    repo_root_text = repo_dir.resolve().as_posix()
    for node in source_nodes:
        node.text = repo_root_text

    out_path = _copy_report(path, out_dir, repo_dir=repo_dir, suffix="")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return {
        "input": path.as_posix(),
        "normalized": out_path.as_posix(),
        "rewritten_paths": rewritten,
        "format": "xml",
    }


def normalize_lcov_report(path: Path, repo_dir: Path, out_dir: Path) -> Dict[str, object]:
    rewritten = 0
    lines: List[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("SF:"):
            candidate = _existing_candidate(raw_line.split(":", 1)[1], [])
            if candidate and candidate != raw_line.split(":", 1)[1].replace("\\", "/"):
                raw_line = f"SF:{candidate}"
                rewritten += 1
        lines.append(raw_line)

    out_path = _copy_report(path, out_dir, repo_dir=repo_dir, suffix="")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "input": path.as_posix(),
        "normalized": out_path.as_posix(),
        "rewritten_paths": rewritten,
        "format": "lcov",
    }


def normalize_reports(inputs: Iterable[str], *, repo_dir: Path, out_dir: Path) -> List[Dict[str, object]]:
    normalized: List[Dict[str, object]] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    with _working_directory(repo_dir):
        for raw_input in inputs:
            path = (repo_dir / raw_input).resolve()
            if _is_xml_report(path):
                normalized.append(normalize_xml_report(path, repo_dir, out_dir))
            elif _is_lcov_report(path):
                normalized.append(normalize_lcov_report(path, repo_dir, out_dir))
            else:
                copied = _copy_report(path, out_dir, repo_dir=repo_dir, suffix="-qlty")
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
    args = _parse_args()
    repo_dir = Path(args.repo_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    payload = normalize_reports(args.inputs, repo_dir=repo_dir, out_dir=out_dir)
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())  # pragma: no cover
