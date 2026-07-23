"""Test fixture: drives the guard with a MOVING arm command and an
above-creep base command on every tick, so the MOB-3 arm/base mutex must
clamp the base to creep. joint_cmd oscillates joint 0 (always changing ->
arm in motion); base_cmd is a constant forward v above v_creep."""

import math

import numpy as np
import pyarrow as pa
from dora import Node

# franka home (7 arm + 2 finger dofs); joint 0 is oscillated
_HOME = [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785, 0.04, 0.04]


def main() -> None:
    node = Node()
    i = 0
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "tick":
            i += 1
            q = list(_HOME)
            q[0] = 0.15 * math.sin(0.3 * i)  # in-range, always changing
            node.send_output("joint_cmd", pa.array(np.array(q, dtype=np.float32)), metadata={})
            node.send_output(
                "base_cmd", pa.array(np.array([0.5, 0.0], dtype=np.float32)), metadata={}
            )


if __name__ == "__main__":
    main()
