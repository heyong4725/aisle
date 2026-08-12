"""Test fixture node: records every received message as one JSONL line
{id, len, sha256, metadata, wall_t} to $RECORDER_OUT, then exits after
$RECORDER_DURATION_S seconds of wall time.

$RECORDER_AWAIT ("topic:count", e.g. "reset_done:2") makes the window
AWAIT that many rows of the topic before it may close (issue #94): a
wall-only window guesses when load-stretched events will land, and under
suite load the guess loses — the capture truncated mid-protocol and a
determinism test read its own truncation as a missing reset. With an
await, the deadline cannot expire before the Nth row, and the Nth row
re-anchors it to now + $RECORDER_AWAIT_TAIL_S (default: the duration),
so the post-event tail is guaranteed however late the event ran.
$RECORDER_AWAIT_SIM_NS additionally holds the window open until the
recorded sim stamps advance that many ns past the Nth row's stamp — a
wall tail alone under-covers SIM time when rtf collapses under load
(PR #159 review), and consumers that need N sim-seconds of post-event
data (CON-5 layer (c) windows) should say so in sim units. If the
awaited row NEVER arrives, no sentinel is written and the settle helper
fails loudly at its outer deadline — a real protocol defect stays a
failure, it does not become a truncated pass."""

import hashlib
import json
import os
import time

import pyarrow as pa
from dora import Node

# dora launches this file as a script, so its directory leads sys.path;
# the window state machine is pure and unit-tested (issue #160)
from recorder_window import CaptureWindow, parse_await_spec


def main() -> None:
    out_path = os.environ["RECORDER_OUT"]
    duration = float(os.environ.get("RECORDER_DURATION_S", "10"))
    await_topic, await_count = parse_await_spec(os.environ.get("RECORDER_AWAIT", ""))
    window = CaptureWindow(
        duration,
        await_topic,
        await_count,
        float(os.environ.get("RECORDER_AWAIT_TAIL_S", str(duration))),
        int(os.environ.get("RECORDER_AWAIT_SIM_NS", "0")),
    )
    node = Node()
    with open(out_path, "w", buffering=1) as out:
        for event in node:
            # INPUT filter FIRST: the window starts at the first DATA event
            # (the bridge's genesis build must not eat the capture), and it
            # cannot close before the awaited rows and any sim horizon
            # (issue #94). Non-INPUT events must not advance it — see the
            # same ordering in base_recorder.py (PR #177 review).
            if event["type"] != "INPUT":
                continue
            now = time.monotonic()
            if window.observe(now):
                # explicit completion sentinel: run_dataflow_until_settled
                # stops on it instead of burning its whole outer deadline
                # (written only when an event ARRIVES after the window, i.e.
                # the stream flowed through the whole capture)
                out.write(json.dumps({"id": "__recorder_done__"}) + "\n")
                break
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
            window.on_recorded(event["id"], record["metadata"].get("sim_time_ns"), now)


if __name__ == "__main__":
    main()
