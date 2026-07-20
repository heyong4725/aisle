"""Reset service node (SPEC 040 RST-1/RST-2).

Teleport requests (mode 0) pass through to the bridge, which owns state
injection (BRG-4); replies flow back with metadata intact so the <2 s
RST-1 budget is auditable end-to-end. Behavioral mode (RST-2) is Phase 2:
it is refused loudly here, never silently downgraded. Invalid requests
are refused per-request — the service never forwards anything the bridge
would reject, and never dies (TC-6).
"""

from __future__ import annotations

import sys

import numpy as np

from aisle.topics import stamp

TELEPORT, BEHAVIORAL = 0, 1


def route_reset(mode: int) -> str:
    """Pure dispatch (unit-tested): teleport -> bridge; behavioral -> Phase 2."""
    if mode == TELEPORT:
        return "bridge"
    if mode == BEHAVIORAL:
        raise NotImplementedError("behavioral reset is Phase 2 (RST-2)")
    raise ValueError(f"reset mode must be 0 or 1, got {mode}")


def refusal_reply_metadata(request_meta: dict, payload: np.ndarray, error: str) -> dict:
    """TC-6 reply keys for a refused request: echo request_id, seed/mode
    when the payload was well-formed enough to carry them, t_reset_ms=0
    (the sim was never touched), and the error (ADR-8)."""
    meta = {
        "request_id": request_meta.get("request_id", ""),
        "t_reset_ms": 0,
        "error": error,
    }
    if payload.shape[0] == 2:
        meta["seed"], meta["mode"] = int(payload[0]), int(payload[1])
    return meta


def main() -> None:
    import pyarrow as pa
    from dora import Node

    node = Node()
    seq_reply = 0
    seq_forward = 0
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "reset":
            if not metadata.get("request_id"):
                # TC-6 correlates request/reply via request_id: with none
                # there is nothing to reply TO — drop loudly; forwarding
                # would trip the bridge's own TC-6 validation
                print("reset refused: missing request_id metadata (TC-6)", file=sys.stderr)
                continue
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
                seq_reply += 1
                node.send_output(
                    "reset_done",
                    pa.array(np.array([0], dtype=np.uint32)),
                    stamp(refusal_reply_metadata(metadata, payload, str(refusal)), seq_reply),
                )
                continue
            seq_forward += 1
            node.send_output("bridge_reset", pa.array(payload), stamp(metadata, seq_forward))
        elif event["id"] == "reset_done":
            seq_reply += 1
            node.send_output("reset_done", event["value"], stamp(metadata, seq_reply))


if __name__ == "__main__":
    main()
