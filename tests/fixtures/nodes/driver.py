"""Test fixture node: drives the bridge according to $DRIVER_MODE.

conformance — sine joint_cmd at the tick rate plus periodic gripper_cmd.
reset       — a seeded reset request every $DRIVER_RESET_SPACING ticks,
              seeds from $DRIVER_RESET_SEEDS (comma-separated).
episode     — one scripted trivial episode: goal, feedback, result carrying
              the goal's goal_id (TC-7 shapes).
multi_env   — env-routed joint_cmds for envs 0 and 1; if
              $DRIVER_SEND_UNROUTED=1, finishes with a cmd missing env_id.
"""

import json
import math
import os

import numpy as np
import pyarrow as pa
from dora import Node


def main() -> None:
    mode = os.environ["DRIVER_MODE"]
    n_dof = int(os.environ.get("DRIVER_N_DOF", "9"))
    node = Node()
    tick = 0
    reset_seeds = [int(s) for s in os.environ.get("DRIVER_RESET_SEEDS", "1").split(",")]
    spacing = int(os.environ.get("DRIVER_RESET_SPACING", "20"))
    sent_resets = 0
    for event in node:
        if event["type"] != "INPUT":
            continue
        tick += 1
        if mode == "conformance":
            target = np.array(
                [0.1 * math.sin(tick / 20 + i) for i in range(n_dof)], dtype=np.float32
            )
            node.send_output("joint_cmd", pa.array(target), metadata={})
            node.send_output("joint_cmd", pa.array(target * 0.99), metadata={})  # forces coalescing
            if tick % 10 == 0:
                node.send_output(
                    "gripper_cmd", pa.array(np.array([0.5], dtype=np.float32)), metadata={}
                )
        elif mode == "reset":
            if tick % spacing == 0 and sent_resets < len(reset_seeds):
                seed = reset_seeds[sent_resets]
                sent_resets += 1
                node.send_output(
                    "reset",
                    pa.array(np.array([seed, 0], dtype=np.uint32)),
                    metadata={"request_id": f"req-{sent_resets}-{seed}"},
                )
        elif mode == "episode":
            # the CLIENT only issues the goal; feedback and the result come
            # from the verifier stub, derived from live bridge observations
            if tick == 1:
                goal = {"tier": "T0", "target_med": "ibuprofen", "timeout_s": 30, "seed": 7}
                node.send_output(
                    "episode_goal", pa.array([json.dumps(goal)]), metadata={"goal_id": "ep-0001"}
                )
        elif mode == "multi_env":
            targets = {
                0: np.array([0.0, -0.6, 0.0, -2.0, 0.0, 1.4, 0.6, 0.02, 0.02], dtype=np.float32),
                1: np.array([0.5, -1.0, 0.3, -2.6, 0.2, 1.8, 1.0, 0.03, 0.03], dtype=np.float32),
            }
            env = tick % 2
            node.send_output("joint_cmd", pa.array(targets[env][:n_dof]), metadata={"env_id": env})
            # periodic: a single early send can be evicted from dora queues
            # while the bridge is still building
            if os.environ.get("DRIVER_SEND_UNROUTED") == "1" and tick % 40 == 0:
                node.send_output("joint_cmd", pa.array(targets[env][:n_dof]), metadata={})


if __name__ == "__main__":
    main()
