"""placement-controller (design doc §11.4, ADR-17): slot-template-relative
fine placement — the neatness skill. The hub stub maps a place_goal {slot,
category} to the slot's world template pose as a TC-1 7d target for the
existing ik/guard/driver stack (decision-class: it plans, it does not
command motion). Pure core, thin dora main (CON-12)."""

from __future__ import annotations

import math


def placement_target(plano: dict, slot_id: str, category: str, meds: dict) -> list[float]:
    """TC-1 [x, y, z, qx, qy, qz, qw]: the item CENTER pose when resting
    exactly at the slot template (RS-4's all-criteria-pass point) — z is
    the board surface plus half the item height, yaw the slot's facing."""
    from aisle.scenes.store import slot_world_pose

    if slot_id not in plano["slots"]:
        raise ValueError(f"unknown slot {slot_id!r}")
    world, yaw = slot_world_pose(plano, slot_id)
    half = yaw / 2
    return [
        world[0],
        world[1],
        world[2] + meds[category]["size"][2] / 2,
        0.0,
        0.0,
        math.sin(half),
        math.cos(half),
    ]


def main() -> None:
    import json

    import numpy as np
    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import load_meds
    from aisle.scenes.store import load_planogram
    from aisle.topics import make_sender

    node = Node()
    send = make_sender(node)
    plano = load_planogram()
    meds = load_meds()
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "place_goal":
            goal = json.loads(event["value"][0].as_py())
            target = placement_target(plano, goal["slot"], goal["category"], meds)
            send(
                "target_pose",
                pa.array(np.asarray(target, dtype=np.float32)),
                event.get("metadata") or {},
            )


if __name__ == "__main__":
    main()
