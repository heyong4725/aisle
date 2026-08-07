"""VER-8 unit tests (SPEC 040, realistic-verifier increment one): the
v1 calibration schema, the pinned Genesis-v1.2.3 conversions, and the
four independent corruption refusals. Pure numpy — no sim, no models
(CON-12)."""

import copy
import math

import numpy as np
import pytest

from aisle.verifier.calibration import (
    GL_TO_CV,
    build_calibration_v1,
    calibration_sha256,
    check_calibration,
    intrinsics_v1,
    lookat_rotation_cv,
    quat_xyzw_from_rotation,
)

pytestmark = pytest.mark.unit

# the desk scene's SCN-5 + physics.toml [cameras] nominals
OVERHEAD_POS = [0.55, 0.0, 1.20]
OVERHEAD_LOOKAT = [0.55, 0.0, 0.20]
WRIST_OFFSET = [0.0, 0.0, 0.05]
JITTER_M = 0.04


def nominal_block():
    block = build_calibration_v1(
        OVERHEAD_POS, OVERHEAD_LOOKAT, (640, 480), 55.0, WRIST_OFFSET, (320, 240), 70.0
    )
    block["_overhead_lookat"] = OVERHEAD_LOOKAT
    return block


def published_block(**overrides):
    return build_calibration_v1(
        overrides.get("pos", OVERHEAD_POS),
        OVERHEAD_LOOKAT,
        (640, 480),
        55.0,
        WRIST_OFFSET,
        (320, 240),
        70.0,
    )


def test_intrinsics_match_genesis_conversion_numerics():
    """VER-8/BRG-8: concrete numbers after the Genesis-to-v1 conversion —
    overhead 640x480 at vertical FOV 55: fx = fy = 240/tan(27.5 deg),
    principal point at the OpenCV pixel-center (w-1)/2 = Genesis's
    corner-convention w/2 minus the half-pixel shift."""
    k = intrinsics_v1((640, 480), 55.0)
    assert k["fx"] == pytest.approx(240.0 / math.tan(math.radians(27.5)))
    assert k["fx"] == pytest.approx(461.04, abs=0.01)
    assert k["fy"] == k["fx"]
    assert k["cx"] == pytest.approx(319.5) and k["cy"] == pytest.approx(239.5)


def test_lookat_rotation_matches_genesis_including_the_degenerate_overhead():
    """VER-8 roll rule mirrors pinned Genesis v1.2.3 `_np_z_up_to_R`.
    The desk overhead camera looks STRAIGHT DOWN — colinear with the
    default up=(0,0,1) — so `cross(up, z)` vanishes and Genesis takes
    its degenerate branch: identity GL rotation, i.e. OpenCV
    diag(1,-1,-1) with the optical axis pointing down. A tilted camera
    takes the normal branch and must match the cross-product frame."""
    r_cv = lookat_rotation_cv(OVERHEAD_POS, OVERHEAD_LOOKAT)
    assert np.allclose(r_cv, np.diag([1.0, -1.0, -1.0]), atol=1e-9)
    assert r_cv[:, 2] == pytest.approx([0.0, 0.0, -1.0], abs=1e-9)  # optical axis down
    assert np.allclose(r_cv @ r_cv.T, np.eye(3), atol=1e-9)

    tilted = lookat_rotation_cv([0.2, 0.0, 1.0], [0.55, 0.0, 0.20])
    z_gl = np.array([0.2, 0.0, 1.0]) - np.array([0.55, 0.0, 0.20])
    z_gl /= np.linalg.norm(z_gl)
    x_gl = np.cross([0.0, 0.0, 1.0], z_gl)
    x_gl /= np.linalg.norm(x_gl)
    expected = np.column_stack([x_gl, np.cross(z_gl, x_gl), z_gl]) @ GL_TO_CV
    assert np.allclose(tilted, expected, atol=1e-9)

    with pytest.raises(ValueError, match="coincides"):
        lookat_rotation_cv([0.5, 0.5, 0.5], [0.5, 0.5, 0.5])


def test_clean_block_passes_and_jittered_position_passes():
    """A jitter-consistent published block (position moved within the
    per-axis ±jitter/2 bound, rotation re-aimed at the lookat) judges
    clean — the predicate accepts what the DR actually produces."""
    nominal = nominal_block()
    assert check_calibration(published_block(), nominal, JITTER_M) is None
    jittered_pos = [OVERHEAD_POS[0] + JITTER_M / 2 - 1e-6, OVERHEAD_POS[1], OVERHEAD_POS[2]]
    assert check_calibration(published_block(pos=jittered_pos), nominal, JITTER_M) is None


