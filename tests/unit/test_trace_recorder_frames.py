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


def test_segmentation_endpoint_routing_keeps_the_mask_out_of_the_numeric_path():
    """TC-9/HAR-4/ADR-11: `seg_overhead` is routed as an IMAGE endpoint, so its
    row can never take the generic numeric path — `.tolist()` on a 640x480
    mask is 307,200 float64s per frame at 15 Hz against ADR-11's ~17 MB
    whole-run budget. (#131 amended what the image path DOES with a mask —
    see the decode/capture/provenance tests — but the routing that keeps it
    off the numeric path is unchanged.)"""
    from aisle.harness.trace_recorder import is_image_endpoint

    # the routing PREDICATE the recorder calls, not the constant behind it:
    # asserting on the constant alone left `IMAGE_ENDPOINTS[:-1]` at the call
    # site green while masks went back down the numeric path
    assert is_image_endpoint("dora-genesis__seg_overhead")
    for endpoint in ("rgb_overhead", "rgb_wrist", "depth_overhead", "seg_overhead"):
        assert is_image_endpoint(f"dora-genesis__{endpoint}"), endpoint
    # a non-image endpoint still takes the numeric path
    assert not is_image_endpoint("dora-genesis__joint_state")


def test_decode_frame_returns_seg_mask_as_int32():
    """#131 (amending #129's decline): at L1 the mask IS the pose's
    determining input, so it decodes — into the capture set and the
    provenance hash — with its ids intact. The mp4 remains rgb-only via the
    recorder's stream-name guard."""
    mask = np.zeros((480, 640), dtype=np.int32)
    mask[100:140, 200:260] = 17
    value = pa.array(mask.ravel())
    decoded = decode_frame({"h": 480, "w": 640, "enc": "seg_i32"}, value)
    assert decoded is not None and decoded.dtype == np.int32
    assert decoded.shape == (480, 640)
    assert int((decoded == 17).sum()) == 40 * 60


def test_seg_provenance_row_proves_which_mask_produced_a_pose():
    """#131 option 2: the metadata-only row carries a sha256 + labeled-pixel
    count (~bytes, not the ~1.2 MB payload), so an L1 `target_pose` in a
    trace can be tied to the exact mask that produced it even when frame
    capture was off."""
    import hashlib
    import json

    from aisle.harness.trace_recorder import seg_provenance

    mask = np.zeros((48, 64), dtype=np.int32)
    mask[10:20, 10:20] = 5
    row = json.loads(seg_provenance(mask))
    assert row["mask_sha256"] == hashlib.sha256(mask.tobytes()).hexdigest()
    assert row["nonzero_px"] == 100
    # a different mask must hash differently — the hash is the audit anchor
    mask[0, 0] = 9
    assert json.loads(seg_provenance(mask))["mask_sha256"] != row["mask_sha256"]


def test_capture_includes_a_same_render_seg_mask_in_the_overhead_npz(tmp_path):
    """#131 option 1: when frame capture is on, the judged overhead npz
    carries the mask from the SAME render pass (TC-9 stamp rule) — the L1
    replay input, a few KB per judged instant."""
    rgb = np.random.default_rng(0).integers(0, 255, (4, 4, 3), dtype=np.uint8)
    depth = np.random.default_rng(1).random((4, 4)).astype(np.float32)
    seg = np.arange(16, dtype=np.int32).reshape(4, 4)
    latest = {
        "rgb_overhead": (5_000_000_000, rgb),
        "depth_overhead": (5_000_000_000, depth),
        "seg_overhead": (5_000_000_000, seg),
    }
    assert capture_frames(tmp_path / "frames", latest) == 5_000_000_000
    frames = load_frames(tmp_path)
    stored = frames["overhead"][5_000_000_000]
    assert set(stored) == {"rgb", "depth", "seg"}
    assert np.array_equal(stored["seg"], seg)


