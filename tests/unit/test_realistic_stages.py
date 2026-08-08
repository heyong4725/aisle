"""VER-9..VER-12 stage geometry (SPEC 040), with detections injected —
no torch, no sim (CON-12). The projection round-trip is the load-bearing
check: a wrong optical convention silently mislocates the tray ROI, and
then every identity vote is judged on the wrong pixels.
"""

import math

import numpy as np
import pytest

from aisle.verifier.calibration import build_calibration_v1
from aisle.verifier.stages import (
    backproject_overhead,
    containment_vote,
    detections_in_roi,
    dominant_surface,
    home_vote,
    identity_frame,
    project_to_pixels,
    tray_roi_pixels,
    upright_vote,
)

pytestmark = pytest.mark.unit

# the production wrist mount (SCN-5 `wrist_rotation_xyzw`, 180 deg about
# X): tests pass it explicitly because build_calibration_v1 requires it —
# a default would let a nominal block describe a different camera than the
# published one and stage 0 would refuse every episode (#110 review)
WRIST_MOUNT = np.diag([1.0, -1.0, -1.0])

CALIB = build_calibration_v1(
    [0.55, 0.0, 1.20],
    [0.55, 0.0, 0.20],
    (640, 480),
    55.0,
    [0.0, 0.0, 0.05],
    (320, 240),
    70.0,
    WRIST_MOUNT,
)
TRAY_MIN = (0.40, -0.20, 0.04)
TRAY_MAX = (0.70, 0.10, 0.06)
RANK = 0.02  # thresholds.toml surface_min_rank_ratio


def test_projection_and_backprojection_round_trip():
    """VER-8/VER-9/VER-10: a base-frame point projected to pixels and
    back through the depth model must return to itself. This is what
    proves the OpenCV conventions are used consistently on BOTH paths."""
    points = np.array([[0.55, 0.0, 0.05], [0.62, -0.08, 0.05], [0.45, 0.06, 0.05]])
    uv = project_to_pixels(points, CALIB)
    # a synthetic overhead depth image holding each point's camera-frame z
    depth = np.zeros((480, 640))
    cam_z = 1.20 - points[:, 2]  # straight-down camera: optical z = height drop
    for (u, v), z in zip(uv, cam_z, strict=True):
        depth[int(round(v)), int(round(u))] = z
    recovered = backproject_overhead(depth, CALIB, np.round(uv).astype(int))
    assert np.allclose(recovered, points, atol=2e-3), f"round trip drifted: {recovered - points}"


def test_tray_roi_is_a_sane_pixel_box_around_the_tray():
    u0, v0, u1, v1 = tray_roi_pixels(TRAY_MIN, TRAY_MAX, CALIB)
    assert 0 <= u0 < u1 <= 640 and 0 <= v0 < v1 <= 480
    centre = project_to_pixels(np.array([[0.55, -0.05, 0.06]]), CALIB)[0]
    assert u0 <= centre[0] <= u1 and v0 <= centre[1] <= v1  # tray centre inside its ROI
    outside = project_to_pixels(np.array([[0.55, 0.35, 0.06]]), CALIB)[0]
    assert not (u0 <= outside[0] <= u1 and v0 <= outside[1] <= v1)


def test_detections_filtered_by_roi_and_threshold():
    roi = (100.0, 100.0, 200.0, 200.0)
    dets = [
        {"label": "omeprazole", "score": 0.91, "box": [140, 140, 160, 160]},  # in, strong
        {"label": "ibuprofen", "score": 0.12, "box": [150, 150, 170, 170]},  # in, weak
        {"label": "cetirizine", "score": 0.95, "box": [400, 400, 420, 420]},  # out
        {"label": "omeprazole", "score": 0.80, "box": [110, 110, 130, 130]},  # in, weaker dup
    ]
    scores = detections_in_roi(dets, roi, min_score=0.3)
    assert scores == {"omeprazole": 0.91}  # max per class; weak + outside dropped


