"""Realistic-verifier stage implementations (SPEC 040 VER-9..VER-12).

Each stage turns pixels (plus the VER-8 calibration) into one
`StageVote` for `realistic.py` to fuse. The geometry here is PURE and
unit-tested; the two model-bearing entry points (`identity_frame`,
`upright_vote`) take an injected callable so the judge is testable with
recorded detections and the real models only appear on the golden-frame
path.

Thresholds come from `verifier/thresholds.toml` — the SAME file and the
SAME values the oracle uses for uprightness (VER-11 reuses VER-2's 30
degrees) and home (VER-12 reuses VER-2's tolerance). One threshold, one
source; a realistic/oracle disagreement must never be a threshold
mismatch.
"""

from __future__ import annotations

import math

import numpy as np

from aisle.verifier.calibration import rotation_from_quat_xyzw
from aisle.verifier.realistic import StageVote


def backproject_overhead(
    depth: np.ndarray, calibration: dict, pixels: np.ndarray | None = None
) -> np.ndarray:
    """Overhead depth pixels -> BASE-frame 3D points (VER-10).

    v1/OpenCV conventions (VER-8): pixel centers, +Z along the optical
    axis, depth in METERS. `pixels` is an (N, 2) array of (u, v); with
    None every pixel is back-projected. Returns (N, 3) base-frame points.
    """
    cam = calibration["overhead"]
    k = cam["intrinsics"]
    scale = float(cam["depth_scale_m"])
    depth = np.asarray(depth, dtype=np.float64)
    if pixels is None:
        vs, us = np.mgrid[0 : depth.shape[0], 0 : depth.shape[1]]
        pixels = np.stack([us.ravel(), vs.ravel()], axis=1)
    pixels = np.asarray(pixels, dtype=np.float64).reshape(-1, 2)
    z = depth[pixels[:, 1].astype(int), pixels[:, 0].astype(int)] * scale
    x = (pixels[:, 0] - k["cx"]) / k["fx"] * z
    y = (pixels[:, 1] - k["cy"]) / k["fy"] * z
    cam_points = np.stack([x, y, z], axis=1)
    rotation = rotation_from_quat_xyzw(cam["cam_to_base"]["quat_xyzw"])
    return cam_points @ rotation.T + np.asarray(cam["cam_to_base"]["pos"], dtype=np.float64)


def project_to_pixels(
    points_base: np.ndarray, calibration: dict, camera: str = "overhead"
) -> np.ndarray:
    """BASE-frame points -> (u, v) pixels for the tray ROI (VER-9)."""
    cam = calibration[camera]
    k = cam["intrinsics"]
    pose = cam["cam_to_base"] if camera == "overhead" else cam["cam_to_ee"]
    rotation = rotation_from_quat_xyzw(pose["quat_xyzw"])
    rel = np.asarray(points_base, dtype=np.float64).reshape(-1, 3) - np.asarray(
        pose["pos"], dtype=np.float64
    )
    cam_points = rel @ rotation  # world -> camera (R^T applied on the right)
    z = np.clip(cam_points[:, 2], 1e-9, None)
    u = cam_points[:, 0] / z * k["fx"] + k["cx"]
    v = cam_points[:, 1] / z * k["fy"] + k["cy"]
    return np.stack([u, v], axis=1)


def tray_roi_pixels(tray_min, tray_max, calibration: dict, camera: str = "overhead") -> tuple:
    """Axis-aligned pixel bounds of the tray footprint (VER-9's "inside
    the tray region"). Projects the tray's top-rim corners and takes
    their bounding box."""
    x0, y0, z0 = tray_min
    x1, y1, z1 = tray_max
    corners = np.array([[x, y, z1] for x in (x0, x1) for y in (y0, y1)], dtype=np.float64)
    uv = project_to_pixels(corners, calibration, camera)
    return (
        float(uv[:, 0].min()),
        float(uv[:, 1].min()),
        float(uv[:, 0].max()),
        float(uv[:, 1].max()),
    )


