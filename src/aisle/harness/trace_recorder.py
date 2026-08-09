"""Trace recorder node (SPEC 070 HAR-4): Arrow IPC traces of every wired
topic plus the overhead video.

EVERY wired topic becomes $AISLE_TRACE_DIR/<topic>.arrow in Arrow IPC
STREAM format — readable batch-by-batch even if the recorder dies before
a clean close (the FILE format needs a footer, and a SIGKILL'd recorder
left unreadable truncated files in the T09 smoke). Numeric payloads fill
the data column, JSON payloads the text column, image topics record
metadata-only rows with pixels in overhead.mp4 (10 fps; ADR-11). SIGTERM
is handled so teardown flushes writers. Measurement only: this node runs in
the rollout runner's INSTRUMENTED copy of the graph, which is never the
graph that the HAR-2 validation gate checks — VAL-6's oracle isolation
governs the composed graph, not the harness's own recording (ADR-11).

$AISLE_FRAME_CAPTURE_PERIOD_S (default 0 = off) additionally persists RAW
pixels as `frames/<camera>/<sim_time_ns>.npz` at VER-9's judged-frame
cadence — ADR-11 clause 14. `CaptureSchedule` reproduces
`checkpoint_stamps`' choice online: last frame at or before each
boundary, counted from each episode's goal receipt. The mp4
is lossy, 10 fps and carries no depth at all, so a run recorded without
this captures NOTHING the realistic verifier can replay (VER-5/VER-6):
its containment and upright stages need the overhead depth, and byte
equality is what makes a replay a replay. Capture is opt-in because it is
expensive: 140 KB per instant measured over a live two-episode run
(480x640 RGB + depth, 240x320 wrist), so a 600 s episode at the VER-9
5 s cadence costs ~17 MB.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa

TRACE_SCHEMA = pa.schema(
    [
        ("sim_time_ns", pa.int64()),
        ("env_id", pa.int32()),
        ("seq", pa.int64()),
        # exactly one of data/text is non-null per row: numeric payloads in
        # data, JSON payloads in text; image topics record metadata-only
        # rows (both null) — pixels live in the mp4 (ADR-11)
        ("data", pa.list_(pa.float64())),
        ("text", pa.string()),
    ]
)
# rows buffered per topic before a batch is written: one batch per message
# costs ~300 bytes of IPC framing per ~40-byte row
BATCH_ROWS = 100
# ALL buffers also flush on this wall cadence: low-rate endpoints (an
# episode_result has ~2 rows per run) never reach BATCH_ROWS, and the
# SIGTERM exit-flush cannot be relied on inside dora's blocking event
# read (PR #11 round 2) — periodic flushing bounds any loss to this many
# seconds of tail
FLUSH_EVERY_S = 5.0
# endpoints whose rows are METADATA-ONLY. Anything not listed here goes down the
# generic numeric path, which calls .tolist() on the payload: a 640x480
# seg_overhead mask is 307,200 float64 list values per frame, ~2.5 MB, ~37 MB/s
# at 15 Hz, buffered BATCH_ROWS deep. (Earlier revisions of this comment cited
# "ADR-11's ~17 MB budget" — that figure is ADR-11's MEASUREMENT of the opt-in
# npz capture set for a 600 s episode, as this module's own docstring states,
# not a trace budget. The arithmetic above is what justifies the routing.)
IMAGE_ENDPOINTS = ("__rgb_overhead", "__rgb_wrist", "__depth_overhead", "__seg_overhead")


def is_image_endpoint(topic: str) -> bool:
    """Whether this endpoint's row is METADATA-ONLY (ADR-11, TC-9).

    A named function, not an inline `topic.endswith(IMAGE_ENDPOINTS)`, so the
    routing decision itself is what a test binds: asserting only on the
    constant left `IMAGE_ENDPOINTS[:-1]` at the call site green while masks went
    back down the numeric path."""
    return topic.endswith(IMAGE_ENDPOINTS)


def decode_frame(metadata: dict, value) -> np.ndarray | None:
    """The RAW pixels of an image payload, or None when the metadata does
    not describe a frame. `enc` is the bridge's own declaration (BRG-2),
    never inferred from the topic name: a depth stream decoded as uint8
    would be silently wrong rather than absent."""
    h, w = int(metadata.get("h", 0)), int(metadata.get("w", 0))
    if not (h and w):
        return None
    enc = metadata.get("enc")
    if enc == "rgb8":
        return np.asarray(value.to_numpy(zero_copy_only=False), dtype=np.uint8).reshape(h, w, 3)
    if enc == "depth32f":
        return np.asarray(value.to_numpy(zero_copy_only=False), dtype=np.float32).reshape(h, w)
    return None


class CaptureSchedule:
    """VER-9's judged-frame selector, run online.

    `checkpoint_stamps()` snaps each wanted stamp to the nearest rendered
    frame AT OR BEFORE it, and counts checkpoints from GOAL RECEIPT. A
    recorder cannot see future frames, so it reproduces the same choice by
    retaining the newest frame and writing it once a later frame proves the
    boundary has passed. Both details are what make the persisted set the
    judged set rather than merely a periodic sample (PR #105 review):

    * at-or-BEFORE: with renders at 4.967 s and 5.033 s around a 5.000 s
      checkpoint, the verifier judges 4.967 s — writing the current frame
      when the boundary is crossed would persist 5.033 s and no byte
      comparison (VER-7) or replay (VER-6) could hold;
    * goal-RELATIVE: a process-global schedule phase-shifts every episode
      that does not start on a boundary, so `start()` re-bases it on each
      `episode_goal` stamp.
    """

    def __init__(self, period_ns: int):
        self.period_ns = int(period_ns)
        self.next_boundary_ns: int | None = None

    @property
    def enabled(self) -> bool:
        """Capture is off entirely at period 0 (HAR-4 records the mp4
        either way)."""
        return bool(self.period_ns)

    def start(self, goal_sim_time_ns: int) -> None:
        """Re-base the schedule on an episode's goal receipt (VER-9)."""
        if self.enabled:
            self.next_boundary_ns = int(goal_sim_time_ns)

    def crossed(self, sim_time_ns: int) -> bool:
        """True when `sim_time_ns` proves the pending boundary has passed,
        so the RETAINED frame is the last one at or before it. A frame
        landing exactly ON the boundary is itself the right frame, so it is
        retained rather than triggering — the next frame writes it."""
        return (
            self.enabled
            and self.next_boundary_ns is not None
            and sim_time_ns > self.next_boundary_ns
        )

    def advance(self, sim_time_ns: int) -> None:
        """Step past every boundary this frame has overtaken. Skipped
        boundaries are gaps in the render stream, not recoverable frames."""
        if not self.enabled or self.next_boundary_ns is None:
            return
        while sim_time_ns > self.next_boundary_ns:
            self.next_boundary_ns += self.period_ns


def capture_frames(frames_dir: Path, latest: dict[str, tuple[int, np.ndarray]]) -> int | None:
    """Persist the retained payloads as one npz per camera, keyed by the
    frame's own sim stamp, and return the overhead stamp written.

    Returns None — capturing NOTHING — unless the overhead rgb and depth
    share a stamp: the verifier's geometry back-projects the two together
    (VER-10/VER-11), so a pair from different ticks is worse than a gap.
    BRG-2 renders both in one pass whenever both are due, so a mismatch
    means the frames did not come from one render."""
    rgb, depth = latest.get("rgb_overhead"), latest.get("depth_overhead")
    if rgb is None or depth is None or rgb[0] != depth[0]:
        return None
    write_camera_frame(frames_dir, "overhead", rgb[0], {"rgb": rgb[1], "depth": depth[1]})
    wrist = latest.get("rgb_wrist")
    if wrist is not None:
        write_camera_frame(frames_dir, "wrist", wrist[0], {"rgb": wrist[1]})
    return rgb[0]


def write_camera_frame(
    frames_dir: Path, camera: str, sim_time_ns: int, arrays: dict[str, np.ndarray]
) -> Path:
    out = frames_dir / camera
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{sim_time_ns:020d}.npz"
    np.savez_compressed(path, **arrays)
    return path


def load_frames(trace_dir: Path) -> dict[str, dict[int, dict[str, np.ndarray]]]:
    """`frames[camera][sim_time_ns] -> {"rgb", "depth"}` — exactly the
    mapping `verifier.realistic.judge_frames` consumes, so an offline
    replay (VER-6) reads the same arrays the live judge saw. Empty when
    the run was recorded without frame capture."""
    frames: dict[str, dict[int, dict[str, np.ndarray]]] = {}
    for camera_dir in sorted((Path(trace_dir) / "frames").glob("*")):
        if not camera_dir.is_dir():
            continue
        per_stamp = {}
        for path in sorted(camera_dir.glob("*.npz")):
            with np.load(path) as data:
                per_stamp[int(path.stem)] = {name: data[name] for name in data.files}
        if per_stamp:
            frames[camera_dir.name] = per_stamp
    return frames


def main() -> None:
    import imageio.v2 as imageio
    from dora import Node

    trace_dir = Path(os.environ["AISLE_TRACE_DIR"])
    trace_dir.mkdir(parents=True, exist_ok=True)

    schema = TRACE_SCHEMA
    writers: dict = {}
    buffers: dict[str, list] = {}

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
                    pa.array([r[4] for r in rows], pa.string()),
                ],
                schema=schema,
            )
        )
        buffers[topic] = []

    def buffer_row(topic, metadata, data, text) -> None:
        buffers.setdefault(topic, []).append(
            (
                int(metadata.get("sim_time_ns", 0)),
                int(metadata.get("env_id", 0)),
                int(metadata.get("seq", 0)),
                data,
                text,
            )
        )
        if len(buffers[topic]) >= BATCH_ROWS:
            flush(topic)

    video = None
    frame_shape: tuple[int, int] | None = None
    last_flush = time.monotonic()

    schedule = CaptureSchedule(
        int(float(os.environ.get("AISLE_FRAME_CAPTURE_PERIOD_S", "0")) * 1e9)
    )
    frames_dir = trace_dir / "frames"
    latest: dict[str, tuple[int, np.ndarray]] = {}

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # run finally
    node = Node()
    try:
        for event in node:
            now = time.monotonic()
            if now - last_flush > FLUSH_EVERY_S:
                last_flush = now
                for pending in list(buffers):
                    flush(pending)
            if event["type"] != "INPUT":
                continue
            topic = event["id"]  # <producer>__<topic> endpoint key
            metadata = event.get("metadata") or {}
            if is_image_endpoint(topic):
                # image endpoints: metadata-only rows; overhead pixels go to
                # the mp4 and, when capture is on, raw arrays (ADR-11).
                # seg_overhead (TC-9, L1) belongs here or nowhere: the generic
                # numeric path below would call .tolist() on 640x480 = 307,200
                # float64s per frame, ~2.5 MB, ~37 MB/s at 15 Hz. decode_frame
                # returns None for enc "seg_i32", so a mask cannot reach the mp4
                # or the VER-9 capture set — the row stays metadata-only, which
                # is what it should be: nothing judges a segmentation mask.
                stream = topic.rsplit("__", 1)[-1]
                frame = decode_frame(metadata, event["value"])
                if frame is not None:
                    sim_time_ns = int(metadata.get("sim_time_ns", 0))
                    # BEFORE retaining this frame: if it proves the pending
                    # boundary has passed, the frame still retained is the
                    # last one at or before it — the one VER-9 judges
                    if schedule.crossed(sim_time_ns):
                        if capture_frames(frames_dir, latest) is not None:
                            schedule.advance(sim_time_ns)
                    latest[stream] = (sim_time_ns, frame)
                    if stream == "rgb_overhead":
                        h, w = frame.shape[0], frame.shape[1]
                        if video is None:
                            video = imageio.get_writer(
                                trace_dir / "overhead.mp4", fps=10, macro_block_size=1
                            )
                            frame_shape = (h, w)
                        if (h, w) == frame_shape:
                            video.append_data(frame)
                buffer_row(topic, metadata, None, None)
                continue
            value = event["value"]
            if pa.types.is_string(value.type) or pa.types.is_large_string(value.type):
                # JSON payloads fill the text column of the same trace
                buffer_row(topic, metadata, None, value[0].as_py())
                if schedule.enabled and topic.endswith("__episode_goal"):
                    # VER-9 counts checkpoints from GOAL RECEIPT, so the
                    # schedule is re-based per episode rather than global
                    schedule.start(int(metadata.get("sim_time_ns", 0)))
                if schedule.enabled and topic.endswith("__episode_result"):
                    # the TERMINAL frame is always judged (VER-9), and it is
                    # the one a mid-period episode end would otherwise drop
                    capture_frames(frames_dir, latest)
                continue
            values = np.asarray(value.to_numpy(zero_copy_only=False), dtype=np.float64).reshape(-1)
            buffer_row(topic, metadata, values.tolist(), None)
    finally:
        for topic in list(buffers):
            flush(topic)
        for writer in writers.values():
            writer.close()
        if video is not None:
            video.close()


if __name__ == "__main__":
    main()