def test_capture_omits_a_seg_mask_from_a_different_render(tmp_path):
    """The TC-9 stamp rule applies to the capture set too: a mask from
    another tick would attest a scene the judged pair never saw — the pair
    is still written, the stale mask is not."""
    rgb = np.random.default_rng(0).integers(0, 255, (4, 4, 3), dtype=np.uint8)
    depth = np.random.default_rng(1).random((4, 4)).astype(np.float32)
    seg = np.arange(16, dtype=np.int32).reshape(4, 4)
    latest = {
        "rgb_overhead": (5_000_000_000, rgb),
        "depth_overhead": (5_000_000_000, depth),
        "seg_overhead": (4_933_000_000, seg),  # previous tick
    }
    assert capture_frames(tmp_path / "frames", latest) == 5_000_000_000
    stored = load_frames(tmp_path)["overhead"][5_000_000_000]
    assert set(stored) == {"rgb", "depth"}


@pytest.mark.parametrize("goal_offset_ms", [0, 40], ids=["first-half-phase", "second-half-phase"])
def test_masks_survive_both_boundary_phases_of_the_real_interleave(tmp_path, goal_offset_ms):
    """PR #134 review P1: drive the retention/capture order with the REAL
    contract-rate interleave — rgb+depth+seg on each 15 Hz render tick
    (bridge publish order), rgb alone on the 30 Hz tick between. The 5 s
    capture period is an exact multiple of the render period, so the
    boundary phase repeats for EVERY checkpoint of an episode: with seg
    retained after the boundary check, a second-half-phase boundary fires
    capture ON the seg event and omits the in-hand mask at every
    mid-episode capture (the first live run's 4/15 misses were one
    all-miss episode, not a scattered race). Seg retained first, both
    phases carry the mask."""
    from aisle.harness.trace_recorder import record_image_frame

    schedule = CaptureSchedule(int(5e9))
    schedule.start(goal_offset_ms * 10**6)  # goal receipt re-bases (VER-9)
    latest: dict = {}
    frames_dir = tmp_path / "frames"
    half = 33_333_333  # the 30 Hz rgb cadence; full render ticks at even k
    for k in range(160):  # ~5.3 s: two checkpoints per phase
        t = k * half
        rgb = np.full((4, 4, 3), k % 251, dtype=np.uint8)
        record_image_frame("rgb_overhead", t, rgb, schedule, latest, frames_dir)
        if k % 2 == 0:
            depth = np.full((4, 4), 0.5 + k, dtype=np.float32)
            seg = np.full((4, 4), k % 17 + 1, dtype=np.int32)
            record_image_frame("depth_overhead", t, depth, schedule, latest, frames_dir)
            record_image_frame("seg_overhead", t, seg, schedule, latest, frames_dir)
    frames = load_frames(tmp_path).get("overhead", {})
    assert len(frames) >= 1, "no captures fired"
    missing = [stamp for stamp, arrays in frames.items() if "seg" not in arrays]
    assert missing == [], f"maskless judged instants at {missing}"


def test_npz_mask_hash_matches_the_provenance_contract(tmp_path):
    """The audit CHAIN between #131's two options: sha256 over the
    npz-STORED mask must equal `seg_provenance` of the live frame — pinned
    with a non-contiguous view so a layout/dtype divergence between the
    write path and the hash path cannot silently sever the chain."""
    import hashlib
    import json

    from aisle.harness.trace_recorder import seg_provenance

    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    depth = np.zeros((4, 4), dtype=np.float32)
    seg = np.arange(32, dtype=np.int32).reshape(8, 4)[::-2]  # non-contiguous view
    prov = json.loads(seg_provenance(seg))
    latest = {
        "rgb_overhead": (7, rgb),
        "depth_overhead": (7, depth),
        "seg_overhead": (7, seg),
    }
    assert capture_frames(tmp_path / "frames", latest) == 7
    stored = load_frames(tmp_path)["overhead"][7]["seg"]
    assert hashlib.sha256(np.ascontiguousarray(stored).tobytes()).hexdigest() == prov["mask_sha256"]
    assert int(np.count_nonzero(stored)) == prov["nonzero_px"]
