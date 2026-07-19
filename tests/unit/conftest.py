"""Shared helpers for unit tests that drive the tools/ CLIs as subprocesses."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cli(argv: list[str]) -> subprocess.CompletedProcess:
    """Run the test venv's python with argv; capture text output."""
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True)


def run_tool(script: str, *args: str) -> subprocess.CompletedProcess:
    """Run tools/<script> with args."""
    return run_cli([str(REPO_ROOT / "tools" / script), *args])


def run_module(module: str, *args: str) -> subprocess.CompletedProcess:
    """Run a package CLI via python -m."""
    return run_cli(["-m", module, *args])
