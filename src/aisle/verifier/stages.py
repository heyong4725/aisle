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


# a point at or behind the image plane has no pixel: projecting it anyway
# produces a huge finite coordinate, which downstream code cannot tell from
# a legitimate one (PR #104 review round 2 follow-up — the wrist ROI came
# back at ~1e10 px and crop_to_roi then clamped it to the WHOLE frame)
MIN_CAMERA_DEPTH_M = 1e-3


def camera_frame_points(
    points_base: np.ndarray,
    calibration: dict,
    camera: str = "overhead",
    ee_to_base: tuple | None = None,
) -> np.ndarray:
    """BASE-frame points expressed in the CAMERA frame (VER-8). Column 2
    is depth along the optical axis: positive is in front of the camera."""
    cam = calibration[camera]
    if camera == "overhead":
        pose_pos = np.asarray(cam["cam_to_base"]["pos"], dtype=np.float64)
        rotation = rotation_from_quat_xyzw(cam["cam_to_base"]["quat_xyzw"])
    else:
        if ee_to_base is None:
            raise ValueError(
                "wrist projection requires ee_to_base=(pos, quat_xyzw) at the frame's "
                "joint_state stamp — cam_to_ee is a MOUNT, not a camera->base pose (VER-8)"
            )
        mount_pos = np.asarray(cam["cam_to_ee"]["pos"], dtype=np.float64)
        mount_rot = rotation_from_quat_xyzw(cam["cam_to_ee"]["quat_xyzw"])
        ee_pos = np.asarray(ee_to_base[0], dtype=np.float64)
        ee_rot = rotation_from_quat_xyzw(ee_to_base[1])
        rotation = ee_rot @ mount_rot
        pose_pos = ee_pos + ee_rot @ mount_pos
    rel = np.asarray(points_base, dtype=np.float64).reshape(-1, 3) - pose_pos
    return rel @ rotation  # world -> camera (R^T applied on the right)


def project_to_pixels(
    points_base: np.ndarray,
    calibration: dict,
    camera: str = "overhead",
    ee_to_base: tuple | None = None,
) -> np.ndarray:
    """BASE-frame points -> (u, v) pixels (VER-9 tray ROI).

    The overhead camera has a static `cam_to_base`. The WRIST camera does
    NOT (it rides the EE): its calibration is the static `cam_to_ee`
    mount, and the camera->base pose must be composed with the EE pose at
    the matching joint_state stamp — `ee_to_base=(pos, quat_xyzw)` from
    FK (VER-8). Treating the mount as a camera->base pose put every wrist
    ROI in the wrong frame (PR #103 review), so wrist projection now
    REQUIRES the EE pose rather than silently guessing."""
    k = calibration[camera]["intrinsics"]
    cam_points = camera_frame_points(points_base, calibration, camera, ee_to_base)
    z = np.clip(cam_points[:, 2], 1e-9, None)
    u = cam_points[:, 0] / z * k["fx"] + k["cx"]
    v = cam_points[:, 1] / z * k["fy"] + k["cy"]
    return np.stack([u, v], axis=1)


def tray_roi_pixels(
    tray_min, tray_max, calibration: dict, camera: str = "overhead", ee_to_base: tuple | None = None
) -> tuple:
    """Axis-aligned pixel bounds of the tray footprint (VER-9's "inside
    the tray region"), or None when the tray is not wholly IN FRONT of
    the camera. Projects the tray's top-rim corners and takes their
    bounding box."""
    x0, y0, z0 = tray_min
    x1, y1, z1 = tray_max
    corners = np.array([[x, y, z1] for x in (x0, x1) for y in (y0, y1)], dtype=np.float64)
    depths = camera_frame_points(corners, calibration, camera, ee_to_base)[:, 2]
    if not np.all(depths > MIN_CAMERA_DEPTH_M):
        # any corner at or behind the image plane makes the bounding box
        # meaningless — the tray is not judgeable from this camera on this
        # frame, which is NOT the same as an empty tray (VER-13)
        return None
    uv = project_to_pixels(corners, calibration, camera, ee_to_base)
    return (
        float(uv[:, 0].min()),
        float(uv[:, 1].min()),
        float(uv[:, 0].max()),
        float(uv[:, 1].max()),
    )


