from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def dedupe_strings(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def safe_output_path(raw: str, fallback: str, base: Path | None = None) -> Path:
    root = (base or Path.cwd()).resolve()
    candidate = Path((raw or "").strip() or fallback).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Output path escapes workspace root: {candidate}") from exc
    return resolved


def write_report(
    payload: Mapping[str, Any],
    *,
    out_json: str,
    out_md: str,
    default_json: str,
    default_md: str,
    render_md: Callable[[Mapping[str, Any]], str],
) -> int:
    try:
        json_path = safe_output_path(out_json, default_json)
        md_path = safe_output_path(out_md, default_md)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_md(payload), encoding="utf-8")
    print(md_path.read_text(encoding="utf-8"), end="")
    return 0
