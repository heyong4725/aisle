#!/usr/bin/env bash
# Unconditional local CI gates in the CON-9 order; exit nonzero on first
# failure. The conditional `pytest -m "sim or graph"` gate is added when
# those suites exist (T04+).
set -euo pipefail
cd "$(dirname "$0")/.."

uv run ruff format --check .
uv run ruff check .
uv run pytest -m unit
uv run python tools/trace_check.py
