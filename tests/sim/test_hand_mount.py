"""Sim regression for the Franka hand-mount compensation (issue #92,
PR #93 review P2): the unit-level mount tests are self-referential
(topdown_quat adds HAND_MOUNT_YAW, finger_yaw_of subtracts it — any
value passes), so THIS test measures the PHYSICAL finger-separation
axis in Genesis against the planner's grip-axis convention. It catches
a wrong offset sign, a wrong magnitude, or an upstream asset-mount
change directly, instead of relying on the 50-episode M0 pass rate.

Marker `sim`: imports genesis, runs headless (CON-12).
"""

import importlib.util
import math

import numpy as np
import pytest

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None,
        reason="genesis not installed (uv sync --extra sim)",
    ),
]


def test_planned_grip_yaw_matches_physical_finger_axis():
    """For planned grip yaw psi the planner's tips sit along
    u(psi) = (-sin psi, cos psi) — axis 90+psi degrees (mod 180). The
    fingers realized in Genesis after solving topdown_rotation(psi)
    must separate along that axis: the uncompensated flange target
    executed 45 degrees off (the m0 seed-3 topple, T10 diagonal
    detents)."""
    from aisle.nodes.ik_trajectory import ik_solve, topdown_rotation
    from aisle.scenes.pharmacy import build_scene, to_numpy

    handle = build_scene(seed=3, embodiment="franka", n_envs=1, headless=True)
    robot = handle.robot
    home = np.array([0.0, 0.2, 0.0, -2.6, 0.0, 1.2, 0.785], dtype=np.float32)
    links = {link.name: link for link in robot.links}

    for grip_yaw in (0.0, math.pi / 2):
        q = ik_solve(np.array([0.394, 0.204, 0.13]), topdown_rotation(grip_yaw), home)
        assert q is not None, f"IK failed for grip yaw {grip_yaw}"
        full = np.concatenate([q, [0.04, 0.04]]).astype(np.float32)
        robot.set_qpos(full[: robot.n_qs])
        handle.scene.step()
        lf = to_numpy(links["left_finger"].get_pos()).reshape(-1)
        rf = to_numpy(links["right_finger"].get_pos()).reshape(-1)
        axis = math.degrees(math.atan2(lf[1] - rf[1], lf[0] - rf[0])) % 180.0
        expected = (90.0 + math.degrees(grip_yaw)) % 180.0
        error = min(abs(axis - expected), 180.0 - abs(axis - expected))
        assert error < 2.0, (
            f"grip yaw {math.degrees(grip_yaw):.0f}: physical separation axis "
            f"{axis:.1f} deg, planner expects {expected:.1f} deg (off {error:.1f})"
        )
