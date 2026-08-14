"""ADR-30 terminal run-to-quiescence barrier node."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from aisle.turns import ProtocolError, TurnBarrier, TurnWatchdog


def load_plan(environ) -> dict:
    raw = environ.get("AISLE_TURN_PLAN", "").strip()
    if not raw:
        raise SystemExit("turn-barrier config refused: AISLE_TURN_PLAN is required")
    try:
        if raw.endswith(".json"):
            path = Path(raw)
            if not path.is_file():
                path = Path(__file__).resolve().parents[3] / "graphs" / raw
            plan = json.loads(path.read_text(encoding="utf-8"))
        else:
            plan = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(
            f"turn-barrier config refused: cannot load AISLE_TURN_PLAN: {exc}"
        ) from exc
    return plan


def main() -> None:  # pragma: no cover - exercised by graph acceptance
    import pyarrow as pa
    from dora import Node

    from aisle.topics import make_sender

    plan = load_plan(os.environ)
    node = Node()
    send = make_sender(node)
    barrier = TurnBarrier(plan)
    watchdog = TurnWatchdog(
        ordinary_s=float(os.environ.get("AISLE_TURN_WATCHDOG_S", "2")),
        verdict_s=float(os.environ.get("AISLE_VERDICT_TURN_WATCHDOG_S", "15")),
        clock=time.monotonic,
    )

    def dispatch(ready: dict[str, dict[str, int]]) -> None:
        if barrier.current is None:
            return
        for target, expected in sorted(ready.items()):
            names = sorted(expected)
            metadata = {
                **barrier.current.metadata(),
                "target_node": target,
                "expected_inputs": names,
                "expected_counts": [expected[name] for name in names],
            }
            send(
                "turn",
                pa.array([barrier.current.turn_id], type=pa.uint64()),
                metadata,
            )

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        try:
            if event["id"] == "sim_turn":
                watchdog.open()
                dispatch(barrier.open_bridge(metadata))
            elif event["id"] == "tick":
                if watchdog.expired:
                    current = barrier.current
                    raise ProtocolError(
                        f"turn watchdog expired ({watchdog.turn_type}) at "
                        f"{current.epoch}:{current.turn_id}"
                        if current
                        else "turn watchdog expired"
                    )
            else:
                source_node = metadata.get("source_node")
                if not isinstance(source_node, str) or not source_node:
                    raise ProtocolError(f"{event['id']} missing source_node")
                dispatch(barrier.close(source_node, metadata))
                if barrier.complete:
                    assert barrier.current is not None
                    send(
                        "turn_commit",
                        pa.array([barrier.current.turn_id], type=pa.uint64()),
                        barrier.current.metadata(),
                    )
        except ProtocolError as exc:
            print(f"ADR-30 protocol error: {exc}", file=sys.stderr)
            raise


if __name__ == "__main__":
    main()
