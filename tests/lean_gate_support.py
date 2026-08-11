"""Shared helpers for the behavioural lean-gate workflow tests.

Several gate lanes are pure shell embedded in ``reusable-quality.yml``. Asserting
on the YAML text only proves the text; the load-bearing properties (a skip that
must be unreachable, a scan that must always run) are behavioural, so these
tests EXTRACT the step's script and EXECUTE it under a real bash.
"""

from __future__ import absolute_import

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-quality.yml"

# ``shutil.which`` is LAST on purpose: on this project's Windows dev box it
# resolves to ``C:\WINDOWS\system32\bash.exe``, which is WSL. WSL cannot see
# Windows drive paths as ``/c/...`` and would report every script as missing.
_BASH_CANDIDATES = (
    "/bin/bash",
    "/usr/bin/bash",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
)


def find_bash() -> Optional[str]:
    """Locate a real bash interpreter, or ``None`` when there is none."""
    for candidate in _BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("bash")


def workflow_steps() -> List[Dict[str, object]]:
    """Return the parsed steps of the reusable ``quality`` job."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"]["quality"]["steps"]


def step_named(name: str) -> Dict[str, object]:
    """Return the step called ``name``, or fail with a readable message."""
    for step in workflow_steps():
        if step.get("name") == name:
            return step
    raise AssertionError("step '" + name + "' is missing from " + WORKFLOW.name)


def run_step_script(
    script: str,
    *,
    workdir: Path,
    env_overrides: Dict[str, str],
    bash: str,
    filename: str = "step.sh",
) -> subprocess.CompletedProcess:
    """Write a step's shell body to ``workdir`` and execute it under ``bash``."""
    script_path = workdir / filename
    script_path.write_text(script, encoding="utf-8", newline="\n")
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [bash, filename],
        cwd=str(workdir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
