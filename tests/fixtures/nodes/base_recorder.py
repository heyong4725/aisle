"""Test fixture: records base topic payloads (+ dtype, metadata, wall_t) to
a JSONL file for mobile graph tests (SPEC 210). Optionally bounds the
capture to $RECORDER_DURATION_S of wall time, started at the FIRST event, so
a live test does not wait the whole outer timeout (the genesis build budget
stays outside the window). Unset -> record until the dataflow is torn down.

$RECORDER_AWAIT ("topic:count"), $RECORDER_AWAIT_TAIL_S, and
$RECORDER_AWAIT_SIM_NS carry the recorder.py await semantics (issue #94),
ported here for the ADR-29 wall-net tests (issue #160): those tests assert
a discrete LATE event (the backstop stop) inside a wall-only window of
backstop+margin — the same truncation class issue #94 fixed — so the
window must not be able to close before the awaited row lands. The shared
window state machine is pure and unit-tested (recorder_window.py)."""

import json
import os
import time

from dora import Node

# dora launches this file as a script, so its directory leads sys.path
from recorder_window import CaptureWindow, parse_await_spec


def main() -> None:
    duration = (
        float(os.environ["RECORDER_DURATION_S"]) if "RECORDER_DURATION_S" in os.environ else None
    )
    await_topic, await_count = parse_await_spec(os.environ.get("RECORDER_AWAIT", ""))
    tail_raw = os.environ.get("RECORDER_AWAIT_TAIL_S")
    window = CaptureWindow(
        duration,
        await_topic,
        await_count,
        float(tail_raw) if tail_raw is not None else None,
        int(os.environ.get("RECORDER_AWAIT_SIM_NS", "0")),
    )
    out = open(os.environ["REC_OUT"], "w", buffering=1)
    node = Node()
    for event in node:
        # INPUT filter FIRST: the window must open at the first DATA event,
        # after the genesis build. Advancing it on non-INPUT events would
        # start a 30 s capture behind a 420 s build and truncate it before
        # any data arrived (PR #177 review — this ordering was the
        # pre-refactor behavior and the deleted comment's whole point).
        if event["type"] != "INPUT":
            continue
        now = time.monotonic()
        if window.observe(now):
            # explicit completion sentinel: the runner waits for THIS, so
            # a mid-capture output stall is never mistaken for a finished
            # window (a stall leaves no sentinel and hits the outer cap)
            out.write(json.dumps({"id": "__recorder_done__", "wall_t": now}) + "\n")
            break
        arrow = event["value"]
        value = arrow.to_numpy(zero_copy_only=False).tolist()
        meta = dict(event.get("metadata") or {})
        out.write(
            json.dumps(
                {
                    "id": event["id"],
                    "value": value,
                    "dtype": str(arrow.type),  # observed Arrow dtype (schema conformance)
                    "wall_t": now,  # consumer wall time (TC-4 rate check)
                    "meta": meta,
                },
                default=str,  # dora stamps a datetime in metadata (see recorder.py)
            )
            + "\n"
        )
        window.on_recorded(event["id"], meta.get("sim_time_ns"), now)


if __name__ == "__main__":
    main()
