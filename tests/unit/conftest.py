"""Shared helpers for unit tests that drive the tools/ CLIs as subprocesses."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_tool(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run tools/<script> with args; capture text output."""
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / script), *args],
        capture_output=True,
        text=True,
    )
