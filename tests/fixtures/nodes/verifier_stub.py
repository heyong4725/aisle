"""Test fixture node: a scripted verifier that closes the episode loop from
LIVE bridge observations — it emits feedback and a schema-valid
episode_result only after actually receiving oracle_state from the bridge,
so a dead bridge fails the acceptance test (TC-7/TC-8 shapes)."""

import json

import pyarrow as pa
from dora import Node


def main() -> None:
    node = Node()
    goal_id = None
    oracle_seen = 0
    last_sim_time_ns = 0
    done = False
    for event in node:
        if event["type"] != "INPUT" or done:
            continue
        if event["id"] == "episode_goal":
            goal_id = (event.get("metadata") or {}).get("goal_id")
        elif event["id"] == "oracle_state" and goal_id is not None:
            oracle_seen += 1
            last_sim_time_ns = int((event.get("metadata") or {}).get("sim_time_ns", 0))
            if oracle_seen in (5, 10):
                feedback = {"t": last_sim_time_ns / 1e9, "phase": "reach"}
                node.send_output(
                    "episode_feedback",
                    pa.array([json.dumps(feedback)]),
                    metadata={"goal_id": goal_id},
                )
            if oracle_seen == 15:
                result = {
                    "status": "success",
                    "failure": None,
                    "t_end": last_sim_time_ns / 1e9,
                    "seed": 7,
                    "goal_id": goal_id,
                    "verifier": "oracle",
                }
                node.send_output(
                    "episode_result",
                    pa.array([json.dumps(result)]),
                    metadata={"goal_id": goal_id},
                )
                done = True


if __name__ == "__main__":
    main()
