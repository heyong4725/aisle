"""VER-8 conformance against the REAL built scene (SPEC 040).

The unit tests pin the v1 conventions against the spec; this test pins
them against pinned Genesis v1.2.3 itself — the only thing that can
tell us the conversion is right. It caught a real defect during
implementation: the desk overhead camera is colinear with Genesis's
default `up`, and its float32 position differs from the float64 nominal
by ~1.2e-8, so a degeneracy epsilon tighter than `gs.EPS` (float32 eps)
silently took the cross-product branch and produced a 90-degree-wrong
camera roll — invisible to every spec-only test.

Marker `sim`: imports genesis, runs headless (CON-12).
"""

import importlib.util

import numpy as np
import pytest

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None,
        reason="genesis not installed (uv sync --extra sim)",
    ),
]


def test_v1_calibration_matches_the_built_genesis_camera():
    """VER-8/BRG-8: the v1 block derived from the frozen config must
    equal what Genesis realized — rotation (after the GL->CV
    conversion), focal length, and the principal-point half-pixel
    shift."""
    from aisle.scenes.pharmacy import build_scene, load_physics, to_numpy
    from aisle.verifier.calibration import (
        GL_TO_CV,
        intrinsics_v1,
        lookat_rotation_cv,
    )

    handle = build_scene(seed=3, embodiment="franka", n_envs=1, headless=True)
    cam = handle.cams["overhead"]
    transform = np.asarray(to_numpy(cam.transform)).reshape(4, 4)
    physics = load_physics()

    realized_cv = transform[:3, :3] @ GL_TO_CV
    derived_cv = lookat_rotation_cv(transform[:3, 3], physics["cameras"]["overhead_lookat"])
    assert np.allclose(derived_cv, realized_cv, atol=1e-6), (
        "v1 rotation disagrees with the realized Genesis camera "
        f"(maxdiff {np.abs(derived_cv - realized_cv).max():.3e})"
    )

    k = np.asarray(cam.intrinsics)
    mine = intrinsics_v1((640, 480), 55.0)
    assert k[0, 0] == pytest.approx(mine["fx"], abs=1e-4)
    assert k[1, 1] == pytest.approx(mine["fy"], abs=1e-4)
    # Genesis stores the corner-convention principal point; v1 stores
    # pixel centers — exactly half a pixel apart, both axes
    assert k[0, 2] - mine["cx"] == pytest.approx(0.5, abs=1e-9)
    assert k[1, 2] - mine["cy"] == pytest.approx(0.5, abs=1e-9)


def test_wrist_cam_to_ee_matches_the_attached_genesis_camera():
    """VER-8/SCN-5: the published `cam_to_ee` must equal the mount Genesis
    actually realized, expressed in OpenCV.

    Nothing checked this before, and the block hard-coded the GL->CV of an
    IDENTITY mount while the scene attached the camera with no rotation at
    all. Both were self-consistent, so every spec-only test passed — and
    the camera pointed straight back up the arm, which is why no wrist
    frame in any recorded run ever contained the workspace (issue #109).
    A conformance test is the only thing that can catch that class of
    defect; this is the wrist's."""
    from aisle.nodes.dora_genesis import realized_calibration
    from aisle.scenes.pharmacy import (
        FRANKA_EE_LINK,
        build_scene,
        load_physics,
        to_numpy,
    )
    from aisle.verifier.calibration import GL_TO_CV, rotation_from_quat_xyzw

    physics = load_physics()
    handle = build_scene(seed=3, embodiment="franka", n_envs=1, headless=True)
    handle.scene.step()
    # an attached camera's transform is stale until it renders — reading it
    # straight after attach reports the pose it was built at, not the pose
    # it is at (cost me a wrong measurement while diagnosing #109)
    handle.cams["wrist"].render(rgb=True)

    link = handle.robot.get_link(FRANKA_EE_LINK)
    link_quat_wxyz = to_numpy(link.get_quat()).reshape(-1)[:4]
    link_rot = rotation_from_quat_xyzw(np.roll(link_quat_wxyz, -1))
    cam_gl = np.asarray(to_numpy(handle.cams["wrist"].transform)).reshape(4, 4)[:3, :3]

    realized_cv = (link_rot.T @ cam_gl) @ GL_TO_CV
    published = rotation_from_quat_xyzw(
        realized_calibration(handle, physics, is_store=False)["wrist"]["cam_to_ee"]["quat_xyzw"]
    )
    assert np.allclose(published, realized_cv, atol=1e-5), (
        "published cam_to_ee disagrees with the attached camera "
        f"(maxdiff {np.abs(published - realized_cv).max():.3e})"
    )

    # and the camera must look where the gripper goes: the CV optical axis
    # (+Z) is the link's approach axis, not its reverse
    optical_axis_in_link = realized_cv[:, 2]
    assert optical_axis_in_link[2] == pytest.approx(1.0, abs=1e-5), (
        f"wrist camera looks along {optical_axis_in_link} of the EE link, not its +Z approach axis"
    )
