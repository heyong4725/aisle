"""L1 pose estimation (TC-9), pure — no Genesis, no dora.

The sim conformance test (estimate vs Genesis ground truth) is the sim-marked
companion; this pins the geometry and, more importantly, the REFUSALS. An L1
estimate that is confidently wrong is worse than no estimate, because the
grasp planner downstream cannot tell the difference.
"""

import numpy as np
import pytest

from aisle.nodes.segmented_pose import (
    MIN_MASK_PIXELS,
    PoseRefused,
    estimate_pose,
    seg_ids_for,
)

pytestmark = pytest.mark.unit

BOX_H = 0.10
TOP_Z = 0.60  # the box's top surface, in the base frame


def _flat_backproject(depth, pixels):
    """A stand-in for VER-8 back-projection: pixel (u, v) maps to
    (x, y) = (u/1000, v/1000) metres and z comes from the depth map, so the
    geometry under test is the mask handling, not the camera model."""
    pixels = np.asarray(pixels)
    z = depth[pixels[:, 1], pixels[:, 0]]
    return np.stack([pixels[:, 0] / 1000.0, pixels[:, 1] / 1000.0, z], axis=1)


def _scene(mask_slice, seg_id=17, shape=(64, 64), top_z=TOP_Z, background_z=0.40):
    seg = np.zeros(shape, dtype=np.int32)
    seg[mask_slice] = seg_id
    depth = np.full(shape, background_z, dtype=np.float32)
    depth[mask_slice] = top_z
    return seg, depth


def test_centre_is_half_a_box_below_the_top_surface():
    """The z estimate is exact by construction: the mask's top surface plus a
    known half-height IS the centre. That is why the measured z error against
    Genesis ground truth was ~0 while XY carried a couple of millimetres."""
    seg, depth = _scene(np.s_[20:40, 10:40])
    est = estimate_pose(seg, depth, [17], BOX_H, _flat_backproject)

    assert est["top_surface_z_m"] == pytest.approx(TOP_Z)
    assert est["pos"][2] == pytest.approx(TOP_Z - BOX_H / 2)
    # centroid of columns 10..39 and rows 20..39, in the stand-in's metres
    assert est["pos"][0] == pytest.approx(0.0245, abs=1e-4)
    assert est["pos"][1] == pytest.approx(0.0295, abs=1e-4)


def test_mask_size_travels_with_the_estimate():
    """TC-9: the supporting evidence is part of the estimate, so a consumer
    can weigh a marginal mask instead of trusting a bare pose."""
    seg, depth = _scene(np.s_[20:40, 10:40])
    assert estimate_pose(seg, depth, [17], BOX_H, _flat_backproject)["mask_pixels"] == 20 * 30


def test_a_mostly_occluded_box_is_REFUSED_not_guessed():
    """The measured failure mode: over 20 objects the mean XY error was 2.2 mm,
    but the two worst (19.5 mm, 9.0 mm) were the two smallest masks — occlusion
    biases the centroid toward the visible fragment. Refusing keeps a phantom
    pose out of the grasp planner; the episode then closes honestly on the
    verifier's timeout."""
    seg, depth = _scene(np.s_[20:25, 10:15])  # 25 px
    with pytest.raises(PoseRefused, match="under the .* px floor"):
        estimate_pose(seg, depth, [17], BOX_H, _flat_backproject)


def test_an_absent_target_is_refused_rather_than_reported_at_the_origin():
    """No mask at all must not average to (0, 0) — an empty selection is the
    same class of bug as a wrong seg id, and both look like a pose."""
    seg, depth = _scene(np.s_[20:40, 10:40], seg_id=17)
    with pytest.raises(PoseRefused):
        estimate_pose(seg, depth, [99], BOX_H, _flat_backproject)


def test_non_finite_depth_is_refused():
    """`depth_overhead` uses 0 for invalid (TC-9's table); a NaN that reaches
    back-projection would produce a NaN pose that reads as a number."""
    seg, depth = _scene(np.s_[20:40, 10:40])
    depth[25, 20] = np.nan
    with pytest.raises(PoseRefused, match="non-finite"):
        estimate_pose(seg, depth, [17], BOX_H, _flat_backproject)


def test_top_surface_beats_a_whole_mask_average_when_edges_hit_the_shelf():
    """Edge pixels straddle the silhouette and pick up whatever is behind, so
    averaging the WHOLE mask pulls z down. Taking the top depth band is the
    same correction VER-10's dominant_surface makes for the verifier."""
    seg, depth = _scene(np.s_[20:40, 10:40])
    depth[20, 10:40] = 0.42  # one edge row resting on the shelf behind

    est = estimate_pose(seg, depth, [17], BOX_H, _flat_backproject)
    assert est["top_surface_z_m"] == pytest.approx(TOP_Z)

    whole_mask_mean = float(depth[seg == 17].mean())
    assert whole_mask_mean < TOP_Z - 0.005, "fixture does not exercise the correction"


