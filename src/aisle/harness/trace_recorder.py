"""Trace recorder node (SPEC 070 HAR-4): Arrow IPC traces of every wired
topic plus the overhead video.

Each numeric topic becomes $AISLE_TRACE_DIR/<topic>.arrow in Arrow IPC
STREAM format — readable batch-by-batch even if the recorder dies before
a clean close (the FILE format needs a footer, and a SIGKILL'd recorder
left unreadable truncated files in the T09 smoke); rgb_overhead
additionally streams into overhead.mp4 (10 fps). SIGTERM is handled so
teardown flushes writers. Measurement only: this node runs in
the rollout runner's INSTRUMENTED copy of the graph, which is never the
graph that the HAR-2 validation gate checks — VAL-6's oracle isolation
governs the composed graph, not the harness's own recording (ADR-11).
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa

TRACE_SCHEMA = pa.schema(
    [
        ("sim_time_ns", pa.int64()),
        ("env_id", pa.int32()),
        ("seq", pa.int64()),
        ("data", pa.list_(pa.float64())),
    ]
)
# rows buffered per topic before a batch is written: one batch per message
# costs ~300 bytes of IPC framing per ~40-byte row; truncation loss on a
# hard kill stays bounded at this many rows
BATCH_ROWS = 100


def main() -> None:
    import json

    import imageio.v2 as imageio
    from dora import Node

    trace_dir = Path(os.environ["AISLE_TRACE_DIR"])
    trace_dir.mkdir(parents=True, exist_ok=True)

    schema = TRACE_SCHEMA
    writers: dict = {}
    buffers: dict[str, list] = {}
    json_files: dict = {}

    def flush(topic: str) -> None:
        rows = buffers.get(topic)
        if not rows:
            return
        if topic not in writers:
            writers[topic] = pa.ipc.new_stream(trace_dir / f"{topic}.arrow", schema)
        writers[topic].write_batch(
            pa.record_batch(
                [
                    pa.array([r[0] for r in rows], pa.int64()),
                    pa.array([r[1] for r in rows], pa.int32()),
                    pa.array([r[2] for r in rows], pa.int64()),
                    pa.array([r[3] for r in rows], pa.list_(pa.float64())),
                ],
                schema=schema,
            )
        )
        buffers[topic] = []

    video = None
    frame_shape: tuple[int, int] | None = None

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run finally
    node = Node()
    try:
        for event in node:
            if event["type"] != "INPUT":
                continue
            topic = event["id"]
            metadata = event.get("metadata") or {}
            if topic == "rgb_overhead":
                h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
                if h and w:
                    frame = np.asarray(
                        event["value"].to_numpy(zero_copy_only=False), dtype=np.uint8
                    ).reshape(h, w, 3)
                    if video is None:
                        video = imageio.get_writer(
                            trace_dir / "overhead.mp4", fps=10, macro_block_size=1
                        )
                        frame_shape = (h, w)
                    if (h, w) == frame_shape:
                        video.append_data(frame)
                continue
            value = event["value"]
            if pa.types.is_string(value.type) or pa.types.is_large_string(value.type):
                # JSON-payload topics (violation, guard_stats, episode_*)
                # land in per-topic sidecars
                if topic not in json_files:
                    json_files[topic] = open(trace_dir / f"{topic}.jsonl", "w", buffering=1)
                json_files[topic].write(
                    # default=str: dora metadata can carry datetimes
                    json.dumps(
                        {"metadata": dict(metadata), "payload": value[0].as_py()}, default=str
                    )
                    + "\n"
                )
                continue
            values = np.asarray(value.to_numpy(zero_copy_only=False), dtype=np.float64).reshape(-1)
            buffers.setdefault(topic, []).append(
                (
                    int(metadata.get("sim_time_ns", 0)),
                    int(metadata.get("env_id", 0)),
                    int(metadata.get("seq", 0)),
                    values.tolist(),
                )
            )
            if len(buffers[topic]) >= BATCH_ROWS:
                flush(topic)
    finally:
        for topic in list(buffers):
            flush(topic)
        for writer in writers.values():
            writer.close()
        for f in json_files.values():
            f.close()
        if video is not None:
            video.close()


if __name__ == "__main__":
    main()