def test_identity_frame_flags_target_and_non_target():
    """VER-9's per-frame primitives — the inputs to the episode latch."""
    roi = (100.0, 100.0, 200.0, 200.0)
    target_only = identity_frame(
        [{"label": "omeprazole", "score": 0.9, "box": [140, 140, 160, 160]}],
        "omeprazole",
        roi,
        0.3,
        1_000_000_000,
    )
    assert target_only["target_in_tray"] and not target_only["non_target_in_tray"]

    both = identity_frame(
        [
            {"label": "omeprazole", "score": 0.9, "box": [140, 140, 160, 160]},
            {"label": "ibuprofen", "score": 0.7, "box": [150, 150, 170, 170]},
        ],
        "omeprazole",
        roi,
        0.3,
        2_000_000_000,
    )
    assert both["target_in_tray"] and both["non_target_in_tray"]  # feeds the latch

    empty = identity_frame([], "omeprazole", roi, 0.3, 3_000_000_000)
    assert not empty["target_in_tray"] and not empty["non_target_in_tray"]


def test_containment_reconstructs_the_bottom_from_the_observed_top():
    """VER-10 (PR #103 rounds 1-2): containment is the tray VOLUME plus
    the resting predicate — and the reconstruction matters. A top-down
    depth camera sees the box LID, so the bottom is the observed top
    plane MINUS the class height; equating min visible z with the bottom
    reported a correctly-resting box as airborne by its own height."""
    height = 0.085  # omeprazole
    floor = TRAY_MIN[2]
    # a resting box: its TOP surface sits one height above the floor
    resting = np.array(
        [[0.55 + 0.01 * i, -0.05 + 0.01 * j, floor + height] for i in range(4) for j in range(4)]
    )
    ok = containment_vote(resting, TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK)
    assert ok.status == "pass", ok.detail
    assert ok.measurement["rest_gap_m"] == pytest.approx(0.0, abs=1e-6)
    assert ok.measurement["top_surface_z_m"] == pytest.approx(floor + height)

    airborne = resting + np.array([0.0, 0.0, 0.5])  # same footprint, lifted
    vote = containment_vote(airborne, TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK)
    assert vote.status == "fail" and vote.measurement["rest_gap_m"] == pytest.approx(0.5, abs=1e-6)

    overhanging = resting + np.array([0.16, 0.0, 0.0])  # slide the lid past the rim
    vote = containment_vote(overhanging, TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK)
    assert vote.status == "fail" and vote.measurement["margin_m"] < 0

    assert (
        containment_vote(np.empty((0, 3)), TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK).status
        == "error"
    )


def test_wrist_projection_requires_the_ee_pose():
    """VER-8/VER-9 (PR #103 review): cam_to_ee is a MOUNT. Projecting
    wrist pixels without the EE->base pose at the frame's stamp put the
    ROI in the wrong frame; it now refuses rather than guessing, and
    composing with the EE pose moves the ROI as the arm moves."""
    points = np.array([[0.55, 0.0, 0.05]])
    with pytest.raises(ValueError, match="cam_to_ee is a MOUNT"):
        project_to_pixels(points, CALIB, "wrist")

    ee_a = ([0.55, 0.0, 0.35], [1.0, 0.0, 0.0, 0.0])  # looking down at the tray
    ee_b = ([0.75, 0.0, 0.35], [1.0, 0.0, 0.0, 0.0])  # same pose, shifted in x
    uv_a = project_to_pixels(points, CALIB, "wrist", ee_to_base=ee_a)
    uv_b = project_to_pixels(points, CALIB, "wrist", ee_to_base=ee_b)
    assert not np.allclose(uv_a, uv_b), "wrist ROI ignored the EE pose"


def test_upright_is_the_top_surface_normal_not_a_principal_axis():
    """VER-11 (PR #103 round 2): the visible patch is the box's top
    PLANE, so its principal axis carries no tilt information — the plane
    NORMAL does. Reuses VER-2's 30-degree band."""
    grid = np.array([[0.55 + 0.01 * i, -0.05 + 0.01 * j, 0.12] for i in range(5) for j in range(5)])
    flat = upright_vote(grid, 30.0, RANK)
    assert flat.status == "pass" and flat.measurement["tilt_deg"] == pytest.approx(0.0, abs=1e-6)

    # tilt the same patch 50 degrees about y: z varies with x
    angle = math.radians(50)
    tilted = grid.copy()
    tilted[:, 2] = 0.12 + (tilted[:, 0] - 0.55) * math.tan(angle)
    vote = upright_vote(tilted, 30.0, RANK)
    assert vote.status == "fail"
    assert vote.measurement["tilt_deg"] == pytest.approx(50, abs=1)

    assert upright_vote(np.array([[0.5, 0.0, 0.05]]), 30.0, RANK).status == "error"