def test_seg_ids_come_from_the_published_map_never_from_entity_indices():
    """TC-9: masking on entity index silently selects other geometry — the
    spike that did it returned robot links with pixel counts identical across
    scenes whose layouts differed. An unpublished target is refused."""
    assert seg_ids_for({"omeprazole": [19]}, "omeprazole") == [19]
    with pytest.raises(PoseRefused, match="no segmentation ids published"):
        seg_ids_for({"omeprazole": [19]}, "metformin")


def test_the_pixel_floor_admits_every_unoccluded_med_measured():
    """Unoccluded meds rendered 433-970 px over seeds 3-6, so the floor must
    sit below that band and above a half-hidden box."""
    assert MIN_MASK_PIXELS < 433
    assert MIN_MASK_PIXELS > 100


def _sized_scene(width_px, height_px, top_z=TOP_Z):
    """A mask whose world extent is width_px/1000 x height_px/1000 metres under
    the stand-in back-projection, so a footprint can be dialled in directly.
    The canvas is sized to the request — a fixed 64x64 silently CLIPPED an
    85 px mask to 44 px, which made the guard look broken when the fixture
    was."""
    shape = (40 + height_px, 30 + width_px)
    return _scene(np.s_[20 : 20 + height_px, 10 : 10 + width_px], shape=shape, top_z=top_z)


def test_a_tipped_box_is_refused_because_half_the_height_is_the_wrong_offset():
    """MEASURED defect, not hypothetical: commanding a 30-45 degree tilt settles
    omeprazole (0.05 x 0.045 x 0.085) on its side, and the z estimate came back
    20.0 mm low — exactly half the difference between the height and the
    dimension now vertical. A planner aiming 2 cm low collides. From one
    top-down view the estimator cannot see WHICH face it sees, but it can
    measure the face: upright shows 0.050x0.045, tipped shows 0.050x0.085."""
    seg, depth = _sized_scene(50, 85)  # the tipped face
    with pytest.raises(PoseRefused, match="not upright"):
        estimate_pose(seg, depth, [17], BOX_H, _flat_backproject, footprint_m=(0.050, 0.045))


def test_an_upright_box_passes_the_footprint_check():
    """The same guard must not reject the case it exists to protect."""
    seg, depth = _sized_scene(50, 45)
    est = estimate_pose(seg, depth, [17], BOX_H, _flat_backproject, footprint_m=(0.050, 0.045))
    assert est["pos"][2] == pytest.approx(TOP_Z - BOX_H / 2)


def test_the_footprint_check_is_opt_in_so_callers_without_dimensions_still_work():
    """`footprint_m=None` keeps the pre-guard behaviour: the neighbour estimator
    and any caller lacking meds.toml dimensions is not forced to invent them."""
    seg, depth = _sized_scene(50, 85)
    assert estimate_pose(seg, depth, [17], BOX_H, _flat_backproject)["mask_pixels"] == 50 * 85


def _session():
    from aisle.nodes.segmented_pose import L1Session

    return L1Session(
        meds={"ibuprofen": {"size": [0.05, 0.045, BOX_H]}},
        backprojector=lambda calibration: _flat_backproject,
    )


def _armed_session():
    s = _session()
    s.on_bridge_info({"calibration": {"any": "thing"}, "segmentation_ids": {"ibuprofen": [17]}})
    s.on_target_request({"target_med": "ibuprofen"})
    return s


def _good_frame(s, sim_time_ns=100):
    seg, depth = _sized_scene(50, 45)
    return s.on_depth(sim_time_ns, depth) or s.on_seg(sim_time_ns, seg)


def test_session_reset_clears_the_active_target():
    """TC-9 via T08's oracle-pose lesson: after reset_done a STALE target must
    not keep emitting target_pose. ik-trajectory accepts a plan whenever it is
    idle — post-reset it is exactly that — so one stale seg frame of the new
    scene would seed a plan for the PREVIOUS episode's med before the new
    target_request lands (wrong_object, the 10x penalty)."""
    s = _armed_session()
    s.on_reset_done()
    assert _good_frame(s) is None


def test_session_publishes_once_per_request():
    """T08 parity with oracle-pose: ONE target_pose per target_request, so a
    completed plan can never be re-triggered by the still-flowing seg stream
    (the pipeline would replan and pick the placed box back out of the tray).
    A new request re-arms it."""
    s = _armed_session()
    assert _good_frame(s, sim_time_ns=100)["target_med"] == "ibuprofen"
    assert _good_frame(s, sim_time_ns=200) is None
    s.on_target_request({"target_med": "ibuprofen"})
    assert _good_frame(s, sim_time_ns=300) is not None


def test_session_refusal_keeps_the_request_pending():
    """TC-9: a refused estimate must not consume the request — a transient
    occlusion retries on the next frame, while a persistent one publishes
    nothing and the episode closes honestly on the verifier's timeout."""
    s = _armed_session()
    tiny_seg, tiny_depth = _scene(np.s_[20:25, 10:15])  # 25 px: under the floor
    s.on_depth(100, tiny_depth)
    with pytest.raises(PoseRefused):
        s.on_seg(100, tiny_seg)
    assert _good_frame(s, sim_time_ns=200) is not None


