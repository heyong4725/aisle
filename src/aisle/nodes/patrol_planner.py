"""patrol-planner (design doc §11.4, ADR-17): a coverage sequence over the
store's shelf zones as nav goals. The hub stub visits every zone once in
sorted order (deterministic, CON-5); agents may replace it with smarter
coverage. Pure core, thin dora main (CON-12): emits the first nav_goal on
patrol_goal and the next after each nav_result."""

from __future__ import annotations


def patrol_sequence(plano: dict) -> list[dict]:
    """Nav goals covering every shelf zone once, sorted by zone name.
    Zone 'aisle_X' maps to the named location 'shelf_zone_X' (ADR-15
    locations alignment); an unmapped zone is a loud error."""
    goals = []
    for zone in sorted({u["zone"] for u in plano["units"].values()}):
        if not zone.startswith("aisle_"):
            raise ValueError(f"zone {zone!r} has no shelf_zone_* location mapping")
        goals.append({"location": f"shelf_zone_{zone.removeprefix('aisle_').upper()}"})
    return goals


def main() -> None:
    import json
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.store import load_planogram
    from aisle.topics import make_sender

    node = Node()
    send = make_sender(node)
    queue: list[dict] = []
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "patrol_goal":
            queue = patrol_sequence(load_planogram())
            send("nav_goal", pa.array([json.dumps(queue.pop(0))]), metadata)
        elif event["id"] == "nav_result":
            if queue:
                send("nav_goal", pa.array([json.dumps(queue.pop(0))]), metadata)
            else:
                print("patrol complete", file=sys.stderr)


if __name__ == "__main__":
    main()
