"""VER-8 calibration contract (SPEC 040; ADR-realistic-verifier).

The v1 calibration block, its Genesis-to-v1 conversions, and the stage-0
refusal predicate. Conventions (v1 = OpenCV): pixel (0,0)'s CENTER is
(0,0); optical frame +Z into the scene, +X image-right, +Y image-down;
quaternions TC-1 xyzw; poses camera->frame in the robot base frame;
depth float32 meters; `fov_deg` is the VERTICAL field of view.

Genesis v1.2.3 exposes cx = w/2, cy = h/2 with `u + 0.5` sampling
(pixel-corner convention) and stores an OpenGL camera pose (look along
-Z, +Y up) converted with diag(1,-1,-1,1) — the v1 block stores the
OpenCV-converted values, so consumers never touch Genesis conventions.

Pure numpy — importable without dora, sim, or torch (CON-12).
"""

from __future__ import annotations

import json
import math

import numpy as np

CALIBRATION_VERSION = 1
# VER-8 (d): the published overhead rotation must sit within this angle
# of the rotation re-derived from (published position, nominal lookat,
# up=(0,0,1)) — jitter re-aims the camera, so the bound is small
ROTATION_TOL_DEG = 1.0
# Genesis default camera up vector — the roll rule (VER-8)
UP_WORLD = np.array([0.0, 0.0, 1.0])
# OpenGL camera -> OpenCV optical frame (VER-8 / Genesis v1.2.3)
GL_TO_CV = np.diag([1.0, -1.0, -1.0])
# Genesis's own degeneracy threshold: gs.EPS = max(user eps, float32 eps)
# at the default float32 precision. This value is LOAD-BEARING, not
# cosmetic: the desk overhead camera is colinear with `up`, and its
# float32 position differs from the float64 nominal by ~1.2e-8 — a
# tighter epsilon takes the cross-product branch while Genesis takes the
# degenerate one, yielding a 90-degree-wrong roll (caught by comparing
# against the built scene, not by reading the source).
DEGENERATE_EPS = float(np.finfo(np.float32).eps)


def intrinsics_v1(resolution: tuple[int, int], fov_deg: float) -> dict:
    """OpenCV pixel-center intrinsics from resolution + VERTICAL fov.
    fx = fy (square pixels); the half-pixel shift converts Genesis's
    corner-convention principal point (w/2, h/2) to pixel centers."""
    w, h = int(resolution[0]), int(resolution[1])
    f = (h / 2.0) / math.tan(math.radians(fov_deg) / 2.0)
    return {"fx": f, "fy": f, "cx": (w - 1) / 2.0, "cy": (h - 1) / 2.0}


def lookat_rotation_cv(pos, lookat, up=UP_WORLD) -> np.ndarray:
    """The OpenCV-frame camera rotation Genesis realizes for a look-at
    camera at `pos` (VER-8 roll rule: up = world +z, Genesis's camera
    default). Mirrors pinned Genesis v1.2.3 `_np_z_up_to_R` EXACTLY,
    including its degenerate branch — which the desk overhead camera
    actually takes: it looks straight down, colinear with up, so
    `cross(up, z)` vanishes and Genesis falls back to the IDENTITY GL
    rotation rather than an arbitrary roll. Columns of the returned
    matrix are the camera axes in the base frame: +X image-right,
    +Y image-down, +Z along the optical axis.
    """
    pos = np.asarray(pos, dtype=np.float64)
    # GL convention: the camera's +Z column points BACKWARD (pos - lookat)
    z = pos - np.asarray(lookat, dtype=np.float64)
    z_norm = np.linalg.norm(z)
    if z_norm < DEGENERATE_EPS:
        raise ValueError("lookat coincides with camera position")
    z = z / z_norm
    x = np.cross(np.asarray(up, dtype=np.float64), z)
    if np.linalg.norm(x) > DEGENERATE_EPS:
        x = x / np.linalg.norm(x)
        y = np.cross(z, x)
        r_gl = np.column_stack([x, y, z])
    else:
        # colinear z and up — Genesis yields identity (the overhead case)
        r_gl = np.eye(3)
    return r_gl @ GL_TO_CV


def quat_xyzw_from_rotation(rotation: np.ndarray) -> list[float]:
    """Matrix -> TC-1 xyzw quaternion (Shepperd, branch-stable)."""
    r = rotation
    trace = float(np.trace(r))
    if trace > 0:
        w = math.sqrt(1.0 + trace) / 2
        x = (r[2, 1] - r[1, 2]) / (4 * w)
        y = (r[0, 2] - r[2, 0]) / (4 * w)
        z = (r[1, 0] - r[0, 1]) / (4 * w)
        return [x, y, z, w]
    i = int(np.argmax(np.diag(r)))
    j, k = (i + 1) % 3, (i + 2) % 3
    s = math.sqrt(max(1.0 + r[i, i] - r[j, j] - r[k, k], 1e-12)) * 2
    vec = [0.0, 0.0, 0.0]
    vec[i] = s / 4
    vec[j] = (r[j, i] + r[i, j]) / s
    vec[k] = (r[k, i] + r[i, k]) / s
    w = (r[k, j] - r[j, k]) / s
    return [*vec, w]