# margin around the tray ROI before detection: a box resting against the
# rim must be WHOLLY visible to the detector, since a clipped box shifts
# its own centre and the centre is what decides containment
ROI_PAD_PX = 12


def crop_to_roi(image: np.ndarray, roi: tuple, pad_px: int = ROI_PAD_PX):
    """The padded ROI window of `image` and the (du, dv) that maps a
    detection in that window back to full-frame pixels, or None when the
    ROI does not overlap the image at all.

    VER-9 detects on this window, NOT the whole frame. Measured on the
    terminal frames of a live two-episode run (capture-smoke-I1, both
    scored success by the oracle): full-frame detection finds the target
    in the tray for 1 of 2, cropped detection for 2 of 2 — the orange
    ibuprofen box is invisible to the detector at full frame and scores
    0.150 cropped, while the red amoxicillin box goes 0.251 -> 0.460.
    Upscaling the crop adds nothing (0.183 vs 0.169 on the golden frame),
    which is what says the limit is the object\'s SHARE of the frame
    rather than its resolution: the rendered meds are ~21 px in a 640 px
    frame, and no threshold can separate signal that is not there.
    """
    h, w = image.shape[0], image.shape[1]
    u0 = max(0, int(math.floor(roi[0])) - pad_px)
    v0 = max(0, int(math.floor(roi[1])) - pad_px)
    u1 = min(w, int(math.ceil(roi[2])) + pad_px)
    v1 = min(h, int(math.ceil(roi[3])) + pad_px)
    if u1 <= u0 or v1 <= v0:
        return None
    # CONTIGUOUS: a render can arrive as a negative-stride view, and the
    # processor refuses those ("at least one stride ... is negative") —
    # the crop is small, so the copy is cheap insurance
    return np.ascontiguousarray(image[v0:v1, u0:u1]), (float(u0), float(v0))


def shift_detections(detections: list[dict], offset: tuple) -> list[dict]:
    """Re-express crop-frame detection boxes in FULL-frame pixels, so
    `detections_in_roi` keeps comparing against one coordinate system."""
    du, dv = offset
    return [
        {
            **det,
            "box": [
                float(det["box"][0]) + du,
                float(det["box"][1]) + dv,
                float(det["box"][2]) + du,
                float(det["box"][3]) + dv,
            ],
        }
        for det in detections
    ]


def med_box_area_limit(
    tray_min,
    tray_max,
    med_sizes: dict,
    calibration: dict,
    slack: float,
    camera: str = "overhead",
    ee_to_base: tuple | None = None,
) -> float:
    """The largest pixel area a single med box may cover (VER-9): the
    largest med footprint PROJECTED at the tray's rim height, times
    `slack`.

    The detector's dominant false positive is labelling the WHOLE TRAY
    with a med class: on the five-class run identity-calib-I2 every
    genuine med box covered <= 0.105 of the tray ROI while every
    non-target artifact covered >= 0.284, mostly ~0.77 — the tray itself.
    `slack` covers the side faces a tall box shows under perspective.

    The limit is projected rather than taken as a FRACTION of the tray
    ROI's pixel area, because that ROI is a projection of the whole tray
    even when most of it lies off-image: a partially visible tray then
    inflated the gate (2491.8 px^2 against a nominal 1531.5) and let the
    tray-sized artifacts back through — PR #104 review round 2. A
    projected footprint does not depend on how much of the tray the
    camera happens to see.

    This bounds size UPWARD only, so a real second med in the tray stays
    detectable and VER-9's wrong-object latch keeps its teeth."""
    if not med_sizes:
        raise ValueError("no med sizes to size the gate from")
    width, depth = max(
        ((float(size[0]), float(size[1])) for size in med_sizes.values()),
        key=lambda wd: wd[0] * wd[1],
    )
    cx = (float(tray_min[0]) + float(tray_max[0])) / 2
    cy = (float(tray_min[1]) + float(tray_max[1])) / 2
    z = float(tray_max[2])
    corners = np.array(
        [[cx + sx * width / 2, cy + sy * depth / 2, z] for sx in (-1, 1) for sy in (-1, 1)],
        dtype=np.float64,
    )
    uv = project_to_pixels(corners, calibration, camera, ee_to_base)
    area = (uv[:, 0].max() - uv[:, 0].min()) * (uv[:, 1].max() - uv[:, 1].min())
    return float(slack * area)


