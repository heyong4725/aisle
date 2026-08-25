"""H6 fault hooks (ADR-h6-operation-protocol): env-triggered
degradations for the Operation campaign. Default (env absent) is
UNCHANGED behavior; an unrecognized value refuses loudly; each fault
degrades success but cannot produce a wrong-medicine delivery."""

import numpy as np
import pytest

from aisle.nodes.h6_fault import (
    GRASP_HIGH_M,
    POSE_BIAS_M,
    TRAJ_SHORT_FRACTION,
    armed_fault,
    bias_pose,
    plan_waypoint_cap,
    raise_grasp,
)
from aisle.nodes.ik_trajectory import Stage, StageStreamer

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------- arming


def test_absent_env_arms_nothing():
    for node in ("segmented-pose", "grasp-planner-topdown", "ik-trajectory"):
        assert armed_fault(node, env={}) is None


def test_blank_env_arms_nothing():
    assert armed_fault("segmented-pose", env={"AISLE_H6_FAULT": "  "}) is None


def test_wrong_node_value_refuses_loudly():
    """`pose_bias` is segmented-pose's fault; arming it on the trajectory
    node is an injector bug that must never run silently unfaulted."""
    with pytest.raises(RuntimeError, match="pose_bias"):
        armed_fault("ik-trajectory", env={"AISLE_H6_FAULT": "pose_bias"})


def test_unknown_value_refuses_loudly():
    with pytest.raises(RuntimeError, match="nonsense"):
        armed_fault("segmented-pose", env={"AISLE_H6_FAULT": "nonsense"})


def test_each_menu_fault_arms_on_its_node():
    assert armed_fault("segmented-pose", env={"AISLE_H6_FAULT": "pose_bias"}) == "pose_bias"
    assert (
        armed_fault("grasp-planner-topdown", env={"AISLE_H6_FAULT": "grasp_high"}) == "grasp_high"
    )
    assert armed_fault("ik-trajectory", env={"AISLE_H6_FAULT": "traj_short"}) == "traj_short"


# ---------------------------------------------------------------- F1 pose_bias


def test_bias_pose_shifts_x_only_and_does_not_mutate():
    estimate = {"pos": [0.5, 0.1, 0.05], "mask_pixels": 900, "top_surface_z_m": 0.08}
    biased = bias_pose(estimate)
    assert biased["pos"] == [0.5 + POSE_BIAS_M, 0.1, 0.05]
    assert biased["mask_pixels"] == 900 and biased["top_surface_z_m"] == 0.08
    assert estimate["pos"] == [0.5, 0.1, 0.05]


def _armed_session(fault=None):
    from aisle.nodes.segmented_pose import L1Session

    session = L1Session(
        meds={"ibuprofen": {"size": [0.05, 0.045, 0.10]}},
        backprojector=lambda calibration: _flat_backproject,
        fault=fault,
    )
    session.on_bridge_info({"calibration": {"n": 1}, "segmentation_ids": {"ibuprofen": [17]}})
    session.on_target_request({"target_med": "ibuprofen"})
    return session


def _flat_backproject(depth, pixels):
    pixels = np.asarray(pixels)
    xy = pixels.astype(np.float64) * 0.001
    z = depth[pixels[:, 1], pixels[:, 0]]
    return np.stack([xy[:, 0], xy[:, 1], z], axis=1)


def _frame(session):
    seg = np.zeros((80, 100), dtype=np.int32)
    seg[10:60, 20:65] = 17
    depth = np.full((80, 100), 0.08, dtype=np.float32)
    return session.on_depth(100, depth) or session.on_seg(100, seg)


def test_l1_session_fault_field_biases_the_target_estimate():
    """The session seam: fault armed -> the published target pos carries the
    +x bias; fault absent (default) -> byte-identical estimate."""
    clean = _armed_session()
    out_clean = _frame(clean)
    out_faulted = _frame(_armed_session(fault="pose_bias"))
    assert out_faulted["pos"][0] == pytest.approx(out_clean["pos"][0] + POSE_BIAS_M)
    assert out_faulted["pos"][1:] == pytest.approx(out_clean["pos"][1:])
    assert clean.fault is None


# ---------------------------------------------------------------- F2 grasp_high


def test_raise_grasp_lifts_z_only():
    pose = np.array([0.5, 0.0, 0.1, 0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    lifted = raise_grasp(pose)
    assert lifted[2] == pytest.approx(0.1 + GRASP_HIGH_M)
    assert lifted[[0, 1, 3, 4, 5, 6]] == pytest.approx(pose[[0, 1, 3, 4, 5, 6]])
    assert pose[2] == pytest.approx(0.1)  # input not mutated
    assert lifted.dtype == np.float32 and lifted.shape == (7,)


# ---------------------------------------------------------------- F3 traj_short


def _two_stage_plan():
    a = np.zeros(7, dtype=np.float32)
    b = np.full(7, 0.2, dtype=np.float32)
    c = np.full(7, 0.4, dtype=np.float32)
    return [
        Stage(name="one", path=(a, b), gripper=0.0, settle_s=0.0),
        Stage(name="two", path=(c,), gripper=0.0, settle_s=0.0),
    ]


def test_plan_waypoint_cap_is_the_fraction_floor_at_least_one():
    assert plan_waypoint_cap(_two_stage_plan()) == max(1, int(3 * TRAJ_SHORT_FRACTION))
    single = [Stage(name="s", path=(np.zeros(7, dtype=np.float32),), gripper=0.0, settle_s=0.0)]
    assert plan_waypoint_cap(single) == 1


def test_streamer_stalls_at_the_waypoint_cap_and_never_finishes():
    """With max_waypoints the executor holds pose after marching the cap:
    same command forever, never done — the episode closes on the budget."""
    stages = _two_stage_plan()
    qpos = np.zeros(9, dtype=np.float32)
    stalled = StageStreamer(stages, np.zeros(7, dtype=np.float32), 0.01, 10.0, max_waypoints=2)
    cmds = []
    for _ in range(600):
        cmd, _grip, _logs = stalled.step(qpos)
        cmds.append(cmd)
    assert not stalled.done
    assert stalled.stage_idx < 2  # never reached the plan's end
    held = cmds[-1]
    assert all(np.array_equal(held, c) for c in cmds[-50:])


def test_streamer_default_is_unchanged_and_completes():
    """max_waypoints=None (the default) pins pre-H6 behavior: the same plan
    runs to done."""
    stages = _two_stage_plan()
    tracked = StageStreamer(stages, np.zeros(7, dtype=np.float32), 0.01, 10.0)
    qpos = np.zeros(9, dtype=np.float32)
    for _ in range(600):
        cmd, _grip, _logs = tracked.step(qpos)
        if cmd is not None:
            qpos = cmd
        if tracked.done:
            break
    assert tracked.done
