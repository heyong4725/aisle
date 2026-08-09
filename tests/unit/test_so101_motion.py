"""SO-101 motion-stack tests for issue #13 / SPEC 090 M0-5.

The production kinematics must come from the owner-approved official URDF,
not a second hand-maintained table.  These tests stay pure-numpy/stdlib so
they remain in the fast unit gate.
"""

import numpy as np
import pytest

from aisle.embodiment import SO101_ARM_JOINTS, SO101_JOINTS
from aisle.kinematics import so101_chain
from aisle.nodes.budget_guard import (
    clamp_joint_cmd,
    fingers_to_gripper,
    fk_ee_pose,
    gripper_to_fingers,
    load_limits,
)
from aisle.nodes.dora_genesis import CarryLatch, frame_point_wxyz
from aisle.nodes.grasp_topdown import place_tcp_z, plan_grasp
from aisle.nodes.ik_trajectory import (
    StagedPlan,
    fk_tcp,
    ik_solve,
    rotation_to_quat,
    so101_radial_rotation,
)
from aisle.scenes.pharmacy import load_meds, load_physics, scaled_meds

pytestmark = pytest.mark.unit

OFFICIAL_LOWER = (-1.91986, -1.74533, -1.69, -1.65806, -2.74385, -0.174533)
OFFICIAL_UPPER = (1.91986, 1.74533, 1.69, 1.65806, 2.84121, 1.74533)


def test_so101_latch_distance_uses_official_fixed_tcp_frame():
    """M0-5, SCN-4: nearest-object carry distance is measured at the
    official fixed gripper-frame TCP, not Genesis's surviving parent link."""
    link_pos = np.array([0.30, 0.10, 0.20])
    quarter_turn_z = np.array([2**-0.5, 0.0, 0.0, 2**-0.5])
    offset = np.array([-0.10, 0.0, 0.0])
    assert frame_point_wxyz(link_pos, quarter_turn_z, offset) == pytest.approx(
        [0.30, 0.00, 0.20], abs=1e-6
    )


def test_so101_carry_latch_is_geometric_deterministic_and_releases():
    """M0-5, SCN-4, CON-5: the simulator carry latch captures only the
    nearest in-envelope object after close, carries its position in the
    hand frame while keeping the carton upright, and releases on open."""
    latch = CarryLatch(close_threshold=0.70, release_threshold=0.20, max_distance_m=0.15)
    identity = np.array([1.0, 0.0, 0.0, 0.0])
    candidates = {
        "near": (np.array([0.10, 0.00, 0.05]), identity),
        "far": (np.array([0.20, 0.00, 0.00]), identity),
    }
    assert latch.update(0.60, np.zeros(3), identity, candidates) is None
    attached = latch.update(0.75, np.zeros(3), identity, candidates)
    assert attached is not None and attached[0] == "near"
    quarter_turn_x = np.array([2**-0.5, 2**-0.5, 0.0, 0.0])
    moved = latch.update(0.75, np.array([0.0, 0.1, 0.2]), quarter_turn_x, candidates)
    assert moved[1] == pytest.approx([0.10, 0.10, 0.25], abs=1e-6)
    # A side-grasp hand may pitch during five-axis transfer; gravity keeps
    # the carried carton upright, as in the accepted ADR-18 carry model.
    assert moved[2] == pytest.approx(identity)
    assert latch.update(0.10, np.zeros(3), identity, candidates) is None
    assert latch.held_name is None


def test_so101_chain_and_guard_limits_come_from_official_urdf():
    """BG-2, TC-5, SCN-4, M0-5: the motion and safety stacks use the
    official five-arm-plus-gripper names, position limits, and 10 rad/s
    velocity parameters from the pinned follower URDF."""
    chain = so101_chain()
    limits = load_limits("so101")
    assert chain.joint_names == SO101_ARM_JOINTS
    assert chain.q_min == OFFICIAL_LOWER[:5]
    assert chain.q_max == OFFICIAL_UPPER[:5]
    assert chain.qdot_max == (10.0,) * 5
    assert limits.n_arm_dof == 5
    assert limits.q_min == OFFICIAL_LOWER
    assert limits.q_max == OFFICIAL_UPPER
    assert limits.qdot_max == (10.0,) * 6
    assert limits.fallback_qpos == (0.0, 0.0, 0.0, 0.0, 0.0, OFFICIAL_UPPER[-1])


def test_so101_normalized_gripper_maps_both_official_endpoints():
    """BG-1, BG-2, TC-5: normalized gripper semantics stay 0=open and
    1=closed while the physical one-axis command spans the official
    revolute upper/lower limits exactly."""
    limits = load_limits("so101")
    assert gripper_to_fingers(0.0, limits) == pytest.approx([OFFICIAL_UPPER[-1]])
    assert gripper_to_fingers(1.0, limits) == pytest.approx([OFFICIAL_LOWER[-1]])
    for value in (0.0, 0.25, 1.0):
        command = np.concatenate(
            [np.asarray(limits.fallback_qpos[:5]), gripper_to_fingers(value, limits)]
        )
        assert fingers_to_gripper(command, limits) == pytest.approx(value, abs=1e-6)


