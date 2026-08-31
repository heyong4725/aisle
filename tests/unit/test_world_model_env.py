"""world-model-env v0 pure dynamics (ADR-m3-protocol; CON-5/CON-12)."""

import numpy as np
import pytest

from aisle.nodes.world_model_env import (
    ATTACH_R_M,
    attach_candidate,
    lag_step,
    settle_pose,
)

pytestmark = pytest.mark.unit


def test_lag_step_is_velocity_bounded_and_converges():
    q = np.zeros(3, dtype=np.float32)
    t = np.array([1.0, -1.0, 0.001], dtype=np.float32)
    q1 = lag_step(q, t, 0.01, 1.0)
    assert np.allclose(q1, [0.01, -0.01, 0.001])
    for _ in range(200):
        q = lag_step(q, t, 0.01, 1.0)
    assert np.allclose(q, t, atol=1e-6)


def test_attach_requires_closing_and_proximity():
    boxes = {"a": np.array([0.5, 0.0, 0.1, 0, 0, 0, 1], dtype=np.float32)}
    tcp = np.array([0.5, 0.0, 0.12])
    assert attach_candidate(tcp, boxes, closing=False, held=None) is None
    assert attach_candidate(tcp, boxes, closing=True, held=None) == "a"
    far = np.array([0.5 + ATTACH_R_M + 0.01, 0.0, 0.12])
    assert attach_candidate(far, boxes, closing=True, held=None) is None


def test_attach_keeps_current_hold_and_picks_nearest():
    boxes = {
        "far": np.array([0.53, 0.0, 0.1, 0, 0, 0, 1], dtype=np.float32),
        "near": np.array([0.5, 0.0, 0.1, 0, 0, 0, 1], dtype=np.float32),
    }
    tcp = np.array([0.5, 0.0, 0.1])
    assert attach_candidate(tcp, boxes, closing=True, held="far") == "far"
    assert attach_candidate(tcp, boxes, closing=True, held=None) == "near"


def test_release_in_tray_settles_on_tray_floor():
    tray = {"pos": [0.35, -0.45, 0.02], "size": [0.2, 0.28, 0.04]}
    inside = np.array([0.35, -0.45, 0.2, 0.1, 0.2, 0.3, 0.9], dtype=np.float32)
    settled = settle_pose(inside, tray, half_h=0.05)
    assert settled[2] == pytest.approx(0.02 + 0.02 + 0.05)
    assert tuple(settled[3:7]) == (0.0, 0.0, 0.0, 1.0)
    outside = np.array([0.6, 0.0, 0.2, 0, 0, 0, 1], dtype=np.float32)
    assert np.allclose(settle_pose(outside, tray, half_h=0.05), outside)


def test_spearman_and_screening_from_records():
    """ADR-m3-protocol: the analyzer's metrics, tie-correct, recomputed
    from records (never hand-written)."""
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "tools"))
    import m3_ranking as m3

    assert m3.spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert m3.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert m3.spearman([1, 1, 1], [1, 1, 1]) is None  # zero variance
    rho = m3.spearman([0.0, 0.875, 0.875, 1.0], [0.0, 1.0, 0.875, 1.0])
    assert rho is not None and 0.5 < rho <= 1.0
    assert m3.screening_agreement([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0
    assert m3.screening_agreement([0, 0, 1, 1], [1, 1, 0, 0]) == 0.0


def test_rasterizer_round_trips_through_the_real_l1_estimator():
    """ADR-m3 amendment: the cartoon's seg/depth pair must be GENUINE L1
    sensor data — the frozen estimator recovers the box centre from it
    within millimetres, using the verifier's own back-projection."""
    from aisle.nodes.segmented_pose import estimate_pose
    from aisle.nodes.world_model_env import rasterize_overhead
    from aisle.scenes.pharmacy import load_physics, wrist_mount_transform
    from aisle.verifier.calibration import build_calibration_v1
    from aisle.verifier.stages import backproject_overhead

    physics = load_physics()
    cam = physics["cameras"]
    mount = wrist_mount_transform(cam, physics["embodiment"]["franka"])
    calibration = build_calibration_v1(
        overhead_pos=list(cam["overhead_pos"]),
        overhead_lookat=list(cam["overhead_lookat"]),
        overhead_resolution=(640, 480),
        overhead_fov_deg=55.0,  # SCN-5 nominal
        wrist_offset_m=mount[:3, 3].tolist(),
        wrist_mount_rotation_gl=mount[:3, :3],
        wrist_resolution=(320, 240),
        wrist_fov_deg=70.0,  # SCN-5 nominal
    )
    size = (0.060, 0.040, 0.100)
    pose = np.array([0.45, -0.10, 0.05, 0, 0, 0, 1], dtype=np.float32)
    seg, depth = rasterize_overhead(
        {"amoxicillin": pose}, {"amoxicillin": size}, {"amoxicillin": 10}, calibration
    )
    assert (seg == 10).sum() > 100, "top face must cover real pixels"
    est = estimate_pose(
        seg,
        depth,
        [10],
        size[2],
        lambda d, px: backproject_overhead(d, calibration, px),
        footprint_m=size[:2],
    )
    assert abs(est["pos"][0] - 0.45) < 0.005
    assert abs(est["pos"][1] + 0.10) < 0.005
    assert abs(est["pos"][2] - 0.05) < 0.005
