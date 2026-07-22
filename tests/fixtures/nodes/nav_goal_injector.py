"""Test fixture: send exactly one nav_goal at startup (goal_id in metadata,
TC-7), then idle. $NAV_GOAL is the goal JSON (default: pose [1, 0, 0])."""

import os

import pyarrow as pa
from dora import Node


def main() -> None:
    goal = os.environ.get("NAV_GOAL", '{"pose": [1.0, 0.0, 0.0]}')
    node = Node()
    sent = False
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "tick" and not sent:
            node.send_output("nav_goal", pa.array([goal]), metadata={"goal_id": "g1"})
            sent = True


if __name__ == "__main__":
    main()