def test_so101_uses_official_simulator_gripper_gains():
    """M0-5, SCN-2, SCN-4: Genesis uses the STS3215 position-control
    parameters published with the pinned official new-calibration MJCF."""
    profile = load_physics()["embodiment"]["so101"]
    assert profile["gripper_kp"] == [998.22]
    assert profile["gripper_kv"] == [2.731]
    # Full-campaign forearm evidence, not the provisional compact-gripper
    # assumption, determines the collision-free planogram corridor.
    assert profile["center_separation_m"] >= 0.18
    assert profile["shelf_level_size"][1] >= 0.50
    assert profile["placement_radius_m"] == pytest.approx(0.44)
    assert profile["carry_latch_close"] == pytest.approx(0.68)
    assert profile["front_vertical_offset_m"] >= 0.12
    assert profile["trajectory_transfer_z"] > profile["front_vertical_offset_m"] + 0.10


def test_so101_fk_and_guard_are_deterministic_and_contained():
    """BG-2, BG-3, CON-5, M0-5: official-URDF FK is deterministic, the
    home TCP is inside the configured workspace, and a legal six-joint
    command is clamped without changing its dimensional contract."""
    limits = load_limits("so101")
    home = np.asarray(limits.fallback_qpos, dtype=np.float32)
    pos_a, rot_a = fk_ee_pose(home[:5], embodiment="so101")
    pos_b, rot_b = fk_ee_pose(home[:5], embodiment="so101")
    assert np.array_equal(pos_a, pos_b)
    assert np.array_equal(rot_a, rot_b)
    assert all(limits.workspace_min[i] <= pos_a[i] <= limits.workspace_max[i] for i in range(3))
    cmd = home.copy()
    cmd[0] += 0.01
    safe, violations = clamp_joint_cmd(cmd, home, limits, timed_out=False)
    assert safe.shape == (len(SO101_JOINTS),)
    assert violations == []


@pytest.mark.parametrize(
    "target",
    [
        (0.369, 0.00, 0.115),  # compact shelf open band
        (0.16, -0.35, 0.18),  # SO-101 tray transfer envelope
    ],
)
def test_so101_ik_reaches_task_envelope_with_free_yaw(target):
    """M0-5, TC-5, SCN-4, CON-5: deterministic five-axis IK reaches the
    pharmacy shelf and tray TCP targets by constraining position and the
    native radial-front pitch/roll while leaving base-coupled world yaw
    free."""
    limits = load_limits("so101")
    home = np.asarray(limits.fallback_qpos[:5], dtype=np.float32)
    target = np.asarray(target, dtype=np.float32)
    desired = so101_radial_rotation(target)
    q = ik_solve(target, desired, home, embodiment="so101")
    assert q is not None
    pos, rotation = fk_ee_pose(q, embodiment="so101")
    assert np.linalg.norm(pos - target) < 0.02
    # Native front grasp: tool axis stays horizontal and the non-jaw frame
    # axis stays vertical; azimuth follows the base pan.
    assert abs(float(rotation[2, 2])) < 0.02
    assert float(rotation[2, 1]) > 0.98
    again = ik_solve(target, desired, home, embodiment="so101")
    assert np.array_equal(q, again)


def test_so101_staged_plan_uses_five_arm_joints_and_official_tcp():
    """M0-5, TC-5, SCN-4: the unchanged T0 pick/place stage sequence can
    be instantiated for the official five-axis chain and terminates at
    the profile's five-joint home pose."""
    limits = load_limits("so101")
    profile = load_physics()["embodiment"]["so101"]
    med = scaled_meds(load_meds(), profile["med_scale"])["amoxicillin"]
    tray_top = profile["tray_pos"][2] + profile["tray_size"][2] / 2
    home = np.asarray(limits.fallback_qpos, dtype=np.float32)
    grasp_pos = np.array([0.369, 0.0, 0.115], dtype=np.float32)
    grasp = np.concatenate(
        [grasp_pos, rotation_to_quat(so101_radial_rotation(grasp_pos)).astype(np.float32)]
    )
    plan = StagedPlan(
        grasp,
        tuple(profile["tray_pos"][:2]),
        approach_m=0.06,
        q_seed=home,
        place_z=place_tcp_z(med["size"], 0.0, tray_top),
        embodiment="so101",
    )
    assert plan.ok, plan.error
    assert all(np.asarray(q).shape == (5,) for stage in plan.stages for q in stage.path)
    assert next(stage for stage in plan.stages if stage.name == "close").gripper == pytest.approx(
        profile["gripper_grasp_cmd"]
    )
    assert profile["carry_latch_close"] < profile["gripper_grasp_cmd"]
    assert next(
        stage for stage in plan.stages if stage.name == "preclose"
    ).gripper == pytest.approx(profile["gripper_pregrasp_cmd"])
    assert next(stage for stage in plan.stages if stage.name == "advance").gripper == pytest.approx(
        profile["gripper_pregrasp_cmd"]
    )
    assert next(stage for stage in plan.stages if stage.name == "retract").vel == pytest.approx(
        0.25
    )
    assert next(stage for stage in plan.stages if stage.name == "transfer").vel == pytest.approx(
        0.20
    )
    advance = next(stage for stage in plan.stages if stage.name == "advance")
    lift = next(stage for stage in plan.stages if stage.name == "lift")
    assert fk_tcp(lift.q, embodiment="so101")[2] - fk_tcp(advance.q, embodiment="so101")[2] > 0.04
    assert plan.stages[-1].q == pytest.approx(home[:5])
    assert fk_tcp(advance.q, embodiment="so101").shape == (3,)


