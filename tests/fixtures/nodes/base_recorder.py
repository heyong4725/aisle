"""Test fixture: records base topic payloads (+ dtype, metadata, wall_t) to
a JSONL file for mobile graph tests (SPEC 210). Optionally bounds the
capture to $RECORDER_DURATION_S of wall time, started at the FIRST event, so
a live test does not wait the whole outer timeout (the genesis build budget
stays outside the window). Unset -> record until the dataflow is torn down."""

import json
import os
import time

from dora import Node


def main() -> None:
    duration = (
        float(os.environ["RECORDER_DURATION_S"]) if "RECORDER_DURATION_S" in os.environ else None
    )
    out = open(os.environ["REC_OUT"], "w", buffering=1)
    node = Node()
    deadline = None
    for event in node:
        if event["type"] != "INPUT":
            continue
        now = time.monotonic()
        # the window opens at the first event (after the genesis build)
        if duration is not None:
            if deadline is None:
                deadline = now + duration
            elif now > deadline:
                break
        arrow = event["value"]
        value = arrow.to_numpy(zero_copy_only=False).tolist()
        out.write(
            json.dumps(
                {
                    "id": event["id"],
                    "value": value,
                    "dtype": str(arrow.type),  # observed Arrow dtype (schema conformance)
                    "wall_t": now,  # consumer wall time (TC-4 rate check)
                    "meta": dict(event.get("metadata") or {}),
                },
                default=str,  # dora stamps a datetime in metadata (see recorder.py)
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
