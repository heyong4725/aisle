"""Test fixture: a genesis-free kinematic base. Integrates base_cmd into a
base_pose each tick (aisle.mobility.base.integrate_base_pose) and publishes
it, closing the nav control loop for the MOB-2 lifecycle test without a sim."""

import os

import numpy as np
import pyarrow as pa
from dora import Node

from aisle.mobility.base import integrate_base_pose


def main() -> None:
    dt = float(os.environ.get("MOCK_DT", "0.02"))
    pose = [0.0, 0.0, 0.0]
    cmd = [0.0, 0.0]
    node = Node()
    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "base_cmd":
            c = event["value"].to_numpy(zero_copy_only=False)
            cmd = [float(c[0]), float(c[1])]
        elif event["id"] == "tick":
            pose = integrate_base_pose(pose, cmd, dt)
            node.send_output("base_pose", pa.array(np.array(pose, dtype=np.float32)), metadata={})


if __name__ == "__main__":
    main()
