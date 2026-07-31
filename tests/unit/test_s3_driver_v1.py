"""Unit tests for the s3-driver-v1 registered skill (PR #75 review;
CAP-6, RS-4, design doc §3 rule 3). Pure planning/geometry — no sim.

Covers the review's three code findings: the grasp-critical bail
firewall (P1: no close/lift command may escape after a bail), the
five-criterion RS-4 self-check (P1: XY+yaw-only false-passed vertical,
overhang, and alignment errors), and the evalcard's honesty (P1: the
eval seeds must be EXACTLY the generator-derived both-L1 feasible
class over the S3 dev range, never a handpicked subset)."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from aisle.nodes.ik_trajectory import Stage, StageStreamer
from aisle.scenes.store import generate_episode, load_planogram, slot_world_pose
from aisle.verifier.retail import build_retail_cfg

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "s3_driver_v1", REPO_ROOT / "skills" / "s3-driver-v1" / "s3_driver_v1.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PickStreamGate = _mod.PickStreamGate
GRASP_CRITICAL = _mod.GRASP_CRITICAL
place_check = _mod.place_check
swap_plan = _mod.swap_plan

S3_DEV_SEEDS = range(50)


def _feasible_seeds() -> list[int]:
    """The both-L1 feasible class, derived from the S3 generator plus the
    driver's OWN refusal logic — the single source of truth for what the
    evalcard population must be."""
    return [seed for seed in S3_DEV_SEEDS if swap_plan(generate_episode(seed, "S3"))[1] is None]


# ---------------------------------------------------------------- firewall


def _pick_stages():
    q = lambda v: np.full(7, v, dtype=np.float32)  # noqa: E731
    return [
        Stage("pregrasp", (q(0.5),), 1.0, 0.05),
        Stage("close", (q(0.5),), 1.0, 0.05, 1.0),
        Stage("lift", (q(0.1),), 1.0, 0.05),
        Stage("carry", (q(0.0),), 0.0, 0.05),
    ]


def test_critical_bail_stops_the_stream_that_tick():
    """PR #75 review P1 (mirrors PR #54): a grasp-critical tracking bail
    suppresses the bailed tick's own command and every tick after it —
    the streamer never marches into close/lift/carry with tracking
    already declared unsafe."""
    home = np.zeros(16, dtype=np.float32)
    gate = PickStreamGate(StageStreamer(_pick_stages(), home, 0.01, 1.0, integ_cap=0.30))
    stuck_qpos = np.zeros(16, dtype=np.float32)  # sim never tracks pregrasp

    emitted_after_bail = []
    for _ in range(2000):  # >> STAGE_BAIL_S / dt
        full_cmd, grip_out, logs = gate.step(stuck_qpos)
        if gate.critical_bail:
            emitted_after_bail.append((full_cmd, grip_out))
            if len(emitted_after_bail) > 20:
                break
    assert gate.critical_bail == "pregrasp"
    assert gate.done is True
    assert all(cmd is None and grip is None for cmd, grip in emitted_after_bail)
    later_logs = []
    for _ in range(50):
        _, _, logs = gate.step(stuck_qpos)
        later_logs += logs
    assert later_logs == []
    assert gate.streamer.stages[gate.streamer.stage_idx].name == "close"  # parked, never run


def test_noncritical_streams_pass_through():
    home = np.zeros(16, dtype=np.float32)
    q_target = np.full(7, 0.01, dtype=np.float32)
    stages = [Stage("pregrasp", (q_target,), 0.0, 0.02)]
    gate = PickStreamGate(StageStreamer(stages, home, 0.01, 1.0))
    tracking_qpos = np.concatenate([q_target, np.zeros(9, dtype=np.float32)])
    saw_cmd = False
    for _ in range(2000):
        full_cmd, _, _ = gate.step(tracking_qpos)
        saw_cmd = saw_cmd or full_cmd is not None
        if gate.done:
            break
    assert saw_cmd and gate.critical_bail is None and gate.streamer.done


def test_pick_streams_are_gated_in_the_driver():
    """The event loop must actually WRAP pick streams in the gate and
    abort on critical_bail without waiting for the stream to run dry
    (source-level wiring assertion, mirrors test_holdout_run_tags)."""
    src = (REPO_ROOT / "skills" / "s3-driver-v1" / "s3_driver_v1.py").read_text()
    assert "PickStreamGate(StageStreamer" in src
    assert "streamer.critical_bail" in src


# ---------------------------------------------------------------- RS-4 self-check


@pytest.fixture(scope="module")
def plano():
    return load_planogram()


@pytest.fixture(scope="module")
def cfg(plano):
    return build_retail_cfg(plano, generate_episode(33, "S3"))


def _l1_slot(plano) -> str:
    return next(s for s in sorted(plano["slots"]) if _mod.slot_level(s) == 1)


HALF = (0.02, 0.02, 0.05)


def _perfect(plano, slot_id):
    world, slot_yaw = slot_world_pose(plano, slot_id)
    return [world[0], world[1], world[2] + HALF[2]], slot_yaw


def test_place_check_passes_a_perfect_placement(plano, cfg):
    slot_id = _l1_slot(plano)
    pos, yaw = _perfect(plano, slot_id)
    assert place_check(pos, yaw, slot_id, plano, HALF, cfg) is None


def test_place_check_fails_each_criterion_alone(plano, cfg):
    """PR #75 review P1: the old XY+yaw self-check returned None for a
    box 10 cm ABOVE its slot (verifier pos: false) and ignored overhang
    and alignment entirely — every RS-4 criterion must be able to fail
    the self-check on its own."""
    slot_id = _l1_slot(plano)
    pos, yaw = _perfect(plano, slot_id)

    # pos — the review's concrete counterexample: 10 cm vertical error
    high = [pos[0], pos[1], pos[2] + 0.10]
    assert place_check(high, yaw, slot_id, plano, HALF, cfg) == "pos"

    # yaw — beyond the tightened band, still front-facing
    assert place_check(pos, yaw + 0.2, slot_id, plano, HALF, cfg) == "yaw"

    # front_face — axis-aligned but facing backwards
    assert place_check(pos, yaw + np.pi, slot_id, plano, HALF, cfg) == "front_face"

    # overhang — a wide box whose front edge crosses the shelf edge while
    # its center sits exactly on the template (alignment stays clean
    # because template_front uses the same half extent)
    wide = (0.30, 0.02, 0.05)
    assert place_check(pos, yaw, slot_id, plano, wide, cfg) == "overhang"

    # alignment — front edge short of the template front (shift INTO the
    # shelf so overhang cannot fire), isolated via its own band
    world, slot_yaw = slot_world_pose(plano, slot_id)
    unit = plano["units"][slot_id.split("-")[0]]
    import math

    back = [
        pos[0] - 0.010 * math.cos(unit["yaw"]),
        pos[1] - 0.010 * math.sin(unit["yaw"]),
        pos[2],
    ]
    tight = replace(cfg, alignment_tol_m=0.001)
    assert place_check(back, yaw, slot_id, plano, HALF, tight) == "alignment"


def test_place_check_bands_never_exceed_the_verifier(cfg):
    """The 'pass here implies a verifier pass' claim, now structural:
    the self-check runs the verifier's own placement_check with pos/yaw
    tightened below the verifier bands and the other three at exactly
    the verifier bands."""
    import math

    assert _mod.CHECK_POS_M <= cfg.pos_tol_m
    assert math.degrees(_mod.CHECK_YAW_RAD) <= cfg.yaw_tol_deg


# ---------------------------------------------------------------- evalcard honesty


def test_swap_plan_refuses_l0_and_accepts_both_l1():
    """The narrowed capability's precondition, exercised: any L0 slot in
    the swap refuses at plan time (safe no-op); a both-L1 swap yields the
    full 12-task buffered cycle."""
    feasible = _feasible_seeds()
    assert feasible, "the S3 dev range must contain both-L1 swaps"
    tasks, reason = swap_plan(generate_episode(feasible[0], "S3"))
    assert reason is None and len(tasks) == 12
    infeasible = next(s for s in S3_DEV_SEEDS if s not in feasible)
    tasks, reason = swap_plan(generate_episode(infeasible, "S3"))
    assert tasks == [] and "L0" in reason


def test_evalcard_population_is_the_whole_feasible_class():
    """PR #75 review P1: the shipped eval must cover EXACTLY the
    generator-derived both-L1 feasible class over the S3 dev range
    (seeds 0..49) — a handpicked subset published 1.0 for a capability
    that no-ops on 44/50 seeds. The suite and capability identity must
    carry the precondition, and the class is machine-derived here from
    the generator plus the driver's own refusal."""
    import yaml

    eval_cfg = yaml.safe_load((REPO_ROOT / "skills" / "s3-driver-v1" / "eval.yaml").read_text())
    eval_seeds = {int(s) for s in str(eval_cfg["seeds"]).split(",")}
    feasible = set(_feasible_seeds())
    assert eval_seeds == feasible
    assert int(eval_cfg["episodes"]) == len(feasible)
    assert "l1" in str(eval_cfg["suite"]).lower()  # precondition in the suite identity

    manifest = yaml.safe_load((REPO_ROOT / "skills" / "s3-driver-v1" / "skill.yaml").read_text())
    assert manifest["provides"] == ["s3_reshelving_driving_both_l1"]
    assert manifest["safety_class"] == "motion"  # emits joint_cmd/gripper_cmd (CAP-6)