def detections_in_roi(detections: list[dict], roi: tuple, min_score: float) -> dict[str, float]:
    """Per-class max score among detections whose box CENTER falls inside
    the tray ROI and clears the threshold (VER-9)."""
    u0, v0, u1, v1 = roi
    scores: dict[str, float] = {}
    for det in detections:
        if float(det["score"]) < min_score:
            continue
        cu = (float(det["box"][0]) + float(det["box"][2])) / 2
        cv = (float(det["box"][1]) + float(det["box"][3])) / 2
        if u0 <= cu <= u1 and v0 <= cv <= v1:
            label = det["label"]
            scores[label] = max(scores.get(label, 0.0), float(det["score"]))
    return scores


def identity_frame(
    detections: list[dict],
    target_med: str,
    roi: tuple,
    min_score: float,
    sim_time_ns: int,
) -> dict:
    """One judged frame for the VER-9 timeline (the VER-14 frame shape).
    `detections` are the model's boxes for THIS camera+frame."""
    scores = detections_in_roi(detections, roi, min_score)
    return {
        "sim_time_ns": int(sim_time_ns),
        "per_class_scores": scores,
        "target_med": target_med,
        "target_in_tray": target_med in scores,
        "non_target_in_tray": any(label != target_med for label in scores),
    }


def containment_vote(
    target_points_base: np.ndarray, tray_min, tray_max, margin_m: float
) -> StageVote:
    """VER-10: the target's back-projected points must sit inside the
    tray footprint. The measurement is the SIGNED margin (m): positive
    means inside with room, negative is the worst overhang."""
    points = np.asarray(target_points_base, dtype=np.float64).reshape(-1, 3)
    if points.size == 0:
        return StageVote("error", detail="no target points from overhead depth")
    lo = np.asarray(tray_min[:2], dtype=np.float64) - margin_m
    hi = np.asarray(tray_max[:2], dtype=np.float64) + margin_m
    inside = np.minimum(points[:, :2] - lo, hi - points[:, :2])
    margin = float(inside.min())
    status = "pass" if margin >= 0 else "fail"
    return StageVote(status, measurement={"margin_m": round(margin, 6)})


def tilt_from_mask_extent(mask_points_base: np.ndarray) -> float:
    """Uprightness from the segmented target's 3D extent (VER-11/D6):
    the angle between the mask's principal axis and world +z."""
    points = np.asarray(mask_points_base, dtype=np.float64).reshape(-1, 3)
    centered = points - points.mean(axis=0)
    # principal axis of the (tall) box mask
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    axis = vh[0]
    cos = abs(float(np.dot(axis, [0.0, 0.0, 1.0])) / np.linalg.norm(axis))
    return math.degrees(math.acos(min(1.0, cos)))


def upright_vote(mask_points_base: np.ndarray, upright_max_deg: float) -> StageVote:
    """VER-11: tilt within the SAME 30-degree band as VER-2."""
    points = np.asarray(mask_points_base, dtype=np.float64).reshape(-1, 3)
    if len(points) < 3:
        return StageVote("error", detail="segmentation mask too small for an extent")
    tilt = tilt_from_mask_extent(points)
    status = "pass" if tilt <= upright_max_deg else "fail"
    return StageVote(status, measurement={"tilt_deg": round(tilt, 4)})


def home_vote(joint_state: np.ndarray, home_qpos: np.ndarray, tolerance_rad: float) -> StageVote:
    """VER-12: joint_state within the SAME home tolerance as VER-2 — the
    one stage that needs no pixels."""
    current = np.asarray(joint_state, dtype=np.float64).reshape(-1)
    home = np.asarray(home_qpos, dtype=np.float64).reshape(-1)
    n = min(len(current), len(home))
    if n == 0:
        return StageVote("error", detail="empty joint_state")
    residual = float(np.abs(current[:n] - home[:n]).max())
    status = "pass" if residual <= tolerance_rad else "fail"
    return StageVote(status, measurement={"max_joint_residual_rad": round(residual, 6)})
