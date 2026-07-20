"""Unit tests for the T0 expert pipeline's pure cores (CAP-5 manifests:
oracle-pose, grasp-planner-topdown, task-state-machine) — no dora, no sim
(CON-12)."""

import numpy as np
import pytest

from aisle.nodes.grasp_topdown import plan_grasp, topdown_quat, yaw_of
from aisle.nodes.ik_trajectory import quat_to_rotation
from aisle.nodes.oracle_pose import select_pose
from aisle.nodes.task_state_machine import TaskStateMachine
from aisle.scenes.pharmacy import MED_NAMES

pytestmark = pytest.mark.unit


def make_poses(n=5):
    blocks = []
    for i in range(n):
        blocks.extend([0.5, -0.1 + 0.06 * i, 0.10, 0.0, 0.0, 0.0, 1.0])
    return np.asarray(blocks, dtype=np.float32)


class TestOraclePose:
    def test_selects_target_block_by_med_name(self):
        """CAP-5 oracle-pose: target_pose is the 7-float block of the
        requested med, in scene-manifest order (TC-1)."""
        poses = make_poses()
        for i, name in enumerate(MED_NAMES):
            block = select_pose(poses, name)
            assert block.shape == (7,)
            assert block[1] == pytest.approx(-0.1 + 0.06 * i)

    def test_unknown_med_is_refused(self):
        with pytest.raises(ValueError, match="unknown"):
            select_pose(make_poses(), "aspirin")


class TestGraspTopdown:
    def test_topdown_quat_points_flange_down(self):
        """The grasp orientation points the flange z-axis DOWN (top-down
        grasp); yaw rotates about world z only."""
        x, y, z, w = topdown_quat(0.0)
        # 180 deg about x: flange z maps to -z
        assert (x, y, z, w) == pytest.approx((1.0, 0.0, 0.0, 0.0), abs=1e-6)

    def test_grasp_at_top_section_with_yaw(self):
        """CAP-5 grasp-planner-topdown: TCP at the box's TOP section
        (center + half height - grip engagement); with tilt=0 the yaw
        follows the box yaw so the fingers straddle the narrow axis."""
        yaw = 0.5
        quat = (0.0, 0.0, np.sin(yaw / 2), np.cos(yaw / 2))
        target = np.array([0.5, -0.1, 0.10, *quat], dtype=np.float32)
        size = (0.055, 0.035, 0.090)  # y narrower: yaw unchanged
        grasp, approach, place_z = plan_grasp(target, size, grip=0.025, tray_top_z=0.04)
        assert approach == pytest.approx(0.15)
        # release TCP: tray top + hanging box length + drop gap
        assert place_z == pytest.approx(0.04 + (0.090 - 0.025) + 0.01, abs=1e-6)
        assert grasp[:3] == pytest.approx([0.5, -0.1, 0.10 + 0.045 - 0.025], abs=1e-6)
        assert yaw_of(grasp[3:]) % np.pi == pytest.approx(yaw % np.pi, abs=1e-5)

    def test_narrow_x_axis_rotates_grip(self):
        """Fingers travel the gripper y-axis: when the box's x side is the
        narrower one, the grasp yaw turns 90 degrees to straddle it."""
        target = np.array([0.5, -0.1, 0.10, 0, 0, 0, 1], dtype=np.float32)
        grasp, _, _ = plan_grasp(target, (0.030, 0.065, 0.110), tray_top_z=0.04)
        assert yaw_of(grasp[3:]) % np.pi == pytest.approx(np.pi / 2, abs=1e-5)

    def test_front_mode_approaches_horizontally(self):
        """ADR-10: a box under a board is grasped from the shelf FRONT —
        wrist horizontal (approach axis +x), TCP at the box center, and
        the approach distance spans from the front clearance point."""
        target = np.array([0.55, -0.11, 0.11, 0, 0, 0, 1], dtype=np.float32)
        grasp, approach, _ = plan_grasp(
            target, (0.055, 0.035, 0.090), front=True, shelf_front_x=0.40, tray_top_z=0.04
        )
        # z rides up to box_bottom + wrist clearance (0.065 + 0.065 = 0.13),
        # capped at box_top - finger engagement
        assert grasp[:3] == pytest.approx([0.55, -0.11, 0.13], abs=1e-6)
        axis = quat_to_rotation(grasp[3:])[:, 2]
        assert axis == pytest.approx([1.0, 0.0, 0.0], abs=1e-6)  # into the shelf
        assert approach == pytest.approx(0.55 - (0.40 - 0.06), abs=1e-6)


class TestTaskStateMachine:
    def test_goal_emits_target_request_and_feedback_until_result(self):
        """CAP-5 task-state-machine + TC-7: a goal emits a target_request
        naming the med; ticks emit >=1 Hz feedback while active; the result
        ends the episode (feedback stops)."""
        machine = TaskStateMachine()
        out = machine.on_goal({"target_med": "ibuprofen", "timeout_s": 30}, "ep-1")
        assert out == [("target_request", {"target_med": "ibuprofen"}, "ep-1")]
        out = machine.on_tick()
        assert out == [("episode_feedback", {"t": 1, "phase": "executing"}, "ep-1")]
        assert machine.on_result() == []
        assert machine.on_tick() == []  # idle: no feedback

    def test_goal_while_active_is_refused(self):
        """TC-7: actions do not overlap — a second goal while one is active
        is refused (empty emission), the active episode continues."""
        machine = TaskStateMachine()
        machine.on_goal({"target_med": "ibuprofen"}, "ep-1")
        assert machine.on_goal({"target_med": "cetirizine"}, "ep-2") == []
        out = machine.on_tick()
        assert out[0][2] == "ep-1"

    def test_violations_are_counted_into_feedback(self):
        machine = TaskStateMachine()
        machine.on_goal({"target_med": "ibuprofen"}, "ep-1")
        machine.on_violation({"reason": "velocity"})
        machine.on_violation({"reason": "velocity"})
        out = machine.on_tick()
        assert out[0][1]["violations"] == {"velocity": 2}

    def test_feedback_t_is_deterministic_episode_ticks(self):
        """CON-5 (T08 review): feedback t counts 1 Hz ticks since the GOAL
        — no wall clock; a second episode restarts at 1."""
        machine = TaskStateMachine()
        machine.on_goal({"target_med": "ibuprofen"}, "ep-1")
        assert [machine.on_tick()[0][1]["t"] for _ in range(3)] == [1, 2, 3]
        machine.on_result()
        machine.on_goal({"target_med": "metformin"}, "ep-2")
        assert machine.on_tick()[0][1]["t"] == 1
