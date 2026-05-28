"""Workspace-safe report writer split from :mod:`scripts.quality.common`.

Houses the ``write_report`` machinery (spec resolution, workspace-bounded path
validation, JSON+Markdown emission) so the shared ``common`` module stays
bounded in file-level complexity. The public names are re-exported from
``common`` to preserve the historical import surface.
"""

from __future__ import absolute_import

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping


@dataclass(frozen=True, slots=True)
class ReportSpec:
    """Report Spec."""

    out_json: str
    out_md: str
    default_json: str
    default_md: str
    render_md: Callable[[Mapping[str, Any]], str]


def _ensure_within_root(path: Path, root: Path) -> Path:
    """Return ``path`` when it stays within ``root``."""
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Output path escapes workspace root: {path}") from exc
    return path


def safe_output_path(raw: str, fallback: str, base: Path | None = None) -> Path:
    """Handle safe output path."""
    root = (base or Path.cwd()).resolve()
    candidate = Path((raw or "").strip() or fallback)
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (root / candidate).resolve(strict=False)
    )
    return _ensure_within_root(resolved, root)


def _raise_write_report_type_error(message: str) -> None:
    """Handle raise write report type error."""
    raise TypeError(message)


def _validate_report_spec_args(args: Any, kwargs: Any) -> ReportSpec | None:
    """Handle validate report spec args."""
    if args and isinstance(args[0], ReportSpec):
        if len(args) != 1 or kwargs:
            _raise_write_report_type_error(
                "write_report expects a ReportSpec or legacy keyword arguments"
            )
        return args[0]

    if args:
        _raise_write_report_type_error(
            "write_report expects a ReportSpec or legacy keyword arguments"
        )
    return None


def _pop_report_kwargs(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Handle pop report kwargs."""
    required = ("out_json", "out_md", "default_json", "default_md", "render_md")
    missing = [key for key in required if key not in kwargs]
    if missing:
        _raise_write_report_type_error(
            f"Missing required report parameter: {missing[0]}"
        )

    values = {key: kwargs.pop(key) for key in required}
    if kwargs:
        _raise_write_report_type_error(
            f"Unexpected write_report parameters: {', '.join(sorted(kwargs))}"
        )
    return values


def _resolve_report_spec(*args: Any, **kwargs: Any) -> ReportSpec:
    """Handle resolve report spec."""
    spec = _validate_report_spec_args(args, kwargs)
    if spec is not None:
        return spec
    return _report_spec_from_kwargs(_pop_report_kwargs(kwargs))


def _report_spec_from_kwargs(values: Mapping[str, Any]) -> ReportSpec:
    """Handle report spec from kwargs."""
    return ReportSpec(
        out_json=str(values["out_json"]),
        out_md=str(values["out_md"]),
        default_json=str(values["default_json"]),
        default_md=str(values["default_md"]),
        render_md=values["render_md"],
    )


def _write_workspace_text(path: Path, content: str, *, root: Path) -> None:
    """Write UTF-8 text after revalidating the workspace-relative target."""
    target = _ensure_within_root(path.resolve(strict=False), root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        handle.write(content)


def write_report(payload: Mapping[str, Any], *args: Any, **kwargs: Any) -> int:
    """Handle write report."""
    spec = _resolve_report_spec(*args, **kwargs)
    workspace_root = Path.cwd().resolve()
    try:
        json_path = safe_output_path(
            spec.out_json,
            spec.default_json,
            base=workspace_root,
        )
        md_path = safe_output_path(
            spec.out_md,
            spec.default_md,
            base=workspace_root,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    markdown_text = spec.render_md(payload)
    _write_workspace_text(json_path, json_text, root=workspace_root)
    _write_workspace_text(md_path, markdown_text, root=workspace_root)
    print(markdown_text, end="")
    return 0
