"""Navigation action node (SPEC 210 MOB-2): the running dora ACTION that
consumes `nav_goal` (goal_id pattern, TC-7), `base_pose`, and ticks, and
publishes `nav_feedback` / `nav_result` (the >=2 Hz lifecycle) plus the
diff-drive `base_cmd` that drives the base toward the goal.

The lifecycle and controller are pure (aisle.mobility.nav) and unit-tested;
this file is the dora wiring (CON-12: dora imported inside main). base_cmd
is published to the budget guard (MOB-3), never straight to the bridge.
"""

from __future__ import annotations

import os


def main() -> None:
    import json
    import sys

    import numpy as np
    import pyarrow as pa
    from dora import Node

    from aisle.mobility.guard import load_base_limits
    from aisle.mobility.nav import (
        NavStateMachine,
        base_cmd_toward,
        load_locations,
        load_nav_params,
        resolve_nav_goal,
    )
    from aisle.topics import make_sender

    embodiment = os.environ.get("AISLE_EMBODIMENT", "mobile")
    limits = load_base_limits(embodiment)
    locations = load_locations()
    machine = NavStateMachine(**load_nav_params(embodiment))

    node = Node()
    send = make_sender(node)

    def send_base_cmd(v: float, omega: float, goal_id: str) -> None:
        send("base_cmd", pa.array(np.array([v, omega], dtype=np.float32)), {"goal_id": goal_id})

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "nav_goal":
            goal = json.loads(event["value"][0].as_py())
            try:
                target = resolve_nav_goal(goal, locations)
            except ValueError as exc:  # MOB-2: never drive to a silent default
                print(f"nav_goal rejected: {exc}", file=sys.stderr)
                continue
            if not machine.on_goal(target, metadata.get("goal_id", "")):
                print(f"nav goal {metadata.get('goal_id')} refused: nav active", file=sys.stderr)
        elif event["id"] == "base_pose":
            machine.on_base_pose(event["value"].to_numpy(zero_copy_only=False).tolist())
        elif event["id"] == "tick":
            # drive toward the target THIS tick (if navigating), then advance
            # the lifecycle; on a terminal result, stop the base
            if machine.target is not None and machine.pose is not None:
                v, omega = base_cmd_toward(machine.pose, machine.target, limits)
                send_base_cmd(v, omega, machine.goal_id or "")
            emissions = machine.on_tick()
            for topic, payload, goal_id in emissions:
                send(topic, pa.array([json.dumps(payload)]), {"goal_id": goal_id})
            if any(topic == "nav_result" for topic, _, _ in emissions):
                send_base_cmd(0.0, 0.0, "")


if __name__ == "__main__":
    main()
