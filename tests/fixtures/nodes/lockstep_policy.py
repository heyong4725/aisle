"""Dora fixture participant that deliberately reorders data and watermark ports."""

import os
import time

import numpy as np
import pyarrow as pa
from dora import Node

from aisle.turns import TurnStamp, watermark_metadata

HOME = np.array([0.0, -0.7, 0.0, -2.2, 0.0, 1.5, 0.8, 0.04, 0.04], dtype=np.float32)


def main() -> None:
    node = Node()
    watermark_first = os.environ.get("LOCKSTEP_SCHEDULE") == "watermark-first"
    data_seq = {"joint_cmd": 0, "reset": 0, "turn_done": 0}

    for event in node:
        if event.get("type") != "INPUT" or event.get("id") != "turn":
            continue
        metadata = event.get("metadata") or {}
        stamp = TurnStamp(
            int(metadata["turn_epoch"]),
            int(metadata["turn_id"]),
            int(metadata["sim_time_ns"]),
        )
        if stamp.turn_id > 100:
            counts = {"joint_cmd": 0, "reset": 0, "turn_done": 1}
            data_seq["turn_done"] += 1
            node.send_output(
                "turn_done",
                pa.array([stamp.turn_id], type=pa.uint64()),
                {
                    **watermark_metadata(stamp, counts),
                    "source_node": "policy",
                    "seq": data_seq["turn_done"],
                    "shutdown": True,
                },
            )
            return

        topic = "reset" if stamp.turn_id == 0 else "joint_cmd"
        counts = {
            "joint_cmd": int(topic == "joint_cmd"),
            "reset": int(topic == "reset"),
            "turn_done": 1,
        }
        data_seq[topic] += 1
        payload = (
            pa.array(np.array([100, 0], dtype=np.uint32)) if topic == "reset" else pa.array(HOME)
        )
        data_metadata = {
            **stamp.metadata(),
            "env_id": 0,
            "seq": data_seq[topic],
            **({"request_id": "initial-reset"} if topic == "reset" else {}),
        }
        data_seq["turn_done"] += 1
        done_metadata = {
            **watermark_metadata(stamp, counts),
            "source_node": "policy",
            "seq": data_seq["turn_done"],
        }

        if watermark_first:
            node.send_output(
                "turn_done", pa.array([stamp.turn_id], type=pa.uint64()), done_metadata
            )
            # Make the cross-port overtake deterministic instead of hoping
            # the OS schedules the barrier between two adjacent sends.
            time.sleep(0.01)
            node.send_output(topic, payload, data_metadata)
        else:
            node.send_output(topic, payload, data_metadata)
            time.sleep(0.01)
            node.send_output(
                "turn_done", pa.array([stamp.turn_id], type=pa.uint64()), done_metadata
            )


if __name__ == "__main__":
    main()