def test_session_never_pairs_seg_and_depth_across_ticks():
    """TC-9: seg and depth MUST come from ONE render pass and carry the SAME
    sim stamp — masking one tick's segmentation over another tick's depth
    measures a scene that never existed (the defect class that already reached
    the trace recorder and the realistic verifier)."""
    s = _armed_session()
    seg, depth = _sized_scene(50, 45)
    assert s.on_depth(100, depth) is None
    assert s.on_seg(200, seg) is None  # mismatched stamps: wait, don't guess
    assert s.on_depth(200, depth) is not None  # the seg's twin arrives


def test_session_pairs_frames_in_either_arrival_order():
    """TC-9's co-scheduled pair is a contract about stamps, not delivery
    order: the bridge happens to publish depth before seg today because of
    topic-table ordering, but the session must not starve if seg arrives
    first (review finding: seg-only buffering made every publish depend on
    that undocumented ordering)."""
    s = _armed_session()
    seg, depth = _sized_scene(50, 45)
    assert s.on_seg(100, seg) is None  # seg first: buffered, not dropped
    assert s.on_depth(100, depth) is not None


def test_session_never_pairs_unstamped_frames():
    """A frame with no sim_time_ns (decoded as a negative sentinel) must not
    pair with another unstamped frame — two -1 stamps are equal but attest
    nothing about a shared render pass."""
    s = _armed_session()
    seg, depth = _sized_scene(50, 45)
    assert s.on_depth(-1, depth) is None
    assert s.on_seg(-1, seg) is None


def test_session_refuses_an_unknown_med_once_at_request_time():
    """L0 parity with oracle-pose: an unknown target_med is refused ONCE at
    request time (the caller logs it), never accepted into a state that
    refuses per-frame at 15 Hz or KeyErrors on the meds lookup."""
    s = _session()
    s.on_bridge_info({"calibration": {}, "segmentation_ids": {"ibuprofen": [17]}})
    assert s.on_target_request({"target_med": "paracetamol"}) is False
    assert _good_frame(s) is None  # nothing armed, nothing published
    assert s.on_target_request({"target_med": "ibuprofen"}) is True


def test_session_estimate_carries_pose_and_neighbour_evidence():
    """TC-9: the publishable payload holds the pose, its supporting mask size,
    and the neighbour rows + refusal count the grasp planner needs for
    fingertip clearance — the same evidence contract as the L0 path plus the
    mask that produced it."""
    s = _armed_session()
    out = _good_frame(s)
    assert out["pos"][2] == pytest.approx(TOP_Z - BOX_H / 2)
    assert out["mask_pixels"] == 50 * 45
    assert out["target_med"] == "ibuprofen"
    assert len(out["neighbours"]) == 1 and out["neighbours_refused"] == 0


def test_session_is_silent_until_calibration_and_target_arrive():
    """Startup ordering: seg frames before bridge_info or before any request
    must produce nothing rather than raise — the node subscribes to a live
    15 Hz stream and boots in whatever order dora delivers."""
    s = _session()
    seg, depth = _sized_scene(50, 45)
    s.on_depth(100, depth)
    assert s.on_seg(100, seg) is None  # no calibration, no target yet
    s.on_bridge_info({"calibration": {}, "segmentation_ids": {"ibuprofen": [17]}})
    assert s.on_seg(100, seg) is None  # calibrated, but still no request


def test_neighbour_estimation_keeps_slots_and_COUNTS_what_it_cannot_see():
    """The grasp planner uses neighbour (x, y) for fingertip clearance, so at L1
    those must be estimated too or it silently loses the check. The payload is
    POSITIONAL — the consumer zips it against MED_NAMES strict=True — so a
    neighbour whose mask is too small keeps its slot as None and is COUNTED:
    a SHORTER list crashes the planner mid-run (found in review), and fewer
    constraints means a permissive plan, not a safe one, so the count travels
    in the metadata."""
    from aisle.nodes.segmented_pose import estimate_neighbours

    seg = np.zeros((64, 64), dtype=np.int32)
    depth = np.full((64, 64), 0.40, dtype=np.float32)
    seg[10:35, 5:35] = 17  # visible neighbour
    depth[10:35, 5:35] = TOP_Z
    seg[40:44, 40:44] = 18  # 16 px: under the floor
    depth[40:44, 40:44] = TOP_Z

    rows, refused = estimate_neighbours(
        seg,
        depth,
        {"visible": [17], "tiny": [18]},
        {"visible": {"size": [0.03, 0.025, BOX_H]}, "tiny": {"size": [0.03, 0.025, BOX_H]}},
        _flat_backproject,
    )
    assert len(rows) == 2 and refused == 1
    assert rows[0] is not None and rows[1] is None
