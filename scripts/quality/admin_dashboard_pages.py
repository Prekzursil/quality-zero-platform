#!/usr/bin/env python3
"""Phase 5 admin-dashboard page renderers.

Emits the 3 additional HTML pages the Phase 5 dashboard requires
beyond the existing ``index.html`` repo heatmap:

* ``coverage.html`` — per-repo coverage trend.
* ``drift.html``    — open/closed drift-sync PR list.
* ``audit.html``    — break-glass / skip JSONL feed.

Also provides the ``redact_private_repos`` helper used to mask
private-repo slugs on the public dashboard (per §8 of the design).

Every renderer is a pure function over a plain list of dicts, so
the dashboard builder workflow can compose them without running a
full inventory scan.
"""

from __future__ import absolute_import

# Newline constant used by the body builders so the duplicate "\n" literal
# Sonar's python:S1192 rule flagged at line 93 stays at zero.
_NL = "\n"

import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()


PRIVATE_SLUG_PLACEHOLDER = "<private>"


def redact_private_repos(
    rows: List[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Mask slugs for rows whose ``visibility == "private"``."""
    masked: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        if str(record.get("visibility", "public")).lower() == "private":
            record["slug"] = PRIVATE_SLUG_PLACEHOLDER
        masked.append(record)
    return masked


def _wrap_html(title: str, body: str) -> str:
    """Minimal HTML scaffold with escaped ``title``."""
    safe_title = html.escape(title)
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        f"  <meta charset=\"utf-8\">\n"
        f"  <title>{safe_title}</title>\n"
        "  <link rel=\"stylesheet\" href=\"styles.css\">\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{safe_title}</h1>\n"
        f"  <main>\n{body}  </main>\n"
        "</body>\n"
        "</html>\n"
    )


def render_coverage_trend_page(
    *, rows: List[Mapping[str, Any]],
) -> str:
    """Return the HTML for ``coverage.html``."""
    if not rows:
        body = "    <p>No coverage data available yet.</p>\n"
        return _wrap_html("Coverage trend", body)
    tbl_rows = []
    for row in rows:
        slug = html.escape(str(row.get("slug", "")))
        cov = row.get("coverage_percent")
        cov_text = f"{cov:.1f}" if isinstance(cov, (int, float)) else html.escape(
            str(cov or ""),
        )
        tbl_rows.append(f"      <tr><td>{slug}</td><td>{cov_text}</td></tr>")
    body = _NL.join((
        "    <table>",
        "      <thead><tr><th>Repo</th><th>Coverage %</th></tr></thead>",
        "      <tbody>",
        _NL.join(tbl_rows),
        "      </tbody>",
        "    </table>",
        "",
    ))
    return _wrap_html("Coverage trend", body)


def render_drift_page(
    *, entries: List[Mapping[str, Any]],
) -> str:
    """Return the HTML for ``drift.html``."""
    if not entries:
        body = "    <p>No drift entries. Fleet is in sync.</p>\n"
        return _wrap_html("Drift-sync status", body)
    rows = []
    for entry in entries:
        slug = html.escape(str(entry.get("slug", "")))
        status = html.escape(str(entry.get("status", "")))
        pr_url = str(entry.get("pr_url", ""))
        if pr_url:
            pr_cell = (
                f"<a href=\"{html.escape(pr_url)}\">"
                f"{html.escape(pr_url)}</a>"
            )
        else:
            pr_cell = "&mdash;"
        rows.append(
            f"      <tr><td>{slug}</td><td>{status}</td><td>{pr_cell}</td></tr>",
        )
    body = (
        "    <table>\n"
        "      <thead><tr><th>Repo</th><th>Status</th><th>Sync PR</th></tr></thead>\n"
        "      <tbody>\n"
        + "\n".join(rows) + "\n"
        "      </tbody>\n"
        "    </table>\n"
    )
    return _wrap_html("Drift-sync status", body)


def render_audit_page(
    *, entries: List[Mapping[str, Any]],
) -> str:
    """Return the HTML for ``audit.html`` (break-glass / skip feed)."""
    if not entries:
        body = "    <p>No bypass events recorded.</p>\n"
        return _wrap_html("Bypass audit feed", body)
    rows = []
    for entry in entries:
        ts = html.escape(str(entry.get("timestamp", "")))
        label = html.escape(str(entry.get("label", "")))
        slug = html.escape(str(entry.get("pr_slug", "")))
        number = html.escape(str(entry.get("pr_number", "")))
        actor = html.escape(str(entry.get("actor", "")))
        incident = html.escape(str(entry.get("incident", "")))
        rows.append(
            "      <tr>"
            f"<td>{ts}</td>"
            f"<td>{label}</td>"
            f"<td>{slug}#{number}</td>"
            f"<td>@{actor}</td>"
            f"<td>{incident or '&mdash;'}</td>"
            "</tr>",
        )
    body = (
        "    <table>\n"
        "      <thead><tr>"
        "<th>Time</th><th>Label</th><th>PR</th><th>Actor</th>"
        "<th>Incident</th></tr></thead>\n"
        "      <tbody>\n"
        + "\n".join(rows) + "\n"
        "      </tbody>\n"
        "    </table>\n"
    )
    return _wrap_html("Bypass audit feed", body)


def load_audit_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Read a one-record-per-line JSONL audit file; empty list if absent."""
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        if isinstance(record, dict):
            records.append(record)
    return records


def load_coverage_rows(path: Path) -> List[Dict[str, Any]]:
    """Load a per-repo coverage-trend JSON file; empty list if absent."""
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("repos"), list):
        raw_rows = payload["repos"]
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        return []
    return [dict(row) for row in raw_rows if isinstance(row, dict)]


def load_drift_entries(path: Path) -> List[Dict[str, Any]]:
    """Load drift-sync entries from a JSONL file; empty list if absent."""
    return load_audit_jsonl(path)


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    import argparse

    from scripts.quality.common import safe_output_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-jsonl", default="")
    parser.add_argument("--coverage-json", default="")
    parser.add_argument("--drift-jsonl", default="")
    parser.add_argument("--output-dir", required=True)
    _args = parser.parse_args()

    # ``safe_output_path`` resolves ``--output-dir`` against the
    # current working directory and rejects any path that escapes
    # the workspace (Sonar S2083 path-traversal defence). Each
    # rendered page filename is a hardcoded constant, so the only
    # user-controllable component is the parent directory — and
    # that's pinned to the workspace by ``_ensure_within_root``.
    _out = safe_output_path(_args.output_dir, fallback="site")
    _out.mkdir(parents=True, exist_ok=True)

    _cov_rows = redact_private_repos(
        load_coverage_rows(Path(_args.coverage_json)) if _args.coverage_json else []
    )
    _coverage_path = (_out / "coverage.html").resolve()
    _coverage_path.relative_to(_out.resolve())
    _coverage_path.write_text(
        render_coverage_trend_page(rows=_cov_rows), encoding="utf-8",
    )

    _drift_rows = redact_private_repos(
        load_drift_entries(Path(_args.drift_jsonl)) if _args.drift_jsonl else []
    )
    _drift_path = (_out / "drift.html").resolve()
    _drift_path.relative_to(_out.resolve())
    _drift_path.write_text(
        render_drift_page(entries=_drift_rows), encoding="utf-8",
    )

    _audit_rows = (
        load_audit_jsonl(Path(_args.audit_jsonl)) if _args.audit_jsonl else []
    )
    _audit_path = (_out / "audit.html").resolve()
    _audit_path.relative_to(_out.resolve())
    _audit_path.write_text(
        render_audit_page(entries=_audit_rows), encoding="utf-8",
    )
    print(f"Wrote coverage.html, drift.html, audit.html to {_out}")
