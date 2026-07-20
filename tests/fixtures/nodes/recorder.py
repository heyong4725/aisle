"""Test fixture node: records every received message as one JSONL line
{id, len, sha256, metadata, wall_t} to $RECORDER_OUT, then exits after
$RECORDER_DURATION_S seconds of wall time."""

import hashlib
import json
import os
import time

import pyarrow as pa
from dora import Node


def main() -> None:
    out_path = os.environ["RECORDER_OUT"]
    duration = float(os.environ.get("RECORDER_DURATION_S", "10"))
    # the window starts at the FIRST event: the bridge's genesis build time
    # (taichi kernel compilation etc.) must not eat the capture window
    deadline = None
    node = Node()
    with open(out_path, "w", buffering=1) as out:
        for event in node:
            now = time.monotonic()
            if deadline is None:
                deadline = now + duration
            elif now > deadline:
                break
            if event["type"] != "INPUT":
                continue
            value = event["value"]
            record = {
                "id": event["id"],
                "len": len(value),
                "metadata": dict(event.get("metadata") or {}),
                "wall_t": time.monotonic(),
            }
            record["dtype"] = str(value.type)
            if pa.types.is_string(value.type) or pa.types.is_large_string(value.type):
                text = value[0].as_py()
                record["text"] = text
                record["sha256"] = hashlib.sha256(text.encode()).hexdigest()
            else:
                arr = value.to_numpy(zero_copy_only=False)
                record["dtype"] = str(value.type)
                if len(value) <= 64:
                    record["values"] = [float(v) for v in arr]
                record["sha256"] = hashlib.sha256(arr.tobytes()).hexdigest()
            out.write(json.dumps(record, default=str) + "\n")


if __name__ == "__main__":
    main()
