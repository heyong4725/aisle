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
# CON-7/HAR-2: the committed env hash must match the frozen set — any PR
# touching frozen files regenerates it with tools/env_hash.py --write
uv run python tools/env_hash.py --check
