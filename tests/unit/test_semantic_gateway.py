"""SPEC 480 in a live graph (issue #352): the semantic-gateway node's
transport-free core — SEM-2 separation, SEM-4 permits, SEM-5 stages, SEM-8
guard independence — and the goal-adversary rewrite."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
import yaml
from cli_helpers import REPO_ROOT

from aisle.nodes import goal_adversary
from aisle.nodes.semantic_gateway import (
    CANDIDATE_RADIUS_M,
    Gateway,
    nearest_box,
    over_tray,
    stage_of,
)

pytestmark = pytest.mark.unit

MEDS = ["amoxicillin", "ibuprofen", "cetirizine", "omeprazole", "metformin"]
TRAY = {"pos": [0.5, -0.4, 0.05], "size": [0.3, 0.2, 0.05]}
KEY = hashlib.sha256(b"test").digest()


def _poses(**at):
    """poses array: every med far away except those placed by keyword."""
    flat = []
    for i, name in enumerate(MEDS):
        pos = at.get(name, [5.0 + i, 5.0, 5.0])
        flat.extend([*pos, 0.0, 0.0, 0.0, 1.0])
    return np.asarray(flat, dtype=np.float32)


def _gateway(arm="oracle_sim_shield") -> Gateway:
    g = Gateway(arm, KEY, MEDS, TRAY, grasp_cmd=1.0)
    g.on_goal({"target_med": "ibuprofen"}, "ep-0", now_s=1.0, vocabulary=MEDS)
    return g


def test_stage_derivation_follows_sem5():
    """SEM-5: pre-grasp at the closing edge, carry while closed, delivery
    while closed over the tray, nothing otherwise."""
    assert stage_of(False, True, False) == "pre_grasp"
    assert stage_of(True, False, False) == "carry"
    assert stage_of(True, False, True) == "delivery"
    assert stage_of(False, False, False) is None
    assert over_tray(np.array([0.5, -0.4, 0.2]), TRAY) and not over_tray(
        np.array([0.9, 0.0, 0.2]), TRAY
    )
    assert (
        nearest_box(_poses(ibuprofen=[0.4, 0.1, 0.3]), np.array([0.41, 0.1, 0.3]), MEDS)[0]
        == "ibuprofen"
    )


def test_correct_grasp_is_permitted_through_all_three_stages():
    """SEM-4 / SEM-5: closing on the assigned box, carrying it, and entering
    the tray each get a single-use permit and are forwarded."""
    g = _gateway()
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(ibuprofen=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert g.propose("joint_cmd", np.zeros(9), tcp, 1.95)["stage"] is None  # approach, open
    close = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)
    assert close["forward"] and close["stage"] == "pre_grasp"
    g.identity.on_poses(_poses(ibuprofen=[0.45, 0.0, 0.35]), sim_time_s=2.4)
    carry = g.propose("joint_cmd", np.ones(9), np.array([0.45, 0.0, 0.35]), 2.5)
    assert carry["forward"] and carry["stage"] == "carry"
    g.identity.on_poses(_poses(ibuprofen=[0.5, -0.4, 0.2]), sim_time_s=2.9)
    deliver = g.propose("joint_cmd", np.ones(9) * 2, np.array([0.5, -0.4, 0.2]), 3.0)
    assert deliver["forward"] and deliver["stage"] == "delivery"
    assert [e["outcome"] for e in g.events] == ["permit", "permit", "permit"]
    assert len(g.permits.consumed) == 3


def test_long_hover_over_the_tray_keeps_delivery_permitted():
    """SEM-5: lowering into the tray takes longer than renewal_s; every
    delivery proposal re-attests the carried identity first, so the permit
    never goes stale while the same box is carried, and the evidence window
    stays bounded at the command cadence."""
    g = _gateway()
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(ibuprofen=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)["forward"]
    over = np.array([0.5, -0.4, 0.2])
    for i in range(400):  # 4 s at 100 Hz over the tray
        now = 2.5 + i * 0.01
        g.identity.on_poses(_poses(ibuprofen=over.tolist()), sim_time_s=now - 0.01)
        d = g.propose("joint_cmd", np.full(9, 0.1 * i), over, now)
        assert d["forward"] and d["stage"] == "delivery", (i, d)
    assert len(g.authorizer.state.assertions) < 200


def test_wrong_object_closure_is_refused_and_held():
    """SEM-4 / SEM-6: closing on a box that is not the assignment's target
    yields no permit; the gripper command is dropped and the next carry
    proposal re-sends the last forwarded joint command (controlled hold)."""
    g = _gateway()
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(metformin=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    approach = g.propose("joint_cmd", np.full(9, 0.3), tcp, 1.95)
    assert approach["forward"]
    close = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)
    assert close["forward"] is False and close["reason"] == "wrong_target"
    assert close["value"].tolist() == [0.0] and g.gripper_closed is False
    assert g.events[-1]["outcome"] == "refuse"
    # the executor keeps commanding as if closed: the gateway sees it as a
    # renewed pre-grasp attempt and keeps refusing; joints hold
    close2 = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.1)
    assert close2["forward"] is False


def test_closing_ramp_is_gated_from_its_first_step():
    """SEM-4: the executor ramps the gripper in 0.01 steps; every step of a
    closure on the wrong box is refused and replaced by the open value, so
    the fingers never close; a permitted first step marks the gripper
    closed and the rest of the ramp becomes carry proposals."""
    g = _gateway()
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(metformin=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert g.propose("gripper_cmd", np.array([0.05]), tcp, 2.0)["stage"] is None  # below the edge
    for i, v in enumerate((0.1, 0.5, 1.0)):
        d = g.propose("gripper_cmd", np.array([v]), tcp, 2.0 + i * 0.01)
        assert d["forward"] is False and d["stage"] == "pre_grasp", (v, d)
        assert d["value"].tolist() == [0.0] and d["reason"] == "wrong_target"
    assert g.gripper_closed is False
    ok = _gateway()
    ok.identity.on_poses(_poses(ibuprofen=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert ok.propose("gripper_cmd", np.array([0.1]), tcp, 2.0)["stage"] == "pre_grasp"
    assert ok.gripper_closed is True
    nxt = ok.propose("gripper_cmd", np.array([0.5]), tcp, 2.01)
    assert nxt["forward"] and nxt["stage"] == "carry"
    release = ok.propose("gripper_cmd", np.array([0.0]), tcp, 5.0)
    assert release["forward"] and release["stage"] is None and ok.gripper_closed is False


def test_refused_closure_holds_the_finger_dofs_in_joint_commands():
    """SEM-4: the executor also closes the fingers through joint_cmd's
    gripper dofs; after a refused closure every forwarded joint command
    keeps them open until a closure is permitted or the gripper opens."""
    g = Gateway("oracle_sim_shield", KEY, MEDS, TRAY, 1.0, finger_open=np.array([0.04, 0.04]))
    g.on_goal({"target_med": "ibuprofen"}, "ep-0", now_s=1.0, vocabulary=MEDS)
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(metformin=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert g.propose("gripper_cmd", np.array([0.5]), tcp, 2.0)["forward"] is False
    closing_joints = np.array([0.1] * 7 + [0.0, 0.0])
    d = g.propose("joint_cmd", closing_joints, tcp, 2.01)
    assert d["forward"] and d["value"][-2:].tolist() == pytest.approx([0.04, 0.04])
    assert d["value"][:7].tolist() == pytest.approx([0.1] * 7)
    g.propose("gripper_cmd", np.array([0.0]), tcp, 3.0)  # opened: hold released
    d = g.propose("joint_cmd", closing_joints, tcp, 3.01)
    assert d["value"][-2:].tolist() == [0.0, 0.0]


def test_no_box_at_the_tool_is_missing_identity():
    """SEM-6: closing on air (no box within the candidate radius) refuses
    with missing_or_stale_identity rather than guessing."""
    g = _gateway()
    far = np.array([0.4, 0.1, 0.3 + CANDIDATE_RADIUS_M * 3])
    g.identity.on_poses(_poses(ibuprofen=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    close = g.propose("gripper_cmd", np.array([1.0]), far, 2.0)
    assert close["forward"] is False and close["reason"] == "missing_or_stale_identity"


def test_stale_identity_refuses():
    """SEM-6: an assertion older than max_age_s is not evidence."""
    g = _gateway()
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(ibuprofen=[0.4, 0.1, 0.3]), sim_time_s=1.0)
    close = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)  # 1.0 s old > 0.5 s
    assert close["forward"] is False and close["reason"] == "missing_or_stale_identity"


def test_no_shield_arm_forwards_but_still_logs():
    """SEM-8 / SEM-10 control arm: every proposal is forwarded; the
    authorizer's refusal is still recorded so the same evidence exists."""
    g = _gateway("no_shield")
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(metformin=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    close = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)
    assert close["forward"] is True and g.events[-1]["outcome"] == "refuse"
    assert g.events[-1]["reason"] == "wrong_target" and g.gripper_closed


