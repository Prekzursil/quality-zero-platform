"""CLI entrypoint for quality rollup v2 (per design §A.8 + Phase 13)."""
from __future__ import absolute_import

from typing import Dict, Tuple

import argparse
import json
import sys
from pathlib import Path

from scripts.quality.rollup_v2.pipeline import run_pipeline

# Artifact discovery: maps lane key -> (subdir, filename)
_ARTIFACT_LOCATIONS: Dict[str, Tuple[str, str]] = {
    "codacy": ("codacy", "codacy.json"),
    "coverage": ("coverage", "coverage.json"),
    "deepscan": ("deepscan", "deepscan.json"),
    "deepsource": ("deepsource", "deepsource.json"),
    "dependabot": ("dependabot", "dependabot.json"),
    "qlty": ("qlty", "qlty.json"),
    "secrets": ("secrets", "secrets.json"),
    "sentry": ("sentry", "sentry.json"),
    "sonarcloud": ("sonarcloud", "sonarcloud.json"),
}


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for rollup_v2."""
    parser = argparse.ArgumentParser(
        prog="rollup_v2",
        description="Quality Rollup v2 -- canonical finding pipeline + multi-view markdown renderer.",
    )
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        help="Directory containing per-provider artifact subdirectories.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where canonical.json and rollup.md will be written.",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Repository slug (owner/repo).",
    )
    parser.add_argument(
        "--sha",
        required=True,
        help="Commit SHA.",
    )
    parser.add_argument(
        "--enable-llm-patches",
        action="store_true",
        default=False,
        help="Enable LLM fallback patch generation (default: off).",
    )
    parser.add_argument(
        "--max-llm-patches",
        type=int,
        default=10,
        help="Maximum number of LLM patch calls per run (default: 10).",
    )
    return parser.parse_args()


def _load_artifacts(artifacts_dir: Path) -> Dict[str, object]:
    """Discover and load JSON artifacts from the artifacts directory."""
    artifacts: Dict[str, object] = {}
    for lane_key, (subdir, filename) in _ARTIFACT_LOCATIONS.items():
        json_path = artifacts_dir / subdir / filename
        if json_path.is_file():
            artifacts[lane_key] = json.loads(
                json_path.read_text(encoding="utf-8")
            )
    return artifacts


def main() -> int:
    """Run the rollup_v2 pipeline from CLI arguments."""
    args = parse_args()
    artifacts_dir = Path(args.artifacts_dir)
    output_dir = Path(args.output_dir)
    repo_root = Path.cwd()

    output_dir.mkdir(parents=True, exist_ok=True)

    # Load artifacts
    artifacts = _load_artifacts(artifacts_dir)

    # Run pipeline
    result = run_pipeline(
        artifacts=artifacts,
        repo_root=repo_root,
        output_dir=output_dir,
    )

    # Write outputs
    canonical_path = output_dir / "canonical.json"
    canonical_path.write_text(
        json.dumps(result.canonical_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    rollup_path = output_dir / "rollup.md"
    rollup_path.write_text(result.markdown, encoding="utf-8")

    # Print markdown to stdout (with fallback encoding for Windows)
    sys.stdout.buffer.write(result.markdown.encode("utf-8", errors="replace"))

    return 0


if __name__ == "__main__":  # pragma: no cover -- script entrypoint
    raise SystemExit(main())
