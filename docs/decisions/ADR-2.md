# ADR-2: argparse --help/usage output is a CON-8 carve-out

CON-8 requires every tool to emit a single JSON object on stdout with exit 0
iff ok. Interpretation chosen (CON-15, prompted by the PR 1 cross-review):
argparse's built-in `--help` (help text on stdout, exit 0) and usage errors
for unknown/malformed flags (usage on stderr, exit 2, stdout empty) are
accepted as-is for all AISLE CLIs. Help is documentation for humans, not tool
output for callers; machine callers never pass `--help`, and on argparse
errors stdout carries no non-JSON bytes while the exit code is still nonzero.
Domain-level argument validation that argparse cannot express (e.g. a
malformed `--specs` range) MUST still produce a JSON error report per CON-8.
