#!/usr/bin/env python3
"""Records producer for a pilot campaign (STA-3, STA-11, CSE-8, CON-8)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    from aisle.harness.pilot_records import RecordsError, build_records

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument(
        "--sessions", type=Path, required=True, help="root holding one directory per session id"
    )
    parser.add_argument("--agent-system", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        records = build_records(
            json.loads(args.protocol.read_bytes()),
            json.loads(args.ledger.read_bytes()),
            args.sessions,
            agent_system=args.agent_system,
            task=args.task,
        )
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n")
        result = {
            "ok": True,
            "sessions": len(records["sessions"]),
            "output": None if args.output is None else str(args.output),
        }
    except (RecordsError, OSError, ValueError, KeyError, TypeError, AttributeError) as refused:
        result = {"ok": False, "error": "pilot records refused", "details": [repr(refused)]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
