"""TC-A1–A3 client: boot reset and commands advance only on BRG-1 turns."""

import json
import math
import os

import pyarrow as pa

from aisle.topics import make_sender
from aisle.turn_node import Node


def main():
    node = Node()
    send = make_sender(node)
    mode = os.environ["DRIVER_MODE"]
    n_dof = int(os.environ.get("DRIVER_N_DOF", "9"))
    seeds = [int(s) for s in os.environ.get("DRIVER_RESET_SEEDS", "1").split(",")]
    spacing = int(os.environ.get("DRIVER_RESET_SPACING", "10"))
    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "reset_refused":
            raise RuntimeError(f"contract reset refused: {event['metadata']}")
        if event["id"] != "turn":
            continue
        turn = int(event["metadata"]["turn_id"])
        if mode == "reset":
            if turn % spacing == 0 and turn // spacing < len(seeds):
                index = turn // spacing
                seed = seeds[index]
                send(
                    "reset",
                    pa.array([seed, 0], type=pa.uint32()),
                    {"request_id": f"req-{index + 1}-{seed}"},
                )
        elif turn == 0:
            send(
                "reset",
                pa.array([int(os.environ["DRIVER_SEED"]), 0], type=pa.uint32()),
                {"request_id": "boot-reset"},
            )
        elif mode == "conformance":
            target = [0.1 * math.sin(turn / 20 + i) for i in range(n_dof)]
            send("joint_cmd", pa.array(target, type=pa.float32()), {})
            send("joint_cmd", pa.array([x * 0.99 for x in target], type=pa.float32()), {})
            if turn % 10 == 0:
                send("gripper_cmd", pa.array([0.5], type=pa.float32()), {})
        elif mode == "episode" and turn == 1:
            goal = {"tier": "T0", "target_med": "ibuprofen", "timeout_s": 30, "seed": 7}
            send("episode_goal", pa.array([json.dumps(goal)]), {"goal_id": "ep-0001"})


if __name__ == "__main__":
    main()
