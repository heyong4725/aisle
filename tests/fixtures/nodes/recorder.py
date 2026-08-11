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


def main() -> None:
    out_path = os.environ["RECORDER_OUT"]
    duration = float(os.environ.get("RECORDER_DURATION_S", "10"))
    await_spec = os.environ.get("RECORDER_AWAIT", "")
    await_topic, await_count = "", 0
    if await_spec:
        await_topic, _, raw = await_spec.partition(":")
        # fail LOUDLY on a malformed spec: a silent recorder death would
        # burn the settle helper's whole outer deadline with an opaque
        # empty-capture error (PR review)
        if not await_topic or not raw.isdigit() or int(raw) < 1:
            raise ValueError(f"RECORDER_AWAIT must be 'topic:count', got {await_spec!r}")
        await_count = int(raw)
    await_tail = float(os.environ.get("RECORDER_AWAIT_TAIL_S", str(duration)))
    await_sim_ns = int(os.environ.get("RECORDER_AWAIT_SIM_NS", "0"))
    awaited_seen = 0
    sim_target = None  # set at the Nth awaited row when RECORDER_AWAIT_SIM_NS
    max_sim_ns = 0  # newest sim stamp seen across ALL recorded rows
    # the window starts at the FIRST event: the bridge's genesis build time
    # (taichi kernel compilation etc.) must not eat the capture window
    deadline = None
    node = Node()
    with open(out_path, "w", buffering=1) as out:
        for event in node:
            now = time.monotonic()
            if deadline is None:
                deadline = now + duration
            elif (
                now > deadline
                and awaited_seen >= await_count
                and (sim_target is None or max_sim_ns >= sim_target)
            ):
                # explicit completion sentinel: run_dataflow_until_settled
                # stops on it instead of burning its whole outer deadline
                # (written only when an event ARRIVES after the window, i.e.
                # the stream flowed through the whole capture)
                out.write(json.dumps({"id": "__recorder_done__"}) + "\n")
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
            stamp = record["metadata"].get("sim_time_ns")
            if isinstance(stamp, int) and stamp > max_sim_ns:
                max_sim_ns = stamp
            if await_topic and event["id"] == await_topic:
                awaited_seen += 1
                if awaited_seen == await_count:
                    # the awaited protocol completed, however late load made
                    # it: guarantee the post-event tail from HERE — wall tail
                    # always, and a sim-stamp horizon when configured (an
                    # unstamped awaited row cannot anchor a sim horizon; the
                    # wall tail then governs alone)
                    deadline = max(deadline, now + await_tail)
                    if await_sim_ns and isinstance(stamp, int) and stamp > 0:
                        sim_target = stamp + await_sim_ns


if __name__ == "__main__":
    main()
