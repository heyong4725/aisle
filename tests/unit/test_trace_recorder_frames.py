"""Lossless judged-frame capture in the trace recorder (HAR-4, ADR-11
clause 14) and the replay mapping the realistic verifier consumes
(VER-5/VER-6)."""

import numpy as np
import pyarrow as pa
import pytest

from aisle.harness.trace_recorder import (
    CaptureSchedule,
    capture_frames,
    decode_frame,
    load_frames,
)

pytestmark = pytest.mark.unit


def rgb_payload(h=4, w=5):
    frame = np.arange(h * w * 3, dtype=np.uint8).reshape(h, w, 3)
    return frame, {"h": h, "w": w, "enc": "rgb8"}, pa.array(frame.reshape(-1))


def depth_payload(h=4, w=5):
    frame = np.linspace(0.3, 1.7, h * w, dtype=np.float32).reshape(h, w)
    return frame, {"h": h, "w": w, "enc": "depth32f"}, pa.array(frame.reshape(-1))


def test_decode_frame_returns_rgb_pixels_unchanged():
    """HAR-4/ADR-11: an rgb8 payload decodes to the published (h, w, 3)
    array byte for byte — a replay compares identical bytes or it is not
    a replay (VER-7)."""
    frame, metadata, value = rgb_payload()
    decoded = decode_frame(metadata, value)
    assert decoded.dtype == np.uint8
    assert np.array_equal(decoded, frame)


def test_decode_frame_returns_depth_as_float32():
    """VER-10/VER-11 back-project the overhead DEPTH; decoding it as the
    rgb dtype would be silently wrong rather than absent."""
    frame, metadata, value = depth_payload()
    decoded = decode_frame(metadata, value)
    assert decoded.dtype == np.float32
    assert np.array_equal(decoded, frame)


@pytest.mark.parametrize(
    "metadata",
    [
        {"h": 4, "w": 5},  # no encoding declared
        {"h": 4, "w": 5, "enc": "bayer_rg8"},  # an encoding we cannot decode
        {"enc": "rgb8"},  # no shape
    ],
)
def test_decode_frame_refuses_undeclared_payloads(metadata):
    """HAR-4: the recorder decodes only what the bridge DECLARED (BRG-2).
    An undecodable payload yields None so the metadata row is still
    recorded and nothing wrong is persisted as pixels."""
    _, _, value = rgb_payload()
    assert decode_frame(metadata, value) is None


def test_capture_round_trips_into_the_judge_frames_mapping(tmp_path):
    """VER-5/VER-6: what the recorder writes IS `frames[camera][stamp]`
    as `judge_frames` consumes it, with the pixels unchanged."""
    rgb, _, _ = rgb_payload()
    depth, _, _ = depth_payload()
    wrist = np.full((3, 3, 3), 7, dtype=np.uint8)
    latest = {
        "rgb_overhead": (5_000_000_000, rgb),
        "depth_overhead": (5_000_000_000, depth),
        "rgb_wrist": (4_966_000_000, wrist),
    }

    assert capture_frames(tmp_path / "frames", latest) == 5_000_000_000

    frames = load_frames(tmp_path)
    assert sorted(frames) == ["overhead", "wrist"]
    overhead = frames["overhead"][5_000_000_000]
    assert np.array_equal(overhead["rgb"], rgb)
    assert np.array_equal(overhead["depth"], depth)
    assert np.array_equal(frames["wrist"][4_966_000_000]["rgb"], wrist)


def test_capture_refuses_an_overhead_pair_from_different_renders(tmp_path):
    """VER-10/VER-11 fuse the overhead rgb and depth geometrically, so a
    pair from two ticks would measure a scene that never existed. A gap
    is the safe outcome — capture nothing and leave the next boundary
    unmoved."""
    rgb, _, _ = rgb_payload()
    depth, _, _ = depth_payload()
    latest = {
        "rgb_overhead": (5_000_000_000, rgb),
        "depth_overhead": (4_933_000_000, depth),
    }

    assert capture_frames(tmp_path / "frames", latest) is None
    assert load_frames(tmp_path) == {}


def test_capture_refuses_when_depth_is_missing_entirely(tmp_path):
    """The mp4-only failure mode this whole change exists to fix: RGB
    alone cannot drive containment, so it is not recorded as a judgeable
    frame."""
    rgb, _, _ = rgb_payload()
    assert capture_frames(tmp_path / "frames", {"rgb_overhead": (5_000_000_000, rgb)}) is None
    assert load_frames(tmp_path) == {}


def test_load_frames_is_empty_for_a_run_recorded_without_capture(tmp_path):
    """VER-6 must be able to tell "no frames recorded" from "frames
    recorded and empty" without raising on the ordinary run layout."""
    (tmp_path / "overhead.mp4").write_bytes(b"")
    assert load_frames(tmp_path) == {}


