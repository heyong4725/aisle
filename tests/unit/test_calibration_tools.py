"""Decision logic of the VER-9 calibration tools (PR #104 review round 4).

These tools are DECISION EVIDENCE for issues #107/#109, so a permissive
predicate here turns a nonexistent operating point into `{"ok": true}`.
The pure halves are tested directly: what counts as delivered, what counts
as a passing camera vote, and what makes a whole sweep successful.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from aisle.scenes.pharmacy import load_meds, load_physics
from aisle.verifier.oracle import build_judge_cfg

pytestmark = pytest.mark.unit

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load("wrist_release_probe")
sweep = _load("identity_dr_sweep")


@pytest.fixture(scope="module")
def cfg():
    meds = load_meds()
    return build_judge_cfg(
        load_physics(),
        meds,
        "franka",
        timeout_s=60.0,
        initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
        robot_home_error_rad=0.0,
    )


def _state(cfg, index: int, pos, quat=(0.0, 0.0, 0.0, 1.0)) -> np.ndarray:
    """An oracle_state vector with one med placed; the rest parked far away."""
    state = np.tile(np.array([9.0, 9.0, 9.0, 0.0, 0.0, 0.0, 1.0]), len(cfg.box_half_extents))
    state[index * 7 : index * 7 + 3] = pos
    state[index * 7 + 3 : index * 7 + 7] = quat
    return state


def _resting_pos(cfg, index: int) -> np.ndarray:
    """Centre of the tray, resting on its floor."""
    return np.array(
        [
            (cfg.tray_min[0] + cfg.tray_max[0]) / 2,
            (cfg.tray_min[1] + cfg.tray_max[1]) / 2,
            cfg.tray_min[2] + cfg.box_half_extents[index][2],
        ]
    )


def test_resting_target_is_delivered(cfg):
    """The positive case the probe exists to find."""
    assert probe.target_delivered(_state(cfg, 0, _resting_pos(cfg, 0)), 0, cfg)


def test_airborne_target_is_not_delivered(cfg):
    """A box still in the gripper, hovering over the tray, is NOT
    delivered. The old predicate had no lower-z bound and accepted a box
    up to 0.27 m above the tray floor — enough to certify a wrist view of
    a delivery that had not happened yet."""
    pos = _resting_pos(cfg, 0) + np.array([0.0, 0.0, 0.20])
    assert not probe.target_delivered(_state(cfg, 0, pos), 0, cfg)


def test_target_outside_the_footprint_is_not_delivered(cfg):
    """Footprint containment is bounded on BOTH sides."""
    pos = _resting_pos(cfg, 0) + np.array([0.40, 0.0, 0.0])
    assert not probe.target_delivered(_state(cfg, 0, pos), 0, cfg)


def test_tipped_target_is_judged_on_its_rotated_footprint(cfg):
    """The oracle's predicate is rotation-aware: a box tipped 90 degrees
    rests on a different face, so a quaternion-blind test would disagree
    with the oracle about the same frame."""
    tipped = (np.sin(np.pi / 4), 0.0, 0.0, np.cos(np.pi / 4))  # 90 deg about x
    upright_z = _resting_pos(cfg, 0)
    assert not probe.target_delivered(_state(cfg, 0, upright_z, tipped), 0, cfg)


def test_vote_needs_the_target_above_threshold(cfg):
    assert probe.vote_passes({"ibuprofen": 0.12}, "ibuprofen", 0.05)
    assert not probe.vote_passes({"ibuprofen": 0.03}, "ibuprofen", 0.05)
    assert not probe.vote_passes({}, "ibuprofen", 0.05)


def test_vote_rejects_a_frame_carrying_a_surviving_wrong_class():
    """VER-9 requires the target detection AND a clear latch. A frame with
    a surviving non-target sets the episode latch, so it cannot be counted
    as a passing wrist candidate however well the target scored."""
    scores = {"ibuprofen": 0.30, "amoxicillin": 0.08}
    assert not probe.vote_passes(scores, "ibuprofen", 0.05)
    # ...but a non-target BELOW threshold does not survive, so it is fine
    assert probe.vote_passes({"ibuprofen": 0.30, "amoxicillin": 0.01}, "ibuprofen", 0.05)


def test_sweep_ok_requires_both_halves_of_the_vote():
    """A miss-only sweep latches nothing and is still not a usable
    calibration — `ok` must not be `not latched` alone."""
    clean = [{"detected": True, "wrong_object": []}, {"detected": True, "wrong_object": []}]
    miss_only = [{"detected": True, "wrong_object": []}, {"detected": False, "wrong_object": []}]
    latch_only = [
        {"detected": True, "wrong_object": []},
        {"detected": True, "wrong_object": ["amoxicillin"]},
    ]

    assert sweep.sweep_ok(clean)
    assert not sweep.sweep_ok(miss_only)
    assert not sweep.sweep_ok(latch_only)