def test_home_vote_matches_the_oracle_tolerance_and_refuses_truncation():
    """VER-12 (PR #103 round 2): a TRUNCATED joint state is
    unable-to-judge, not a pass — comparing the overlapping prefix let a
    one-element array score zero residual against the 7-joint home."""
    home = np.array([0.0, -0.4, 0.0, -2.2, 0.0, 1.8, 0.785])
    assert home_vote(home + 0.05, home, 0.15).status == "pass"

    far = home.copy()
    far[3] += 0.4
    vote = home_vote(far, home, 0.15)
    assert vote.status == "fail" and vote.measurement["max_joint_residual_rad"] == pytest.approx(
        0.4
    )

    truncated = home_vote(np.array([0.0]), home, 0.15)
    assert truncated.status == "error", "a truncated joint state passed VER-12"
    assert "1 values" in truncated.detail

    assert home_vote(np.array([]), home, 0.15).status == "error"
    nan_state = home.copy()
    nan_state[2] = float("nan")
    assert home_vote(nan_state, home, 0.15).status == "error"


def test_tilted_box_body_outside_the_tray_fails_containment():
    """VER-10 (PR #103 review round 3): a 30-degree lid can sit inside
    the tray footprint while the BODY hangs outside it — the bottom face
    is displaced by height*sin(tilt) along the lid's own normal (4.25 cm
    for a standard med). Checking the lid footprint alone called that a
    success; containment now reconstructs both faces."""
    height = 0.085
    tilt = math.radians(30)
    xs = np.linspace(0.405, 0.445, 8)
    ys = np.linspace(-0.10, -0.06, 8)
    lid = np.array([[x, y, 0.125 - (x - 0.425) * math.tan(tilt)] for x in xs for y in ys])
    vote = containment_vote(lid, TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK)
    assert vote.status == "fail", f"tilted body outside the tray passed: {vote}"
    assert vote.measurement["margin_m"] < 0

    # the same lid tilt with the body INSIDE the tray still passes
    inside = lid + np.array([0.10, 0.0, 0.0])
    ok = containment_vote(inside, TRAY_MIN, TRAY_MAX, 0.005, 0.05, height, RANK)
    assert ok.measurement["margin_m"] > 0


def test_rank_deficient_mask_cannot_produce_a_verdict():
    """VER-10/VER-11 (PR #103 review round 3): three collinear points do
    not define a plane, so the 'normal' — and any tilt derived from it —
    is arbitrary. Both geometry stages must return error, not pass."""
    direction = np.array([0.125730, -0.132105, 0.032021])
    collinear = np.array([[0.55, -0.05, 0.125] + direction * t for t in (-1.0, 0.0, 1.0)])

    assert upright_vote(collinear, 30.0, RANK).status == "error"
    assert (
        containment_vote(collinear, TRAY_MIN, TRAY_MAX, 0.005, 0.01, 0.085, RANK).status == "error"
    )

    nan_points = np.array([[0.55, -0.05, float("nan")]] * 5)
    assert upright_vote(nan_points, 30.0, RANK).status == "error"


def test_dominant_surface_band_is_a_parameter():
    """VER-10/11 (PR #103 review round 3): the depth band decides which
    points reach both geometry stages, so it is passed in from
    thresholds.toml rather than hard-coded."""
    lid = np.array([[0.55, -0.05, 0.125]] * 10)
    floor_bleed = np.array([[0.55, -0.05, 0.041]] * 3)
    cloud = np.vstack([lid, floor_bleed])
    assert len(dominant_surface(cloud, 0.01)) == 10  # bleed removed
    assert len(dominant_surface(cloud, 0.20)) == 13  # wide band keeps everything


