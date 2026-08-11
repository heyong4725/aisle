"""Test fixture (MOB-3 watchdog): send ONE forward base_cmd then go silent
on the command topic, while publishing a base_pose stream each tick — the
watchdog's clock (ADR-28) — so the guard must stop the latched command with
[0,0]. Poses carry advancing sim stamps by default (the sim-time staleness
path); LATCH_STAMP_POSES=0 publishes them UNSTAMPED (a blind sim clock the
wall net must still stop), and LATCH_POSE_COUNT=N stops the pose stream
after N ticks (a HUNG sim: only the guard's stats-tick sweep can act)."""

import os

import numpy as np
import pyarrow as pa
from dora import Node

_DT_NS = 20_000_000  # 20 ms of sim per tick, mirroring the 50 Hz contract


def main() -> None:
    stamp_poses = os.environ.get("LATCH_STAMP_POSES", "1") != "0"
    pose_count = int(os.environ.get("LATCH_POSE_COUNT", "0"))  # 0 = unlimited
    node = Node()
    sent = False
    sim_ns = 0
    poses = 0
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "tick":
            if not sent:
                node.send_output(
                    "base_cmd", pa.array(np.array([0.5, 0.0], dtype=np.float32)), metadata={}
                )
                sent = True
            if pose_count and poses >= pose_count:
                continue
            sim_ns += _DT_NS
            poses += 1
            node.send_output(
                "base_pose",
                pa.array(np.zeros(3, dtype=np.float32)),
                metadata={"sim_time_ns": sim_ns} if stamp_poses else {},
            )


if __name__ == "__main__":
    main()
