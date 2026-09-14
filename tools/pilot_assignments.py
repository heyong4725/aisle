#!/usr/bin/env python3
"""Sealed, balanced arm assignments for a pilot campaign (TRT-8, CSE-7, CON-8).

`create` seals a plan from a controller-private 256-bit seed and writes only
its public commitment; `reveal` appends exactly the next assignment to the
ledger after re-verifying the public history. The seed file stays private;
the ledger retains every randomized assignment, started or not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LEDGER_SCHEMA = "aisle.pilot-assignment-ledger.v1"


def _plan(seed_path: Path, arms, sessions_per_arm: int):
    from aisle.harness.treatment_randomization import create_sealed_plan

    seed_hex = Path(seed_path).expanduser().read_text().strip()
    blocks = [f"block-{index + 1:02d}" for index in range(sessions_per_arm)]
    return create_sealed_plan(list(arms), blocks, seed_hex)


def create(args) -> dict:
    from aisle.harness.pilot_records import TREATMENTS

    labels = {treatment: arm for arm, treatment in TREATMENTS.items()}
    protocol = json.loads(args.protocol.read_bytes())
    if protocol.get("campaign_id") != args.campaign:
        raise ValueError("protocol campaign differs from the requested campaign")
    arms = [labels[arm] for arm in protocol["treatment_arms"]]
    sessions_per_arm = protocol["stopping_rule"]["max_sessions_per_arm"]
    plan = _plan(args.seed, arms, sessions_per_arm)
    ledger = {
        "schema_version": LEDGER_SCHEMA,
        "campaign_id": args.campaign,
        "arms": arms,
        "sessions_per_arm": sessions_per_arm,
        "protocol_id": protocol["protocol_id"],
        "commitment": plan.public_commitment(),
        "assignments": [],
    }
    if args.ledger.exists():
        raise FileExistsError(f"ledger exists: {args.ledger}")
    args.ledger.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return {"ok": True, "ledger": str(args.ledger), **ledger["commitment"]}


def reveal(args) -> dict:
    from aisle.harness.treatment_randomization import reveal_assignment

    ledger = json.loads(args.ledger.read_bytes())
    if not isinstance(ledger, dict):
        raise ValueError("ledger is not an object")
    if ledger.get("schema_version") != LEDGER_SCHEMA or ledger.get("campaign_id") != args.campaign:
        raise ValueError("ledger does not belong to this campaign")
    plan = _plan(args.seed, ledger["arms"], ledger["sessions_per_arm"])
    if plan.public_commitment() != ledger["commitment"]:
        raise ValueError("private seed does not reproduce the ledger's commitment")
    prior = [row["assignment"] for row in ledger["assignments"]]
    record = reveal_assignment(plan, len(prior), prior)
    entry = {
        "assignment": record,
        "session_id": f"{args.campaign}-{record['assignment_index'] + 1:02d}-{record['arm']}",
    }
    ledger["assignments"].append(entry)
    args.ledger.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n")
    return {"ok": True, **entry}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "reveal"):
        command = commands.add_parser(name)
        command.add_argument("--campaign", required=True)
        command.add_argument(
            "--seed", type=Path, required=True, help="private 256-bit hex seed file"
        )
        command.add_argument("--ledger", type=Path, required=True)
        if name == "create":
            command.add_argument("--protocol", type=Path, required=True, help="SPEC 400 protocol")
    args = parser.parse_args(argv)
    try:
        result = create(args) if args.command == "create" else reveal(args)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as refused:
        result = {"ok": False, "error": "pilot assignment refused", "details": [repr(refused)]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