def test_resting_height_is_robust_to_silhouette_outliers():
    """VER-10: mask-edge pixels that survive the depth band must not make
    a resting box read as sunken. The footprint still uses the FULL
    extent (a displaced body must fail), but the resting height uses a
    robust central estimate of the reconstructed bottom face."""
    height = 0.085
    floor = TRAY_MIN[2]
    lid = np.array(
        [[0.55 + 0.01 * i, -0.05 + 0.01 * j, floor + height] for i in range(6) for j in range(6)]
    )
    lid[0, 2] -= 0.008  # one silhouette-edge pixel, 8 mm low
    vote = containment_vote(lid, TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK)
    assert vote.status == "pass", vote
    assert abs(vote.measurement["rest_gap_m"]) < 0.002

    # a genuinely airborne box still fails, outlier or not
    airborne = lid + np.array([0.0, 0.0, 0.3])
    assert (
        containment_vote(airborne, TRAY_MIN, TRAY_MAX, 0.005, 0.01, height, RANK).status == "fail"
    )


def test_crop_to_roi_returns_the_window_and_its_offset():
    """VER-9 detects on the tray window, not the whole frame. The offset
    is what lets `detections_in_roi` keep judging in ONE coordinate
    system (full-frame pixels)."""
    from aisle.verifier.stages import crop_to_roi

    image = np.arange(480 * 640 * 3, dtype=np.uint8).reshape(480, 640, 3)
    window, offset = crop_to_roi(image, (100.4, 200.6, 160.2, 280.9), pad_px=10)

    assert offset == (90.0, 190.0)
    assert window.shape == (291 - 190, 171 - 90, 3)
    assert np.array_equal(window, image[190:291, 90:171])


def test_crop_to_roi_clamps_to_the_image_and_refuses_no_overlap():
    """A tray partly outside the frame still yields the visible part; a
    tray entirely outside yields None, so the caller can record "not
    judgeable" rather than detect on an empty array."""
    from aisle.verifier.stages import crop_to_roi

    image = np.zeros((60, 80, 3), dtype=np.uint8)
    window, offset = crop_to_roi(image, (-30.0, -30.0, 20.0, 20.0), pad_px=5)
    assert offset == (0.0, 0.0)
    assert window.shape == (25, 25, 3)

    assert crop_to_roi(image, (200.0, 200.0, 260.0, 260.0), pad_px=5) is None


def test_shift_detections_maps_crop_boxes_back_to_full_frame():
    """The ROI test compares box CENTRES against full-frame ROI bounds —
    an unshifted crop box would be judged against the wrong region."""
    from aisle.verifier.stages import detections_in_roi, shift_detections

    cropped = [{"label": "ibuprofen", "score": 0.15, "box": [10.0, 12.0, 30.0, 34.0]}]
    shifted = shift_detections(cropped, (170.0, 369.0))

    assert shifted[0]["box"] == [180.0, 381.0, 200.0, 403.0]
    assert cropped[0]["box"] == [10.0, 12.0, 30.0, 34.0]  # inputs untouched
    assert detections_in_roi(shifted, (182.0, 381.0, 274.0, 509.0), 0.05) == {"ibuprofen": 0.15}


def test_med_box_area_limit_projects_the_largest_footprint():
    """VER-9: the gate is the largest med footprint PROJECTED at the tray
    rim, not a fraction of anything — and it is the LARGEST med, so a
    legitimate big box is never gated out."""
    from aisle.verifier.stages import med_box_area_limit, project_to_pixels

    sizes = {"small": [0.03, 0.02, 0.05], "metformin": [0.07, 0.035, 0.095]}
    limit = med_box_area_limit(TRAY_MIN, TRAY_MAX, sizes, CALIB, 3.0)

    cx, cy = (TRAY_MIN[0] + TRAY_MAX[0]) / 2, (TRAY_MIN[1] + TRAY_MAX[1]) / 2
    corners = np.array(
        [[cx + sx * 0.035, cy + sy * 0.0175, TRAY_MAX[2]] for sx in (-1, 1) for sy in (-1, 1)]
    )
    uv = project_to_pixels(corners, CALIB)
    expected = 3.0 * np.ptp(uv[:, 0]) * np.ptp(uv[:, 1])
    assert limit == pytest.approx(expected)
    assert med_box_area_limit(
        TRAY_MIN, TRAY_MAX, {"metformin": sizes["metformin"]}, CALIB, 3.0
    ) == (pytest.approx(limit))


