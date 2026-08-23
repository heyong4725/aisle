"""return-planner node (T4 increment two, ADR-32 §3; VER-3 amendment):
turns the state machine's `return_request` into a standard grasp plan
that picks the named med FROM THE TRAY and places it at a free shelf
slot — the `return_item` behavior the spec names, riding the existing
guard-gated grasp/IK stack unchanged (the plan's `place_xy` metadata
redirects the place destination; nothing else differs from a pick).

Not the reset service, deliberately: this is an in-episode manipulation
with the episode's own perception constraints. Pose source: the tray
geometry itself — the box to return IS the tray's occupant (goal-start
tray set, VER-1 amendment), so its grasp pose is the tray centre at the
med's known half-height (SCN-2 public geometry), no privileged topic.
"""

from __future__ import annotations


def return_grasp_and_slot(med: str, meds: dict, layout: dict) -> tuple[list, list] | None:
    """(grasp_pose7, place_xy) for returning `med` from the tray to the
    front-most free shelf slot column on level 0. Pure (CON-12)."""
    spec = meds.get(med)
    if spec is None:
        return None
    tray = layout["tray"]
    tx, ty = float(tray["pos"][0]), float(tray["pos"][1])
    tray_top = float(tray["pos"][2]) + float(tray["size"][2]) / 2.0
    grasp_z = tray_top + float(spec["size"][2]) / 2.0
    grasp = [tx, ty, grasp_z, 0.0, 0.0, 0.0, 1.0]
    shelf = layout["shelf"]
    # the return slot: the shelf's front-left corner column at level 0 —
    # empty by construction in T4 scenes (SCN-2 samples interior slots)
    sx = float(shelf["pos"][0]) - float(shelf["level_size"][0]) / 2.0 + float(spec["size"][0])
    sy = float(shelf["pos"][1]) - float(shelf["level_size"][1]) / 2.0 + float(spec["size"][1])
    return grasp, [sx, sy]


def main() -> None:  # pragma: no cover — graph-tested
    import json
    import os
    import sys

    import numpy as np
    import pyarrow as pa

    from aisle.scenes.pharmacy import load_meds, load_physics, resolve_layout
    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    meds = load_meds()
    layout = resolve_layout(load_physics(), os.environ.get("AISLE_EMBODIMENT", "franka"))
    env_pin = env_pin_from_env(os.environ)
    node = Node()
    send = make_sender(node, env_pin)

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if not env_accepts(metadata, env_pin):
            continue
        if event["id"] != "return_request":
            continue
        payload = json.loads(event["value"][0].as_py())
        med = payload.get("return_med", "")
        pair = return_grasp_and_slot(med, meds, layout)
        if pair is None:
            print(f"return refused: unknown med {med!r}", file=sys.stderr)
            continue
        grasp, place_xy = pair
        out_meta = dict(metadata)
        out_meta["place_xy"] = place_xy
        send("grasp_pose", pa.array(np.asarray(grasp, dtype=np.float32)), out_meta)
        print(f"return plan: {med} tray->slot {place_xy}", file=sys.stderr)


if __name__ == "__main__":
    main()
