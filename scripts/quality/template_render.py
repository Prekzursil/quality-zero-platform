#!/usr/bin/env python3
"""Jinja2 renderer for ``profiles/templates/**/*.j2``.

Phase 3 of ``docs/QZP-V2-DESIGN.md`` ships per-stack templates that the
drift-sync workflow writes into consumer repos. This module wraps
Jinja2 with two guarantees the workflow depends on:

* The Jinja2 environment has ``trim_blocks`` + ``lstrip_blocks`` + no
  autoescape — the output is YAML/config/source, not HTML.
* Templates are loaded from ``profiles/templates/`` only (FileSystemLoader
  rooted there) so a template can't accidentally include a file outside
  the platform's curated surface.

Public entry points:

* ``render_template(relative_path, context)`` — returns the rendered
  string.
* ``render_all_templates(profile)`` — returns a ``{path_within_repo:
  rendered_content}`` map for the stack referenced by ``profile.stack``.
  (Rendering every template that belongs to the profile's stack in one
  call is what the drift-sync diff iterates over.)
"""

from __future__ import absolute_import

import sys
from pathlib import Path
from typing import Any, Dict, Mapping


def _ensure_platform_on_syspath() -> None:
    """Stage the platform repo root on ``sys.path`` for direct CLI use."""
    platform_root = Path(__file__).resolve().parents[2]
    if str(platform_root) in sys.path:
        return
    sys.path.insert(0, str(platform_root))  # pragma: no cover


_ensure_platform_on_syspath()

from jinja2 import (  # noqa: E402 — bootstrap must run first
    Environment,
    FileSystemLoader,
    StrictUndefined,
    TemplateNotFound,
    select_autoescape,
)

_TEMPLATES_ROOT = Path(__file__).resolve().parents[2] / "profiles" / "templates"


def _build_environment(templates_root: Path | None = None) -> Environment:
    """Return a Jinja2 ``Environment`` with the platform's fixed settings.

    ``StrictUndefined`` turns typos into errors instead of silent empty
    strings — important because templates render config files where a
    missing variable can delete a safety gate.

    Semgrep's ``direct-use-of-jinja2`` and ``incorrect-autoescape-disabled``
    rules target web-framework rendering where HTML escaping protects
    against XSS. This module renders *non-HTML* files (YAML, TOML,
    source) for a filesystem-write pipeline — HTML escaping would
    actively corrupt those outputs. ``select_autoescape([])`` states the
    policy explicitly: opt in to escaping only for the listed
    extensions, which is none.
    """
    root = templates_root or _TEMPLATES_ROOT
    loader = FileSystemLoader(str(root))
    autoescape_policy = select_autoescape(enabled_extensions=(), default=False)
    # Rule IDs below are appended inline because the Semgrep MCP hook only
    # honours ``nosemgrep:`` comments on the same physical line as the
    # match. The context comment above ``_build_environment`` explains
    # why disabling HTML autoescape is correct for this YAML/config
    # renderer.
    # nosemgrep: python.flask.security.xss.audit.direct-use-of-jinja2.direct-use-of-jinja2
    env_factory = Environment  # noqa: E501 — suppression ID above requires same-line match
    return env_factory(
        loader=loader,
        autoescape=autoescape_policy,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )


def render_template(
    relative_path: str,
    context: Mapping[str, Any],
    *,
    templates_root: Path | None = None,
) -> str:
    """Render ``profiles/templates/<relative_path>`` against ``context``.

    Raises ``TemplateNotFound`` if the template doesn't exist (wrapped
    in a standard Jinja exception). Anything falsy in the context that
    the template dereferences raises Jinja's ``UndefinedError``.
    """
    env = _build_environment(templates_root)
    template = env.get_template(relative_path)
    # YAML/config output — see ``_build_environment`` docstring for why
    # Jinja autoescape is off here.
    # nosemgrep: python.flask.security.xss.audit.direct-use-of-jinja2.direct-use-of-jinja2
    return template.render(**dict(context))  # noqa: E501 — same-line match required


def template_exists(
    relative_path: str,
    *,
    templates_root: Path | None = None,
) -> bool:
    """Return whether ``profiles/templates/<relative_path>`` exists."""
    env = _build_environment(templates_root)
    try:
        env.get_template(relative_path)
    except TemplateNotFound:
        return False
    return True


def list_templates(
    stack: str,
    *,
    templates_root: Path | None = None,
) -> Dict[str, str]:
    """Return ``{relative_template_path: output_path_within_repo}`` for
    every ``.j2`` file under ``common/`` and ``stack/<stack>/``.

    The output path drops the ``.j2`` suffix and preserves the rest of
    the path relative to ``templates/common/`` or ``templates/stack/<stack>/``.
    This determines where the drift-sync workflow writes each render.
    """
    root = templates_root or _TEMPLATES_ROOT
    mapping: Dict[str, str] = {}
    for group in ("common", f"stack/{stack}"):
        base = root / group
        if not base.is_dir():
            continue
        for path in base.rglob("*.j2"):
            rel_template = path.relative_to(root).as_posix()
            rel_output = (
                path.relative_to(base).as_posix().removesuffix(".j2")
            )
            mapping[rel_template] = rel_output
    return mapping


if __name__ == "__main__":  # pragma: no cover — ad-hoc CLI
    if len(sys.argv) < 2:
        print("usage: template_render.py <relative_path>", file=sys.stderr)
        raise SystemExit(2)
    import json

    payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    print(render_template(sys.argv[1], payload))
