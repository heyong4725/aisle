"""Unit tests for the ik-trajectory node's pure planning core (CAP-5
ik-trajectory) — DLS-IK on the shared Panda kinematics, staged pick-place
plan, velocity-bounded interpolation. No dora, no sim (CON-12)."""

import numpy as np
import pytest

from aisle.nodes.budget_guard import fk_flange, load_limits
from aisle.nodes.grasp_topdown import FRONT_QUAT, plan_grasp, topdown_quat
from aisle.nodes.ik_trajectory import (
    STAGES,
    StagedPlan,
    fk_tcp,
    ik_solve,
    interpolate_step,
    topdown_rotation,
)

pytestmark = pytest.mark.unit

LIMITS = load_limits("franka")
HOME = np.asarray(LIMITS.fallback_qpos[:7], dtype=np.float32)


def test_fk_tcp_extends_flange_along_its_z():
    """The TCP sits a fixed hand offset along the flange z-axis; at home
    the flange points mostly down, so the TCP is BELOW the flange."""
    flange_pos, rotation = fk_flange(HOME)
    tcp = fk_tcp(HOME)
    offset = tcp - flange_pos
    assert np.linalg.norm(offset) == pytest.approx(0.1034, abs=1e-6)
    assert tcp[2] < flange_pos[2]  # home flange faces down


@pytest.mark.parametrize(
    "target",
    [
        (0.45, -0.10, 0.15),
        (0.50, 0.10, 0.20),
        (0.35, -0.35, 0.25),  # over the tray
    ],
)
def test_ik_reaches_topdown_targets(target):
    """CAP-5 ik-trajectory: DLS-IK converges on reachable top-down targets —
    TCP within 5 mm, flange z-axis within 5 degrees of straight down, and
    the solution respects joint position limits (CON-5: deterministic, no
    randomness)."""
    q = ik_solve(np.asarray(target, dtype=np.float32), topdown_rotation(0.3), HOME)
    assert q is not None
    tcp = fk_tcp(q)
    assert np.linalg.norm(tcp - np.asarray(target)) < 0.005
    _, rotation = fk_flange(q)
    assert rotation[2, 2] == pytest.approx(-1.0, abs=0.004)  # cos(5deg)
    assert (q >= np.asarray(LIMITS.q_min[:7]) - 1e-6).all()
    assert (q <= np.asarray(LIMITS.q_max[:7]) + 1e-6).all()


def test_ik_is_deterministic():
    """CON-5: same target, same seed pose => bit-identical solution."""
    a = ik_solve(np.array([0.5, 0.0, 0.2], np.float32), topdown_rotation(0.0), HOME)
    b = ik_solve(np.array([0.5, 0.0, 0.2], np.float32), topdown_rotation(0.0), HOME)
    assert np.array_equal(a, b)


def test_unreachable_target_returns_none():
    """A target far outside the reach envelope reports failure instead of
    a silently-wrong pose."""
    assert ik_solve(np.array([2.0, 0.0, 0.2], np.float32), topdown_rotation(0.0), HOME) is None


def test_interpolate_step_respects_velocity_bound():
    """CAP-5 param max_joint_vel_rad_s: each 100 Hz step moves every joint
    at most vel*dt toward the stage target and lands exactly on it."""
    current = np.zeros(7, dtype=np.float32)
    target = np.array([0.5, -0.5, 0.05, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    step = interpolate_step(current, target, max_vel=1.0, dt=0.01)
    assert np.abs(step - current).max() <= 0.01 + 1e-7
    q = current
    for _ in range(200):
        q = interpolate_step(q, target, max_vel=1.0, dt=0.01)
    assert q == pytest.approx(target, abs=1e-6)


def test_staged_plan_topdown_walks_the_pick_place_sequence():
    """The staged plan visits pregrasp -> advance -> close -> lift ->
    retract -> transfer -> lower -> release -> home in order, closing the
    gripper only at CLOSE and opening it at RELEASE; top-down mode
    approaches vertically."""
    grasp = np.array([0.46, -0.1, 0.50, *topdown_quat(0.0)], dtype=np.float32)
    tray_xy = (0.35, -0.35)
    plan = StagedPlan(grasp, tray_xy, approach_m=0.15, q_seed=HOME)
    assert plan.ok, plan.error
    names = [s.name for s in plan.stages]
    assert names == list(STAGES)
    by_name = {s.name: s for s in plan.stages}
    assert by_name["pregrasp"].gripper == 0.0
    assert by_name["close"].gripper == 1.0
    assert by_name["retract"].gripper == 1.0
    assert by_name["release"].gripper == 0.0
    # pregrasp sits approach_m back along the (vertical) approach axis
    offset = fk_tcp(by_name["pregrasp"].q) - fk_tcp(by_name["advance"].q)
    assert offset[2] == pytest.approx(0.15, abs=0.02)
    # lift raises the box slightly before retracting
    assert fk_tcp(by_name["lift"].q)[2] - fk_tcp(by_name["advance"].q)[2] == pytest.approx(
        0.015, abs=0.01
    )
    # transfer carries the box over the tray footprint
    tcp = fk_tcp(by_name["transfer"].q)
    assert tcp[:2] == pytest.approx(tray_xy, abs=0.01)
    # home stage returns to the profile home pose exactly (no IK)
    assert by_name["home"].q == pytest.approx(HOME, abs=1e-6)


def test_staged_plan_front_mode_inserts_horizontally():
    """Lower shelf levels are grasped from the FRONT (ADR-10): the
    pregrasp sits in front of the shelf at the box's height, the advance
    slides horizontally into the inter-board gap."""
    target = np.array([0.55, -0.11, 0.105, 0, 0, 0, 1], dtype=np.float32)
    grasp, approach = plan_grasp(target, (0.055, 0.035, 0.090), front=True, shelf_front_x=0.40)
    plan = StagedPlan(grasp, (0.35, -0.35), approach_m=approach, q_seed=HOME)
    assert plan.ok, plan.error
    by_name = {s.name: s for s in plan.stages}
    pre, adv = fk_tcp(by_name["pregrasp"].q), fk_tcp(by_name["advance"].q)
    assert pre[2] == pytest.approx(adv[2], abs=0.01)  # same height: horizontal
    assert adv[0] - pre[0] == pytest.approx(approach, abs=0.02)  # slides in +x
    # retract carries the lifted box back OUT of the shelf
    retract = fk_tcp(by_name["retract"].q)
    assert retract[0] == pytest.approx(pre[0], abs=0.02)
    assert retract[2] == pytest.approx(adv[2] + 0.015, abs=0.02)


def test_ik_front_orientation_is_not_pi_flipped():
    """Regression (T08 live): the naive rotation-vector error is blind to
    180-degree orientation errors, and front-approach IK from home
    'converged' onto a pi-flipped wrist pointing DOWN instead of into the
    shelf. The solution's approach axis must match the target's."""
    from aisle.nodes.ik_trajectory import quat_to_rotation

    rot = quat_to_rotation(FRONT_QUAT)
    q = ik_solve(np.array([0.34, -0.11, 0.105], np.float32), rot, HOME)
    assert q is not None
    _, solution_rot = fk_flange(q)
    assert solution_rot[:, 2] == pytest.approx([1.0, 0.0, 0.0], abs=0.01)
