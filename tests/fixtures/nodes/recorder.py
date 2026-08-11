"""Test fixture node: records every received message as one JSONL line
{id, len, sha256, metadata, wall_t} to $RECORDER_OUT.

Normally the duration window begins at the first event. When
``RECORDER_WAIT_FOR_ID`` and ``RECORDER_WAIT_FOR_COUNT`` are set, it begins
only after that many matching inputs have been recorded. This lets graph
tests anchor their capture tail to observed evidence rather than a producer's
nominal wall schedule.
"""

import hashlib
import json
import os
import time

import pyarrow as pa


class CaptureWindow:
    """Arm a wall-time tail immediately or after observed stream evidence."""

    def __init__(self, duration_s: float, wait_for_id: str | None, wait_for_count: int):
        if wait_for_id is not None and wait_for_count < 1:
            raise ValueError("RECORDER_WAIT_FOR_COUNT must be >= 1")
        self.duration_s = duration_s
        self.wait_for_id = wait_for_id
        self.wait_for_count = wait_for_count
        self.observed = 0
        self.deadline: float | None = None

    def observe(self, input_id: str, now: float) -> None:
        if self.deadline is not None:
            return
        if self.wait_for_id is None:
            self.deadline = now + self.duration_s
            return
        if input_id == self.wait_for_id:
            self.observed += 1
            if self.observed >= self.wait_for_count:
                self.deadline = now + self.duration_s

    def complete(self, now: float) -> bool:
        return self.deadline is not None and now > self.deadline


def main() -> None:
    from dora import Node

    out_path = os.environ["RECORDER_OUT"]
    duration = float(os.environ.get("RECORDER_DURATION_S", "10"))
    wait_for_id = os.environ.get("RECORDER_WAIT_FOR_ID")
    wait_for_count = int(os.environ.get("RECORDER_WAIT_FOR_COUNT", "0"))
    window = CaptureWindow(duration, wait_for_id, wait_for_count)
    # Without an evidence anchor the window starts at the FIRST event: the
    # bridge's genesis build time (taichi kernel compilation etc.) must not
    # eat the capture window. With an anchor it starts after the Nth matching
    # input has been written, guaranteeing that evidence cannot lose a race
    # with recorder teardown.
    node = Node()
    with open(out_path, "w", buffering=1) as out:
        for event in node:
            now = time.monotonic()
            if window.complete(now):
                # explicit completion sentinel: run_dataflow_until_settled
                # stops on it instead of burning its whole outer deadline
                # (written only when an event ARRIVES after the window, i.e.
                # the stream flowed through the whole capture)
                out.write(json.dumps({"id": "__recorder_done__"}) + "\n")
                break
            if wait_for_id is None:
                window.observe(event.get("id", ""), now)
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
            if wait_for_id is not None:
                # Arm only after the matching row is durably present in the
                # JSONL capture. The duration is a post-evidence tail.
                window.observe(event["id"], now)


if __name__ == "__main__":
    main()