def test_size_gate_rejects_a_tray_sized_detection_and_keeps_a_med():
    """The measured failure mode (PR #104 review, finding 1): the model
    labels the WHOLE TRAY with a med class. On identity-calib-I2 the
    genuine ibuprofen box covered 0.077 of the tray ROI at score 0.1182
    while a 'cetirizine' artifact covered 0.774 at 0.0474 — with the gate
    only the real box survives, so the wrong-object latch cannot fire on
    a correct delivery."""
    from aisle.verifier.stages import detections_in_roi, med_box_area_limit

    roi = tray_roi_pixels(TRAY_MIN, TRAY_MAX, CALIB)
    limit = med_box_area_limit(TRAY_MIN, TRAY_MAX, {"m": [0.07, 0.035, 0.095]}, CALIB, 3.0)
    cu, cv = (roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2
    med_box = [cu - 11.0, cv - 20.0, cu + 11.0, cv + 20.0]  # ~23x40 px, a real box
    tray_box = [roi[0] + 1, roi[1] + 1, roi[2] - 1, roi[3] - 1]  # the tray itself
    detections = [
        {"label": "ibuprofen", "score": 0.1182, "box": med_box},
        {"label": "cetirizine", "score": 0.0474, "box": tray_box},
    ]

    # judged at 0.04, BELOW the artifact's score, so the gate alone decides
    assert detections_in_roi(detections, roi, 0.04, limit) == {"ibuprofen": 0.1182}
    # ungated, the artifact is indistinguishable from a real wrong object
    assert set(detections_in_roi(detections, roi, 0.04)) == {"ibuprofen", "cetirizine"}


def test_size_gate_keeps_a_genuine_second_med_so_the_latch_still_fires():
    """The gate bounds size UPWARD only — VER-3's safety asymmetry must
    survive it. A real non-target box in the tray is med-sized, so it
    still sets `non_target_in_tray`."""
    from aisle.verifier.stages import identity_frame, med_box_area_limit

    roi = tray_roi_pixels(TRAY_MIN, TRAY_MAX, CALIB)
    limit = med_box_area_limit(TRAY_MIN, TRAY_MAX, {"m": [0.07, 0.035, 0.095]}, CALIB, 3.0)
    cu, cv = (roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2
    frame = identity_frame(
        [
            {"label": "omeprazole", "score": 0.20, "box": [cu - 20, cv - 12, cu - 2, cv + 12]},
            {"label": "metformin", "score": 0.14, "box": [cu + 2, cv - 12, cu + 20, cv + 12]},
        ],
        "omeprazole",
        roi,
        0.05,
        42,
        limit,
    )

    assert frame["target_in_tray"] and frame["non_target_in_tray"]


def test_tray_roi_refuses_when_the_tray_is_behind_the_camera():
    """A point behind the image plane has NO pixel. Projecting it anyway
    produced coordinates around 1e10, which `crop_to_roi` then clamped to
    the whole frame — so a camera pointing AWAY from the tray reported the
    tray as visible and identity ran on the entire image (found while
    measuring #107). Not judgeable is None, which is not an empty tray.

    Since #110 the wrist mount aims the camera along the EE link's +Z
    approach axis, so `cam_to_ee` in the OpenCV convention is identity: an
    EE with identity orientation looks along world +Z."""
    from aisle.verifier.calibration import build_calibration_v1
    from aisle.verifier.stages import tray_roi_pixels

    calibration = build_calibration_v1(
        [0.55, 0.0, 1.20],
        [0.55, 0.0, 0.20],
        (640, 480),
        55.0,
        [0.0, 0.0, 0.05],
        (320, 240),
        70.0,
        WRIST_MOUNT,
    )
    # looking UP from above the tray puts it behind the image plane
    above = ((0.55, -0.05, 0.90), (0.0, 0.0, 0.0, 1.0))
    assert tray_roi_pixels(TRAY_MIN, TRAY_MAX, calibration, "wrist", above) is None

    # looking UP from below it, the tray is in front and projects finitely
    below = ((0.55, -0.05, -0.40), (0.0, 0.0, 0.0, 1.0))
    roi = tray_roi_pixels(TRAY_MIN, TRAY_MAX, calibration, "wrist", below)
    assert roi is not None and all(abs(v) < 1e5 for v in roi)
