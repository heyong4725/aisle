"""Lossless judged-frame capture in the trace recorder (HAR-4, ADR-11
clause 14) and the replay mapping the realistic verifier consumes
(VER-5/VER-6)."""

import numpy as np
import pyarrow as pa
import pytest

from aisle.harness.trace_recorder import (
    capture_frames,
    decode_frame,
    due_for_capture,
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


def test_due_for_capture_is_off_at_period_zero():
    """ADR-11 clause 14: capture is opt-in — the default run records the
    mp4 and nothing else."""
    assert not due_for_capture(10**12, 0, 0)


def test_due_for_capture_fires_at_the_period_boundary():
    """VER-9's judged-frame cadence: the first frame at or after each
    boundary (renders are rate-limited, so the exact stamp rarely
    exists)."""
    assert not due_for_capture(4_000_000_000, 5_000_000_000, 5_000_000_000)
    assert due_for_capture(5_033_000_000, 5_000_000_000, 5_000_000_000)


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
