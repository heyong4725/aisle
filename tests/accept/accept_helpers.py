"""Shared helpers for the acceptance suite (imported like
tests/unit/cli_helpers.py — pytest puts each test dir on sys.path)."""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_harness(*args: str, timeout: float) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-m", "aisle.harness.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=timeout,
    )
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError as bad:
        raise AssertionError(
            f"non-JSON stdout from harness {args[:2]}: "
            f"stdout={proc.stdout[:400]!r} stderr={proc.stderr[-600:]!r}"
        ) from bad