def detections_in_roi(
    detections: list[dict], roi: tuple, min_score: float, max_box_area: float | None = None
) -> dict[str, float]:
    """Per-class max score among detections whose box CENTER falls inside
    the tray ROI, clears the threshold, and is small enough to BE a med
    (VER-9). `max_box_area` comes from `med_box_area_limit`; None keeps
    the pre-gate behaviour for callers that judge without geometry."""
    u0, v0, u1, v1 = roi
    scores: dict[str, float] = {}
    for det in detections:
        if float(det["score"]) < min_score:
            continue
        box = [float(v) for v in det["box"]]
        if max_box_area is not None and abs((box[2] - box[0]) * (box[3] - box[1])) > max_box_area:
            continue
        cu, cv = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
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
    max_box_area: float | None = None,
) -> dict:
    """One judged frame for the VER-9 timeline (the VER-14 frame shape).
    `detections` are the model's boxes for THIS camera+frame."""
    scores = detections_in_roi(detections, roi, min_score, max_box_area)
    return {
        "sim_time_ns": int(sim_time_ns),
        "per_class_scores": scores,
        "target_med": target_med,
        "target_in_tray": target_med in scores,
        "non_target_in_tray": any(label != target_med for label in scores),
    }


def dominant_surface(points_base: np.ndarray, band_m: float) -> np.ndarray:
    """The mask's dominant depth layer — the target's visible lid.

    A segmentation mask's boundary pixels straddle the silhouette and
    pick up BACKGROUND depth: on the golden frame ~5% of the grounded
    mask lands on the tray floor, 8 cm below the lid. Those outliers
    dragged the plane fit almost vertical and would also inflate the
    footprint. `band_m` comes from thresholds.toml — it decides which
    points reach BOTH geometry stages, so it is a frozen threshold, not
    a constant (PR #103 review round 3)."""
    points = np.asarray(points_base, dtype=np.float64).reshape(-1, 3)
    if len(points) == 0:
        return points
    median_z = float(np.median(points[:, 2]))
    return points[np.abs(points[:, 2] - median_z) <= band_m]


def fit_surface(points_base: np.ndarray, min_rank_ratio: float) -> tuple:
    """(centroid, unit normal oriented +z) of the lid plane, or (None,
    None) when no plane is determined.

    Collinear or near-collinear masks do NOT define a plane: their
    second singular value collapses and the "normal" is arbitrary, which
    previously produced a confident tilt from three collinear points
    (PR #103 review round 3). The rank test is the singular-value ratio
    s1/s0 — points must genuinely span two dimensions."""
    points = np.asarray(points_base, dtype=np.float64).reshape(-1, 3)
    if len(points) < 3 or not np.all(np.isfinite(points)):
        return None, None
    centroid = points.mean(axis=0)
    _, sv, vh = np.linalg.svd(points - centroid, full_matrices=False)
    if sv[0] <= 0 or (sv[1] / sv[0]) < min_rank_ratio:
        return None, None
    normal = vh[-1]
    norm = np.linalg.norm(normal)
    if not np.isfinite(norm) or norm <= 0:
        return None, None
    normal = normal / norm
    if normal[2] < 0:  # orient outward (up) so the body lies BELOW the lid
        normal = -normal
    return centroid, normal


def reconstruct_box_points(lid_points: np.ndarray, normal, height_m: float) -> np.ndarray:
    """Lid points plus the opposite face, obtained by translating the lid
    along its ORIENTED normal by the class height.

    A top-down camera sees one face; the body hangs beneath it along the
    lid's own normal, NOT straight down. For a tilted box those differ:
    a 30-degree lid inside the tray footprint has its bottom face
    displaced by height*sin(30) — 4.25 cm for a standard med — which can
    put the body outside the tray while the lid looks contained
    (PR #103 review round 3: a dangerous false success)."""
    lid = np.asarray(lid_points, dtype=np.float64).reshape(-1, 3)
    return np.vstack([lid, lid - np.asarray(normal, dtype=np.float64) * float(height_m)])


def surface_top_z(points_base: np.ndarray) -> float:
    """The observed top-surface height (m)."""
    return float(np.median(np.asarray(points_base, dtype=np.float64).reshape(-1, 3)[:, 2]))