def test_so101_staged_plan_covers_seed_zero_offset_grasp():
    """M0-5, TC-5, SCN-4: the official-chain radial transfer route
    carries the actual seed-0 asymmetric front grasp around the five-axis
    straight-chord singularity without changing the T0 graph."""
    limits = load_limits("so101")
    profile = load_physics()["embodiment"]["so101"]
    med = scaled_meds(load_meds(), profile["med_scale"])["amoxicillin"]
    tray_top = profile["tray_pos"][2] + profile["tray_size"][2] / 2
    # Recorded from seed 0 after applying the official-mesh jaw offsets.
    grasp = np.array(
        [
            0.39851335,
            0.00547770,
            0.095,
            0.49655211,
            0.50342429,
            0.50342429,
            0.49655211,
        ],
        dtype=np.float32,
    )
    plan = StagedPlan(
        grasp,
        tuple(profile["tray_pos"][:2]),
        approach_m=0.13852643,
        q_seed=np.asarray(limits.fallback_qpos, dtype=np.float32),
        place_z=place_tcp_z(med["size"], 0.0, tray_top),
        embodiment="so101",
    )
    assert plan.ok, plan.error
    assert len(next(stage for stage in plan.stages if stage.name == "transfer").path) >= 2


def test_so101_front_grasp_pose_is_radial_and_clears_shelf_front():
    """M0-5, SCN-4: the SO-101 planner emits its official-chain-feasible
    radial front frame and places the pregrasp beyond the shelf front
    clearance instead of reusing Franka's fixed +x flange pose."""
    pose = np.array([0.369, 0.166, 0.08, 0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    grasp, approach, _ = plan_grasp(
        pose,
        (0.030, 0.020, 0.050),
        front=True,
        shelf_front_x=0.29,
        tray_top_z=0.025,
        radial_front=True,
        front_clearance=0.03,
        front_tcp_overshoot=-0.01,
        front_jaw_center_offset=0.0144,
        front_vertical_offset=0.025,
    )
    from aisle.nodes.ik_trajectory import quat_to_rotation

    rotation = quat_to_rotation(grasp[3:])
    assert grasp[2] == pytest.approx(pose[2] + 0.025)
    radial = pose[:2] / np.linalg.norm(pose[:2])
    tangent = np.array([-radial[1], radial[0]])
    delta = grasp[:2] - pose[:2]
    assert float(np.dot(delta, radial)) == pytest.approx(-0.01, abs=2e-3)
    assert float(np.dot(delta, tangent)) == pytest.approx(0.0144, abs=2e-3)
    # Official moving-jaw travel is frame X: it must be horizontal and
    # tangential, not vertical (the first live smoke otherwise pushed the
    # medicine deeper into the shelf instead of pinching it).
    tcp_radial = grasp[:2] / np.linalg.norm(grasp[:2])
    assert rotation[:2, 2] == pytest.approx(tcp_radial, abs=1e-6)
    assert rotation[:2, 0] == pytest.approx([-tcp_radial[1], tcp_radial[0]], abs=1e-6)
    assert rotation[2, 0] == pytest.approx(0.0, abs=1e-6)
    pregrasp = grasp[:3] - rotation[:, 2] * approach
    assert pregrasp[0] == pytest.approx(0.29 - 0.03, abs=1e-6)


def test_so101_front_grasp_flips_jaw_away_from_nearest_fixed_finger_neighbor():
    """M0-5, SCN-3, SCN-4: radial-front planning may rotate the official
    one-sided jaw 180 degrees about its tool axis so the fixed-finger side
    faces the larger neighbor-free corridor."""
    pose = np.array([0.413, 0.105, 0.078, 0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    radial = pose[:2] / np.linalg.norm(pose[:2])
    tangential = np.array([-radial[1], radial[0]])
    # The neighbor lies on the default fixed-finger (-tangential) side.
    neighbor_xy = pose[:2] - tangential * 0.12
    grasp, _, _ = plan_grasp(
        pose,
        (0.0275, 0.0175, 0.045),
        front=True,
        shelf_front_x=0.29,
        tray_top_z=0.08,
        radial_front=True,
        neighbours=[[neighbor_xy[0], neighbor_xy[1], 0.015, 0.010]],
        front_clearance=0.03,
        front_tcp_overshoot=-0.01,
        front_jaw_center_offset=0.0144,
        front_vertical_offset=0.025,
    )
    from aisle.nodes.ik_trajectory import quat_to_rotation

    rotation = quat_to_rotation(grasp[3:])
    assert float(np.dot(rotation[:2, 0], tangential)) < -0.99
    assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-6)
