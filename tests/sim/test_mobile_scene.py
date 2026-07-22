"""Sim tests for the mobile embodiment's kinematic base (SPEC 210 T11,
ADR-13). Marker `sim`: imports genesis, headless (CON-12)."""

import importlib.util
import math

import numpy as np
import pytest

from aisle.mobility.base import integrate_base_pose
from aisle.nodes.ik_trajectory import fk_tcp
from aisle.scenes.pharmacy import build_scene, to_numpy

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None, reason="sim extra not installed"
    ),
]


def test_mobile_builds_franka_arm():
    """MOB-4: the mobile profile builds the franka arm (arm subtree
    identical) — home FK matches the fixed-base franka."""
    mobile = build_scene(seed=0, embodiment="mobile", headless=True)
    franka = build_scene(seed=0, embodiment="franka", headless=True)
    tcp_m = fk_tcp(to_numpy(mobile.robot.get_qpos()).reshape(-1)[:7])
    tcp_f = fk_tcp(to_numpy(franka.robot.get_qpos()).reshape(-1)[:7])
    assert tcp_m == pytest.approx(tcp_f, abs=1e-4)


def test_kinematic_rebase_moves_the_arm_mount():
    """ADR-13: integrating base_cmd and re-basing the robot moves the whole
    arm's world mount, while the arm stays base-relative (joint FK
    unchanged)."""
    h = build_scene(seed=0, embodiment="mobile", headless=True)
    robot = h.robot
    tcp_before = fk_tcp(to_numpy(robot.get_qpos()).reshape(-1)[:7])

    pose = [0.0, 0.0, 0.0]
    for _ in range(10):  # 1 m forward at 1 m/s over 0.1 s ticks
        pose = integrate_base_pose(pose, [1.0, 0.0], dt=0.1)
    qz = math.sin(pose[2] / 2), math.cos(pose[2] / 2)
    robot.set_pos(np.array([pose[0], pose[1], 0.0], dtype=np.float32))
    robot.set_quat(np.array([qz[1], 0.0, 0.0, qz[0]], dtype=np.float32))  # wxyz
    h.scene.step()

    assert to_numpy(robot.get_pos()).reshape(-1)[:2] == pytest.approx([1.0, 0.0], abs=1e-3)
    # the arm-frame FK is unchanged: the arm rode the base, it did not move
    tcp_after = fk_tcp(to_numpy(robot.get_qpos()).reshape(-1)[:7])
    assert tcp_after == pytest.approx(tcp_before, abs=1e-2)
