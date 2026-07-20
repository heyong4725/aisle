"""Reset service node (SPEC 040 RST-1/RST-2).

Teleport requests (mode 0) pass through to the bridge, which owns state
injection (BRG-4); replies flow back with metadata intact so the <2 s
RST-1 budget is auditable via t_reset_ms. Behavioral mode (RST-2) is
Phase 2: it is refused loudly here, never silently downgraded.
"""

from __future__ import annotations

import sys

import numpy as np

TELEPORT, BEHAVIORAL = 0, 1


def route_reset(mode: int) -> str:
    """Pure dispatch (unit-tested): teleport -> bridge; behavioral -> Phase 2."""
    if mode == TELEPORT:
        return "bridge"
    if mode == BEHAVIORAL:
        raise NotImplementedError("behavioral reset is Phase 2 (RST-2)")
    raise ValueError(f"reset mode must be 0 or 1, got {mode}")


def main() -> None:
    import pyarrow as pa
    from dora import Node

    node = Node()
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "reset":
            payload = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.uint32
            ).reshape(-1)
            try:
                if payload.shape[0] != 2:
                    raise ValueError(f"reset payload must be UInt32[2], got {payload.shape}")
                route_reset(int(payload[1]))
            except (ValueError, NotImplementedError) as refusal:
                # refuse THIS request loudly without killing the service: the
                # requester gets a correlated error reply, later teleports
                # still work (TC-6; ADR-8)
                print(f"reset refused: {refusal}", file=sys.stderr)
                node.send_output(
                    "reset_done",
                    pa.array(np.array([0], dtype=np.uint32)),
                    metadata={**metadata, "error": str(refusal)},
                )
                continue
            node.send_output("bridge_reset", pa.array(payload), metadata=metadata)
        elif event["id"] == "reset_done":
            node.send_output("reset_done", event["value"], metadata=metadata)


if __name__ == "__main__":
    main()
