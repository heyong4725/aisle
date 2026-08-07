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
    home_vote,
    identity_frame,
    project_to_pixels,
    tray_roi_pixels,
    upright_vote,
)

pytestmark = pytest.mark.unit

CALIB = build_calibration_v1(
    [0.55, 0.0, 1.20], [0.55, 0.0, 0.20], (640, 480), 55.0, [0.0, 0.0, 0.05], (320, 240), 70.0
)
TRAY_MIN = (0.40, -0.20, 0.04)
TRAY_MAX = (0.70, 0.10, 0.06)


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


def test_containment_margin_sign_and_error():
    inside = containment_vote(
        np.array([[0.55, -0.05, 0.05], [0.60, 0.0, 0.05]]), TRAY_MIN, TRAY_MAX, 0.005
    )
    assert inside.status == "pass" and inside.measurement["margin_m"] > 0

    overhanging = containment_vote(
        np.array([[0.55, -0.05, 0.05], [0.75, 0.0, 0.05]]), TRAY_MIN, TRAY_MAX, 0.005
    )
    assert overhanging.status == "fail" and overhanging.measurement["margin_m"] < 0

    assert containment_vote(np.empty((0, 3)), TRAY_MIN, TRAY_MAX, 0.005).status == "error"


def test_upright_vote_uses_the_oracle_threshold_band():
    """VER-11 reuses VER-2's 30 degrees — one threshold, one source."""
    upright_points = np.array([[0.55, 0.0, 0.05 + 0.01 * i] for i in range(10)])
    assert upright_vote(upright_points, 30.0).status == "pass"

    angle = math.radians(50)
    tilted = np.array(
        [
            [0.55 + 0.01 * i * math.sin(angle), 0.0, 0.05 + 0.01 * i * math.cos(angle)]
            for i in range(10)
        ]
    )
    vote = upright_vote(tilted, 30.0)
    assert vote.status == "fail" and vote.measurement["tilt_deg"] == pytest.approx(50, abs=1)

    assert upright_vote(np.array([[0.5, 0.0, 0.05]]), 30.0).status == "error"


def test_home_vote_matches_the_oracle_tolerance():
    home = np.array([0.0, -0.4, 0.0, -2.2, 0.0, 1.8, 0.785])
    assert home_vote(home + 0.05, home, 0.15).status == "pass"
    far = home.copy()
    far[3] += 0.4
    vote = home_vote(far, home, 0.15)
    assert vote.status == "fail" and vote.measurement["max_joint_residual_rad"] == pytest.approx(
        0.4
    )
    assert home_vote(np.array([]), home, 0.15).status == "error"