def containment_vote(
    lid_points_base: np.ndarray,
    tray_min,
    tray_max,
    margin_m: float,
    resting_tolerance_m: float,
    target_height_m: float,
    min_rank_ratio: float,
) -> StageVote:
    """VER-10: the RECONSTRUCTED BOX must sit inside the tray volume and
    rest on its floor — mirroring the oracle's VER-2 predicate over the
    box's world AABB, not over the visible lid alone."""
    lid = np.asarray(lid_points_base, dtype=np.float64).reshape(-1, 3)
    if lid.size == 0:
        return StageVote("error", detail="no target points from overhead depth")
    _, normal = fit_surface(lid, min_rank_ratio)
    if normal is None:
        return StageVote(
            "error", detail="mask does not determine a surface (rank-deficient or non-finite)"
        )
    box = reconstruct_box_points(lid, normal, target_height_m)
    # FOOTPRINT: the full extent of both faces — outliers are the point
    # here, since a displaced body is exactly what must be caught
    lo = np.asarray(tray_min[:2], dtype=np.float64) - margin_m
    hi = np.asarray(tray_max[:2], dtype=np.float64) + margin_m
    inside = np.minimum(box[:, :2] - lo, hi - box[:, :2])
    margin = float(inside.min())
    # RESTING HEIGHT: a robust central estimate of the reconstructed
    # bottom face. The strict minimum is contaminated by mask-silhouette
    # pixels that survive the depth band — on the golden delivery it read
    # 8.5 mm BELOW the tray floor, while the median lands within 0.2 mm
    # of the true bottom (PR #103 review round 3 follow-through).
    top_z = surface_top_z(lid)
    bottom_z = float(np.median(box[len(lid) :, 2]))
    rest_gap = bottom_z - float(tray_min[2])
    measurement = {
        "margin_m": round(margin, 6),
        "top_surface_z_m": round(top_z, 6),
        "rest_gap_m": round(rest_gap, 6),
    }
    if margin < 0:
        return StageVote("fail", measurement=measurement, detail="reconstructed box overhangs")
    if not (-margin_m <= rest_gap <= resting_tolerance_m):
        return StageVote(
            "fail",
            measurement=measurement,
            detail="reconstructed bottom is not resting on the tray floor",
        )
    return StageVote("pass", measurement=measurement)


def upright_vote(
    mask_points_base: np.ndarray, upright_max_deg: float, min_rank_ratio: float
) -> StageVote:
    """VER-11: top-surface tilt within the SAME 30-degree band as VER-2,
    measured from the fitted plane's NORMAL. A mask that does not
    determine a plane is unable-to-judge, never a pass."""
    _, normal = fit_surface(mask_points_base, min_rank_ratio)
    if normal is None:
        return StageVote(
            "error", detail="mask does not determine a surface (rank-deficient or non-finite)"
        )
    cos = abs(float(np.dot(normal, [0.0, 0.0, 1.0])))
    tilt = math.degrees(math.acos(min(1.0, cos)))
    status = "pass" if tilt <= upright_max_deg else "fail"
    return StageVote(status, measurement={"tilt_deg": round(tilt, 4)})


def home_vote(joint_state, home_qpos, tolerance_rad: float) -> StageVote:
    """VER-12: joint_state within the SAME home tolerance as VER-2.

    A TRUNCATED state is an unable-to-judge condition, not a pass (PR
    #103 review round 2): comparing only the overlapping prefix let a
    one-element array score a zero residual against the seven-joint home
    vector."""
    current = np.asarray(joint_state, dtype=np.float64).reshape(-1)
    home = np.asarray(home_qpos, dtype=np.float64).reshape(-1)
    if home.size == 0:
        return StageVote("error", detail="empty home_qpos")
    if current.size != home.size:
        return StageVote(
            "error",
            detail=f"joint_state has {current.size} values, home has {home.size} (VER-12)",
        )
    if not (np.all(np.isfinite(current)) and np.all(np.isfinite(home))):
        return StageVote("error", detail="non-finite joint values")
    residual = float(np.abs(current - home).max())
    status = "pass" if residual <= tolerance_rad else "fail"
    return StageVote(status, measurement={"max_joint_residual_rad": round(residual, 6)})
