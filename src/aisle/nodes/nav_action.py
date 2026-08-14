"""Navigation action node (SPEC 210 MOB-2): the running dora ACTION that
consumes `nav_goal` (goal_id pattern, TC-7) and `base_pose`, and publishes
`nav_feedback` / `nav_result` (the >=2 Hz lifecycle) plus the diff-drive
`base_cmd` that drives the base toward the goal.

The control loop is clocked by `base_pose` itself (50 Hz SIM cadence,
MOB-1), not a wall timer: exactly one control iteration per serviced pose,
so the command sequence is a function of the sim trajectory alone (CON-5,
ADR-29 — a wall tick raced the pose stream and made the recompute count
host-dependent, issue #71).

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

    from aisle.mobility.guard import load_base_limits, parse_sim_stamp
    from aisle.mobility.nav import (
        NavStateMachine,
        base_cmd_toward,
        load_locations,
        load_nav_params,
        load_near_field_m,
        load_rotate_omega_max,
        nav_goal_is_current,
        resolve_nav_goal,
    )
    from aisle.topics import make_sender
    from aisle.turn_node import Node

    embodiment = os.environ.get("AISLE_EMBODIMENT", "mobile")
    limits = load_base_limits(embodiment)
    locations = load_locations()
    params = load_nav_params(embodiment)
    arrival_tol_m = params["arrival_tol_m"]
    rotate_cap = load_rotate_omega_max(embodiment)
    near_field = load_near_field_m(embodiment)
    machine = NavStateMachine(**params)

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
            # TC-7: check the active state to tell accept from refuse (on_goal
            # returns [] for all three outcomes), so a valid first goal is
            # not mislogged and a STALE one is not mistaken for either
            goal_epoch = metadata.get("episode_epoch")
            if machine.target is not None:
                print(f"nav goal {metadata.get('goal_id')} refused: nav active", file=sys.stderr)
            elif not nav_goal_is_current(machine.episode_epoch, goal_epoch):
                # issue #179 review: emitted before the boundary, delivered
                # after it. Distinct message because the consequence is
                # distinct — accepting it drives the PREVIOUS episode's
                # target and refuses the real goal behind it.
                print(
                    f"nav goal {metadata.get('goal_id')} refused: stale episode "
                    f"(goal epoch {goal_epoch!r}, nav is in {machine.episode_epoch!r})",
                    file=sys.stderr,
                )
            else:
                machine.on_goal(target, metadata.get("goal_id", ""), goal_epoch)
        elif event["id"] == "reset_done":
            # the episode boundary (issue #179). waypoint-nav was the only
            # stateful node in these graphs without this input, so a leg
            # still in flight at a timeout or verifier verdict survived into
            # the next episode: its first nav_goal was refused as
            # "nav active", and the carried leg's nav_result then completed
            # the NEW episode's subtask.
            # the epoch IS reset_done's TC-2 seq, read from the same message
            # the goal's producer reads, so the two cannot drift (issue #179)
            aborted = machine.on_episode_boundary(metadata.get("seq"))
            if aborted is not None:
                print(f"nav goal {aborted} abandoned: episode boundary", file=sys.stderr)
                # stop commanding. The guard emits its own zero at
                # reset_done (MOB-3), so this is belt-and-braces — but nav
                # owns its own output and must not leave a live command as
                # the last thing it said.
                send_base_cmd(0.0, 0.0, "")
        elif event["id"] == "base_pose":
            # the TC-2 sim stamp drives the machine's stall/timeout budgets
            # (SIM seconds, CON-5) — outcomes must not depend on host rtf.
            # parse_sim_stamp is the same TOTAL read the guard uses (BG-3,
            # issue #160 item 1): a malformed/absent/zero stamp must not
            # kill nav's event loop — base_pose is its only clock, and a
            # dead loop latches the last command until the wall net — so it
            # degrades to None and the machine HOLDS its budgets instead
            machine.on_base_pose(
                event["value"].to_numpy(zero_copy_only=False).tolist(),
                parse_sim_stamp(metadata),
            )
            # one control iteration per pose (ADR-29): drive toward the
            # target (if navigating), then advance the lifecycle; on a
            # terminal result, stop the base
            if machine.target is not None and machine.pose is not None:
                v, omega = base_cmd_toward(
                    machine.pose,
                    machine.target,
                    limits,
                    arrival_tol_m,
                    rotate_only=machine.rotating,
                    rotate_omega_max=rotate_cap,
                    near_field_m=near_field,
                )
                send_base_cmd(v, omega, machine.goal_id or "")
            emissions = machine.on_tick()
            for topic, payload, goal_id in emissions:
                send(topic, pa.array([json.dumps(payload)]), {"goal_id": goal_id})
            if any(topic == "nav_result" for topic, _, _ in emissions):
                send_base_cmd(0.0, 0.0, "")


if __name__ == "__main__":
    main()