def _drive(schedule, frames_dir, stamps, period_start=None):
    """Feed synchronized overhead pairs through the same order main() uses:
    check the boundary against the RETAINED frame, then retain this one."""
    if period_start is not None:
        schedule.start(period_start)
    latest, written = {}, []
    for stamp in stamps:
        if schedule.crossed(stamp):
            if capture_frames(frames_dir, latest) is not None:
                written.append(latest["rgb_overhead"][0])
                schedule.advance(stamp)
        rgb, _, _ = rgb_payload()
        depth, _, _ = depth_payload()
        latest = {"rgb_overhead": (stamp, rgb), "depth_overhead": (stamp, depth)}
    return written


def test_capture_selects_the_frame_at_or_before_the_checkpoint(tmp_path):
    """VER-9 via `checkpoint_stamps()` snaps each checkpoint to the nearest
    rendered frame AT OR BEFORE it. With renders bracketing a 5.000 s
    boundary at 4.967 s and 5.033 s, the verifier judges 4.967 s — so that
    is the frame the recorder must persist, or VER-7 byte equality and
    VER-6 replay compare different pixels (PR #105 review)."""
    schedule = CaptureSchedule(5_000_000_000)
    written = _drive(
        schedule,
        tmp_path / "frames",
        [4_933_000_000, 4_967_000_000, 5_033_000_000, 5_100_000_000],
        period_start=0,
    )
    assert 4_967_000_000 in written
    assert 5_033_000_000 not in written


def test_capture_matches_checkpoint_stamps_on_the_same_frame_set(tmp_path):
    """The strongest form of the requirement: the recorder's ONLINE choice
    equals what the production selector picks OFFLINE over the same
    rendered stamps. Only the terminal frame is missing, because the
    recorder cannot know an episode has ended until `episode_result`
    arrives — main() forces that one separately."""
    from aisle.verifier.realistic import checkpoint_stamps

    stamps = [70_000_000 + i * 33_000_000 for i in range(400)]  # ~30 Hz renders
    written = _drive(
        CaptureSchedule(5_000_000_000), tmp_path / "frames", stamps, period_start=stamps[0]
    )
    expected = checkpoint_stamps(stamps[0], stamps[-1], 5.0, stamps)

    assert written == expected[: len(written)]
    assert expected[len(written) :] == [stamps[-1]]


def test_schedule_is_goal_relative_not_process_global(tmp_path):
    """VER-9 counts checkpoints from GOAL RECEIPT. A process-global
    schedule phase-shifts every episode that does not start on a boundary,
    so two episodes at different phases would get different checkpoint
    offsets from their own goals."""
    period = 5_000_000_000
    first = _drive(
        CaptureSchedule(period),
        tmp_path / "a",
        [i * 1_000_000_000 for i in range(1, 14)],
        period_start=1_000_000_000,
    )
    second = _drive(
        CaptureSchedule(period),
        tmp_path / "b",
        [23_400_000_000 + i * 1_000_000_000 for i in range(13)],
        period_start=23_400_000_000,
    )

    # both episodes capture  their checkpoints at the same OFFSETS from their goals
    assert [s - 1_000_000_000 for s in first] == [s - 23_400_000_000 for s in second]


def test_capture_is_off_at_period_zero(tmp_path):
    """ADR-11 clause 14: capture is opt-in — the default run records the
    mp4 and nothing else."""
    schedule = CaptureSchedule(0)
    assert not schedule.enabled
    assert not schedule.crossed(10**12)
    assert _drive(schedule, tmp_path / "frames", [1, 2, 3], period_start=0) == []


def test_segmentation_endpoint_is_metadata_only():
    """TC-9/HAR-4/ADR-11: `seg_overhead` is routed as an IMAGE endpoint, so its
    row is metadata-only.

    Not a style point. The generic numeric path calls `.tolist()` on the
    payload, and a 640x480 mask is 307,200 values per frame at 15 Hz, buffered
    BATCH_ROWS deep, against ADR-11's ~17 MB budget for an ENTIRE run's trace.
    Routing also keeps masks out of the mp4 and the VER-9 capture set, which
    `decode_frame` enforces independently by declining the `seg_i32` encoding —
    nothing judges a segmentation mask."""
    from aisle.harness.trace_recorder import IMAGE_ENDPOINTS

    # the routing decision the recorder makes, on the constant it makes it with
    assert "dora-genesis__seg_overhead".endswith(IMAGE_ENDPOINTS)
    # and the encoding is not decodable as pixels, so a mask cannot reach the
    # mp4 or the VER-9 capture set even though it is routed as an image
    _, _, value = rgb_payload()
    assert decode_frame({"h": 480, "w": 640, "enc": "seg_i32"}, value) is None
