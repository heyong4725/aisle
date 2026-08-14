"""Dora helper for ADR-30 wall-cost measurements."""

import json
import os
import time
from pathlib import Path

from dora import Node


def main() -> None:
    output = Path(os.environ["AISLE_HORIZON_OUTPUT"])
    horizon_ns = int(float(os.environ["AISLE_HORIZON_S"]) * 1e9)
    first_sim_ns = None
    first_wall = None
    node = Node()
    for event in node:
        if event.get("type") != "INPUT" or event.get("id") != "joint_state":
            continue
        sim_ns = int((event.get("metadata") or {}).get("sim_time_ns", -1))
        if sim_ns < 0:
            continue
        if first_sim_ns is None:
            first_sim_ns = sim_ns
            first_wall = time.monotonic()
        if sim_ns - first_sim_ns >= horizon_ns:
            output.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "sim_s": (sim_ns - first_sim_ns) / 1e9,
                        "wall_s": time.monotonic() - first_wall,
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            return


if __name__ == "__main__":
    main()