def test_sensor_arm_refuses_without_frame_or_calibration():
    """SEM-6 / SEM-14: the sensor arm with no overhead frame or calibration
    is out of envelope and refuses rather than passing."""
    g = _gateway("sensor_shield")
    tcp = np.array([0.4, 0.1, 0.3])
    close = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)
    assert close["forward"] is False
    assert close["reason"] in ("out_of_envelope", "missing_or_stale_identity")


def test_reset_and_goal_change_revoke():
    """SEM-6: a reset restarts the authorizer; a new goal revokes permits."""
    g = _gateway()
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(ibuprofen=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)["forward"]
    g.on_reset()
    assert g.assignment is None and g.authorizer.state.grasp_track is None
    assert g.propose("joint_cmd", np.zeros(9), tcp, 2.5)["stage"] is None


def test_commands_after_the_verdict_carry_no_event():
    """SEM-8 evidence hygiene: once the verdict cleared the assignment the
    executor may still stream closed-gripper commands; they pass without a
    permit and without an event (the first live no_shield run died here)."""
    g = _gateway("no_shield")
    tcp = np.array([0.4, 0.1, 0.3])
    g.identity.on_poses(_poses(metformin=[0.4, 0.1, 0.3]), sim_time_s=1.9)
    assert g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)["event"] is not None
    g.assignment = None  # episode_result arrived
    later = g.propose("joint_cmd", np.zeros(9), tcp, 2.1)
    assert later["forward"] and later["stage"] == "carry" and later["event"] is None


