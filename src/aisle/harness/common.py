"""Shared plumbing for harness CLIs (CON-8)."""

import json
import sys
from collections.abc import Callable
from pathlib import Path

# dev-harness assumption: a src-layout checkout (uv editable install);
# non-editable installs must pass --root explicitly
DEFAULT_ROOT = Path(__file__).resolve().parents[3]


def emit_report(report: dict, line_for: Callable[[str, dict], str]) -> int:
    """Print the single JSON report to stdout (CON-8; default=str because
    YAML-native scalars like unquoted dates must never break the contract),
    mirror errors/warnings to stderr, and return the exit code."""
    print(json.dumps(report, default=str))
    for level in ("errors", "warnings"):
        for entry in report.get(level, []):
            print(line_for(level[:-1], entry), file=sys.stderr)
    return 0 if report["ok"] else 1
