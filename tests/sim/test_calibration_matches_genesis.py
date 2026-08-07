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
