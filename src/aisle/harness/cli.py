"""`harness` CLI entry point (CON-8). Subcommands grow per SPEC 070;
T03 ships `harness validate <graph.yaml>` (SPEC 060)."""

import argparse
import sys
from pathlib import Path

from aisle.harness.common import DEFAULT_ROOT, emit_report
from aisle.harness.validate import validate


def main() -> int:
    parser = argparse.ArgumentParser(prog="harness", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    val = subparsers.add_parser("validate", help="validate a dora dataflow YAML (SPEC 060)")
    val.add_argument("graph", type=Path)
    val.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    val.add_argument("--embodiment", default="franka")
    val.add_argument(
        "--allow-unproven",
        action="store_true",
        help="downgrade EVAL_MISSING_FOR_MOTION to a warning (never set for agents)",
    )
    args = parser.parse_args()

    report = validate(args.graph, args.root, args.embodiment, args.allow_unproven)
    return emit_report(
        report,
        lambda level, e: (
            f"validate {level}: {e['code']} at {e.get('edge') or e.get('node')}: {e['detail']}"
        ),
    )


if __name__ == "__main__":
    sys.exit(main())