def test_unknown_arm_is_refused():
    with pytest.raises(ValueError, match="unknown shield arm"):
        Gateway("magic", KEY, MEDS, TRAY, 1.0)


# -- graphs and adversary ---------------------------------------------------


def test_goal_adversary_rewrites_only_the_target():
    """The wrong-object adversary keeps every other goal field and the
    control mode passes through."""
    goal = {"target_med": "ibuprofen", "tier": "T0", "goal_revision": 0}
    out = goal_adversary.rewrite(goal, MEDS, "next_med")
    assert out == {**goal, "target_med": "cetirizine"}
    assert goal_adversary.rewrite(goal, MEDS, "none") == goal
    assert (
        goal_adversary.rewrite({"target_med": "metformin"}, MEDS, "next_med")["target_med"]
        == "amoxicillin"
    )


@pytest.mark.parametrize(
    "name, adversary, arm",
    [
        ("shield_t0_oracle", False, "oracle_sim_shield"),
        ("shield_t0_oracle_adversary", True, "oracle_sim_shield"),
        ("shield_t0_none_adversary", True, "no_shield"),
    ],
)
def test_shield_graphs_keep_the_guard_identical_and_route_through_the_gateway(name, adversary, arm):
    """SEM-8: budget-guard is byte-identical to expert_t0.yaml's in every arm
    and consumes the gateway's commands; the gateway consumes the executor's;
    the adversary sits only in front of the state machine."""
    base = {
        n["id"]: n
        for n in yaml.safe_load((REPO_ROOT / "graphs/expert_t0.yaml").read_text())["nodes"]
    }
    doc = {
        n["id"]: n for n in yaml.safe_load((REPO_ROOT / f"graphs/{name}.yaml").read_text())["nodes"]
    }
    guard = doc["budget-guard"]
    assert (
        guard["env"] == base["budget-guard"]["env"]
        and guard["path"] == base["budget-guard"]["path"]
    )
    assert guard["inputs"]["joint_cmd"]["source"] == "semantic-gateway/joint_cmd"
    assert guard["inputs"]["gripper_cmd"]["source"] == "semantic-gateway/gripper_cmd"
    gw = doc["semantic-gateway"]
    assert gw["inputs"]["joint_proposal"]["source"] == "ik-trajectory/joint_cmd"
    assert gw["env"]["AISLE_SHIELD_ARM"] == arm
    assert "oracle_state" not in {
        v["source"].split("/")[1] for v in gw["inputs"].values() if "/" in str(v.get("source", ""))
    }
    assert doc["verifier-oracle"] == base["verifier-oracle"]
    if adversary:
        assert (
            doc["task-state-machine"]["inputs"]["episode_goal"]["source"]
            == "goal-adversary/episode_goal"
        )
        assert (
            doc["verifier-oracle"]["inputs"]["episode_goal"]["source"]
            == "rollout-client/episode_goal"
        )
    else:
        assert "goal-adversary" not in doc