def test_four_independent_corruptions_each_refuse():
    """VER-8 acceptance: depth scale, one intrinsic, translation beyond
    bound, rotation beyond bound — each refuses independently."""
    nominal = nominal_block()

    depth = published_block()
    depth["overhead"]["depth_scale_m"] = 0.001  # millimeter depth
    assert "depth_scale_m" in check_calibration(depth, nominal, JITTER_M)

    intr = published_block()
    intr["overhead"]["intrinsics"]["fx"] += 1.0
    assert "intrinsics.fx" in check_calibration(intr, nominal, JITTER_M)

    trans = published_block(pos=[OVERHEAD_POS[0] + JITTER_M, OVERHEAD_POS[1], OVERHEAD_POS[2]])
    assert "position deviates" in check_calibration(trans, nominal, JITTER_M)

    rot = published_block()
    spun = lookat_rotation_cv(OVERHEAD_POS, OVERHEAD_LOOKAT) @ np.array(
        [
            [math.cos(math.radians(5)), -math.sin(math.radians(5)), 0.0],
            [math.sin(math.radians(5)), math.cos(math.radians(5)), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    rot["overhead"]["cam_to_base"]["quat_xyzw"] = quat_xyzw_from_rotation(spun)
    assert "rotation" in check_calibration(rot, nominal, JITTER_M)  # rotated in place


def test_malformed_and_wrong_version_refuse():
    nominal = nominal_block()
    assert "calibration_version" in check_calibration({}, nominal, JITTER_M)
    broken = published_block()
    del broken["overhead"]["intrinsics"]
    assert "malformed" in check_calibration(broken, nominal, JITTER_M)


def test_wrist_mount_corruption_refuses():
    nominal = nominal_block()
    moved = published_block()
    moved["wrist"]["cam_to_ee"]["pos"] = [0.0, 0.01, 0.05]
    assert "cam_to_ee" in check_calibration(moved, nominal, JITTER_M)


def test_sha256_is_canonical_and_order_independent():
    """VER-8: the audit hash is over sorted-keys JSON — key order in the
    constructed dict must not change it."""
    a = published_block()
    b = copy.deepcopy(a)
    b["overhead"] = dict(reversed(list(b["overhead"].items())))
    assert calibration_sha256(a) == calibration_sha256(b)


def test_non_finite_and_non_unit_quaternions_fail_closed():
    """PR #103 review: a NaN position passed every numeric comparison,
    and doubling a quaternion's components read as a perfect rotation
    match because the dot product was clamped. Stage 0 validates
    structure, finiteness, and unit norm BEFORE comparing."""
    nominal = nominal_block()

    nan_pos = published_block()
    nan_pos["overhead"]["cam_to_base"]["pos"] = [float("nan"), 0.0, 1.2]
    assert "non-finite" in check_calibration(nan_pos, nominal, JITTER_M)

    inf_depth = published_block()
    inf_depth["overhead"]["depth_scale_m"] = float("inf")
    assert "non-finite" in check_calibration(inf_depth, nominal, JITTER_M)

    scaled = published_block()
    scaled["overhead"]["cam_to_base"]["quat_xyzw"] = [
        2 * v for v in scaled["overhead"]["cam_to_base"]["quat_xyzw"]
    ]
    refusal = check_calibration(scaled, nominal, JITTER_M)
    assert "norm" in refusal, f"scaled quaternion accepted: {refusal}"

    short = published_block()
    short["overhead"]["cam_to_base"]["quat_xyzw"] = [0.0, 0.0, 1.0]
    assert "4 components" in check_calibration(short, nominal, JITTER_M)

    nan_intrinsic = published_block()
    nan_intrinsic["overhead"]["intrinsics"]["fy"] = float("nan")
    assert "non-finite" in check_calibration(nan_intrinsic, nominal, JITTER_M)


def test_realized_rotation_is_published_not_rederived():
    """BRG-8 (PR #103 review): the block must carry the REALIZED camera
    rotation. If it re-derived the rotation from config, a camera
    rotated in place would publish the expected pose and stage 0 could
    never catch it — the exact VER-8 corruption case."""
    spun = lookat_rotation_cv(OVERHEAD_POS, OVERHEAD_LOOKAT) @ np.array(
        [
            [math.cos(math.radians(10)), -math.sin(math.radians(10)), 0.0],
            [math.sin(math.radians(10)), math.cos(math.radians(10)), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    published = build_calibration_v1(
        OVERHEAD_POS,
        OVERHEAD_LOOKAT,
        (640, 480),
        55.0,
        WRIST_OFFSET,
        (320, 240),
        70.0,
        overhead_rotation_cv=spun,
    )
    assert "rotation" in check_calibration(published, nominal_block(), JITTER_M)


def test_absent_or_non_mapping_calibration_refuses_without_raising():
    """VER-8 (PR #103 review round 3): an ABSENT block must refuse like
    any other malformed calibration — `.get` on None previously raised
    AttributeError straight out of the judge, so no verdict and no
    sidecar were produced."""
    nominal = nominal_block()
    for absent in (None, [], "calibration", 42):
        refusal = check_calibration(absent, nominal, JITTER_M)
        assert refusal is not None and "not an object" in refusal, absent
    assert check_calibration(published_block(), None, JITTER_M) is not None
