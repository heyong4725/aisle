"""Test fixture (MOB-3 keep-out): every tick send an EXTENDED arm pose
(flange ~0.55 m, past the reach threshold), a base_pose 0.32 m from the
shelf face, and a forward base_cmd toward it — so the guard's keep-out must
clamp the base to a stop."""

import numpy as np
import pyarrow as pa
from dora import Node

# flange xy ~0.548 m > arm_extended_reach_m (0.40): the arm is reaching
_EXTENDED = [0.0, 0.0, 0.0, -1.5, 0.0, 1.5, 0.785, 0.04, 0.04]
_POSE_NEAR_SHELF = [0.0, 0.0, 0.0]  # 0.32 m from the shelf AABB, heading +x
_FORWARD = [0.8, 0.0]  # toward the shelf, above any keep-out cap


def main() -> None:
    node = Node()
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "tick":
            node.send_output(
                "joint_cmd", pa.array(np.array(_EXTENDED, dtype=np.float32)), metadata={}
            )
            node.send_output(
                "base_pose", pa.array(np.array(_POSE_NEAR_SHELF, dtype=np.float32)), metadata={}
            )
            node.send_output(
                "base_cmd", pa.array(np.array(_FORWARD, dtype=np.float32)), metadata={}
            )


if __name__ == "__main__":
    main()