def rotation_from_quat_xyzw(quat) -> np.ndarray:
    x, y, z, w = (float(v) for v in quat)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def quat_angle_deg(qa, qb) -> float:
    """Angle of the relative rotation between two xyzw quaternions."""
    dot = abs(float(np.dot(np.asarray(qa, dtype=np.float64), np.asarray(qb, dtype=np.float64))))
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def build_calibration_v1(
    overhead_pos,
    overhead_lookat,
    overhead_resolution: tuple[int, int],
    overhead_fov_deg: float,
    wrist_offset_m,
    wrist_resolution: tuple[int, int],
    wrist_fov_deg: float,
) -> dict:
    """The v1 block from realized camera state (VER-8 schema). The bridge
    calls this with the BUILT scene's post-jitter overhead position
    (BRG-8); nominals come from the frozen scene config via the same
    function. The wrist mount rotation is the identity mount expressed in
    the OpenCV convention (GL->CV of the identity GL mount)."""
    return {
        "calibration_version": CALIBRATION_VERSION,
        "overhead": {
            "resolution": [int(overhead_resolution[0]), int(overhead_resolution[1])],
            "fov_deg": float(overhead_fov_deg),
            "intrinsics": intrinsics_v1(overhead_resolution, overhead_fov_deg),
            "cam_to_base": {
                "pos": [float(v) for v in overhead_pos],
                "quat_xyzw": quat_xyzw_from_rotation(
                    lookat_rotation_cv(overhead_pos, overhead_lookat)
                ),
            },
            "depth_scale_m": 1.0,
        },
        "wrist": {
            "resolution": [int(wrist_resolution[0]), int(wrist_resolution[1])],
            "fov_deg": float(wrist_fov_deg),
            "intrinsics": intrinsics_v1(wrist_resolution, wrist_fov_deg),
            "cam_to_ee": {
                "pos": [float(v) for v in wrist_offset_m],
                "quat_xyzw": quat_xyzw_from_rotation(np.eye(3) @ GL_TO_CV),
            },
        },
    }


def calibration_sha256(block: dict) -> str:
    """Audit hash: sha256 of the canonical sorted-keys JSON (VER-8)."""
    import hashlib

    return hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()


def check_calibration(published: dict, nominal: dict, jitter_bound_m: float) -> str | None:
    """VER-8 stage-0 refusal predicate: None iff every check passes,
    else the refusal reason. Each check independent, fail closed:
    (a) well-formed + supported version; (b) intrinsics and depth scale
    EXACTLY nominal; (c) overhead position within jitter_bound_m/2 per
    axis; (d) overhead rotation within ROTATION_TOL_DEG of the
    re-derived lookat rotation FROM the published position; (e) wrist
    cam_to_ee exactly nominal."""
    try:
        if published.get("calibration_version") != CALIBRATION_VERSION:
            return f"unsupported calibration_version {published.get('calibration_version')!r}"
        pub_o, nom_o = published["overhead"], nominal["overhead"]
        pub_w, nom_w = published["wrist"], nominal["wrist"]
        # (b) intrinsics + depth scale exact — DR never perturbs them
        for cam, pub_c, nom_c in (("overhead", pub_o, nom_o), ("wrist", pub_w, nom_w)):
            if pub_c["resolution"] != nom_c["resolution"]:
                return f"{cam}: resolution {pub_c['resolution']} != nominal {nom_c['resolution']}"
            for k, v in nom_c["intrinsics"].items():
                if not math.isclose(float(pub_c["intrinsics"][k]), float(v), abs_tol=1e-9):
                    return f"{cam}: intrinsics.{k} {pub_c['intrinsics'][k]} != nominal {v}"
        if not math.isclose(
            float(pub_o["depth_scale_m"]), float(nom_o["depth_scale_m"]), abs_tol=0.0
        ):
            return f"overhead: depth_scale_m {pub_o['depth_scale_m']} != {nom_o['depth_scale_m']}"
        # (c) overhead position: per-axis uniform jitter is ±bound/2
        pos = np.asarray(pub_o["cam_to_base"]["pos"], dtype=np.float64)
        nom_pos = np.asarray(nom_o["cam_to_base"]["pos"], dtype=np.float64)
        dev = np.abs(pos - nom_pos)
        if np.any(dev > jitter_bound_m / 2 + 1e-12):
            return (
                f"overhead: position deviates {dev.max():.4f} m from nominal "
                f"(bound {jitter_bound_m / 2:.4f} m per axis)"
            )
        # (d) rotation: re-derive from the PUBLISHED position (jitter-
        # consistent) — a rotated-in-place camera at a correct position
        # refuses. The nominal lookat is recoverable from the nominal
        # block's pose only via the caller; nominal carries it verbatim.
        lookat = nominal.get("_overhead_lookat")
        if lookat is None:
            return "nominal block missing _overhead_lookat for the rotation predicate"
        expected = quat_xyzw_from_rotation(lookat_rotation_cv(pos, lookat))
        angle = quat_angle_deg(pub_o["cam_to_base"]["quat_xyzw"], expected)
        if angle > ROTATION_TOL_DEG:
            return f"overhead: rotation {angle:.2f} deg from lookat-consistent pose (VER-8 d)"
        # (e) wrist mount exact
        if not np.allclose(pub_w["cam_to_ee"]["pos"], nom_w["cam_to_ee"]["pos"], atol=1e-9):
            return "wrist: cam_to_ee.pos differs from nominal mount"
        if quat_angle_deg(pub_w["cam_to_ee"]["quat_xyzw"], nom_w["cam_to_ee"]["quat_xyzw"]) > 1e-6:
            return "wrist: cam_to_ee rotation differs from nominal mount"
    except (KeyError, TypeError, ValueError) as exc:
        return f"malformed calibration block: {exc!r}"
    return None
