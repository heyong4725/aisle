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
    """Angle between two UNIT xyzw quaternions. Callers must validate
    normalisation first (`check_quaternion`): clamping an oversized dot
    product would silently read a scaled quaternion as a zero-angle
    match (PR #103 review)."""
    a = np.asarray(qa, dtype=np.float64)
    b = np.asarray(qb, dtype=np.float64)
    dot = abs(float(np.dot(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def check_quaternion(value, label: str) -> str | None:
    """None iff `value` is a finite, unit-norm xyzw quaternion."""
    arr = np.asarray(value, dtype=np.float64).reshape(-1)
    if arr.shape != (4,):
        return f"{label}: quaternion must have 4 components, got {arr.shape[0]}"
    if not np.all(np.isfinite(arr)):
        return f"{label}: quaternion has non-finite components"
    norm = float(np.linalg.norm(arr))
    if abs(norm - 1.0) > 1e-6:
        return f"{label}: quaternion norm {norm:.6f} is not 1 (not a rotation)"
    return None


def check_finite(value, label: str, length: int | None = None) -> str | None:
    """None iff `value` is finite (and the right length, if given)."""
    arr = np.asarray(value, dtype=np.float64).reshape(-1)
    if length is not None and arr.shape != (length,):
        return f"{label}: expected {length} values, got {arr.shape[0]}"
    if not np.all(np.isfinite(arr)):
        return f"{label}: non-finite value"
    return None


def build_calibration_v1(
    overhead_pos,
    overhead_lookat,
    overhead_resolution: tuple[int, int],
    overhead_fov_deg: float,
    wrist_offset_m,
    wrist_resolution: tuple[int, int],
    wrist_fov_deg: float,
    overhead_rotation_cv=None,
    wrist_mount_rotation_gl=None,
) -> dict:
    """The v1 block from realized camera state (VER-8 schema). The bridge
    calls this with the BUILT scene's post-jitter overhead position
    (BRG-8); nominals come from the frozen scene config via the same
    function.

    `wrist_mount_rotation_gl` is the scene's GL-convention camera->EE
    mount (SCN-5, `wrist_rotation_xyzw`); `cam_to_ee` is it converted to
    OpenCV. It is a parameter rather than a constant because the two must
    move together: the block previously hard-coded the GL->CV of an
    IDENTITY mount, which faithfully described a camera aimed back up the
    arm — the projection was right and the scene was wrong (issue #109)."""
    return {
        "calibration_version": CALIBRATION_VERSION,
        "overhead": {
            "resolution": [int(overhead_resolution[0]), int(overhead_resolution[1])],
            "fov_deg": float(overhead_fov_deg),
            "intrinsics": intrinsics_v1(overhead_resolution, overhead_fov_deg),
            "cam_to_base": {
                "pos": [float(v) for v in overhead_pos],
                # the REALIZED rotation when the caller has it (BRG-8
                # publishes the built scene's actual transform). Falling
                # back to the lookat derivation is for NOMINAL blocks
                # only: publishing a re-derived rotation would make a
                # rotated-in-place camera pass stage 0 by construction
                # (PR #103 review).
                "quat_xyzw": quat_xyzw_from_rotation(
                    lookat_rotation_cv(overhead_pos, overhead_lookat)
                    if overhead_rotation_cv is None
                    else np.asarray(overhead_rotation_cv, dtype=np.float64)
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
                "quat_xyzw": quat_xyzw_from_rotation(
                    (
                        np.eye(3)
                        if wrist_mount_rotation_gl is None
                        else np.asarray(wrist_mount_rotation_gl, dtype=np.float64)
                    )
                    @ GL_TO_CV
                ),
            },
        },
    }


def calibration_sha256(block: dict) -> str:
    """Audit hash: sha256 of the canonical sorted-keys JSON (VER-8)."""
    import hashlib

    return hashlib.sha256(json.dumps(block, sort_keys=True).encode()).hexdigest()


def check_calibration(published: dict, nominal: dict, jitter_bound_m: float) -> str | None:
    """VER-8 stage-0 refusal predicate: None iff every check passes."""
    return calibration_report(published, nominal, jitter_bound_m)[0]


def calibration_report(
    published: dict, nominal: dict, jitter_bound_m: float
) -> tuple[str | None, dict]:
    """(refusal, deviations) for VER-8 stage 0.

    The refusal is None iff every check passes; `deviations` carries the
    measured nominal-vs-actual deltas that VER-14 records for the
    calibration stage (PR #103 review round 2: a bare `vote: pass`
    carried no measurement). Each check is independent and fails closed:
    (a) structure, supported version, finiteness, unit quaternions;
    (b) resolution, fov_deg, intrinsics and depth scale EXACTLY nominal
    (DR never perturbs them); (c) overhead position within
    jitter_bound_m/2 per axis; (d) overhead rotation within
    ROTATION_TOL_DEG of the rotation re-derived FROM the published
    position; (e) wrist cam_to_ee exactly nominal.
    """
    deviations: dict = {}
    try:
        # VER-8: an ABSENT or non-mapping block refuses like any other
        # malformed calibration — `.get` on None raised AttributeError
        # straight out of the judge (PR #103 review round 3)
        if not isinstance(published, dict):
            return f"calibration block is {type(published).__name__}, not an object", deviations
        if not isinstance(nominal, dict):
            return f"nominal calibration is {type(nominal).__name__}, not an object", deviations
        if published.get("calibration_version") != CALIBRATION_VERSION:
            return (
                f"unsupported calibration_version {published.get('calibration_version')!r}",
                deviations,
            )
        pub_o, nom_o = published["overhead"], nominal["overhead"]
        pub_w, nom_w = published["wrist"], nominal["wrist"]

        # (a) structure + finiteness BEFORE any comparison: a NaN position
        # passed every numeric check, and a scaled quaternion read as a
        # perfect rotation match once the dot product was clamped
        for cam, pub_c in (("overhead", pub_o), ("wrist", pub_w)):
            pose_key = "cam_to_base" if cam == "overhead" else "cam_to_ee"
            if "fov_deg" not in pub_c:
                return f"{cam}: calibration block is missing fov_deg", deviations
            pose = pub_c[pose_key]
            checks = [
                (f"{cam}.fov_deg", pub_c["fov_deg"], 1),
                (f"{cam}.intrinsics.fx", pub_c["intrinsics"]["fx"], 1),
                (f"{cam}.intrinsics.fy", pub_c["intrinsics"]["fy"], 1),
                (f"{cam}.intrinsics.cx", pub_c["intrinsics"]["cx"], 1),
                (f"{cam}.intrinsics.cy", pub_c["intrinsics"]["cy"], 1),
                (f"{cam}.{pose_key}.pos", pose["pos"], 3),
            ]
            for label, value, length in checks:
                bad = check_finite(value, label, length)
                if bad:
                    return bad, deviations
            bad = check_quaternion(pose["quat_xyzw"], f"{cam}.{pose_key}")
            if bad:
                return bad, deviations
        bad = check_finite(pub_o["depth_scale_m"], "overhead.depth_scale_m", 1)
        if bad:
            return bad, deviations

        # (b) the non-DR fields must be EXACTLY nominal
        for cam, pub_c, nom_c in (("overhead", pub_o, nom_o), ("wrist", pub_w, nom_w)):
            if list(pub_c["resolution"]) != list(nom_c["resolution"]):
                return (
                    f"{cam}: resolution {pub_c['resolution']} != nominal {nom_c['resolution']}",
                    deviations,
                )
            if float(pub_c["fov_deg"]) != float(nom_c["fov_deg"]):
                return (
                    f"{cam}: fov_deg {pub_c['fov_deg']} != nominal {nom_c['fov_deg']}",
                    deviations,
                )
            for k, v in nom_c["intrinsics"].items():
                if float(pub_c["intrinsics"][k]) != float(v):
                    return (
                        f"{cam}: intrinsics.{k} {pub_c['intrinsics'][k]} != nominal {v}",
                        deviations,
                    )
        if float(pub_o["depth_scale_m"]) != float(nom_o["depth_scale_m"]):
            return (
                f"overhead: depth_scale_m {pub_o['depth_scale_m']} != {nom_o['depth_scale_m']}",
                deviations,
            )

        # (c) overhead position: per-axis uniform jitter is +/-bound/2
        pos = np.asarray(pub_o["cam_to_base"]["pos"], dtype=np.float64)
        nom_pos = np.asarray(nom_o["cam_to_base"]["pos"], dtype=np.float64)
        dev = np.abs(pos - nom_pos)
        deviations["overhead_pos_dev_m"] = [round(float(v), 6) for v in dev]
        deviations["overhead_pos_max_dev_m"] = round(float(dev.max()), 6)
        if np.any(dev > jitter_bound_m / 2 + 1e-12):
            return (
                f"overhead: position deviates {dev.max():.4f} m from nominal "
                f"(bound {jitter_bound_m / 2:.4f} m per axis)",
                deviations,
            )

        # (d) rotation re-derived from the PUBLISHED position, so jitter
        # is accounted for but a rotated-in-place camera refuses
        lookat = nominal.get("_overhead_lookat")
        if lookat is None:
            return "nominal block missing _overhead_lookat for the rotation predicate", deviations
        expected = quat_xyzw_from_rotation(lookat_rotation_cv(pos, lookat))
        angle = quat_angle_deg(pub_o["cam_to_base"]["quat_xyzw"], expected)
        deviations["overhead_rotation_dev_deg"] = round(float(angle), 4)
        if angle > ROTATION_TOL_DEG:
            return (
                f"overhead: rotation {angle:.2f} deg from lookat-consistent pose (VER-8 d)",
                deviations,
            )

        # (e) wrist mount EXACT
        if not np.array_equal(
            np.asarray(pub_w["cam_to_ee"]["pos"], dtype=np.float64),
            np.asarray(nom_w["cam_to_ee"]["pos"], dtype=np.float64),
        ):
            return "wrist: cam_to_ee.pos differs from nominal mount", deviations
        if quat_angle_deg(pub_w["cam_to_ee"]["quat_xyzw"], nom_w["cam_to_ee"]["quat_xyzw"]) > 1e-6:
            return "wrist: cam_to_ee rotation differs from nominal mount", deviations
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        return f"malformed calibration block: {exc!r}", deviations
    return None, deviations
