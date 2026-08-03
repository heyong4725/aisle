"""Forced-bail firewall test for the s1-driver-v2 registered skill
(PR #54 review P1): a grasp-critical tracking bail must stop the pick
stream THAT tick — no later close/lift/retract/carry command may be
emitted. Drives the REAL StageStreamer (no fakes) to a pregrasp bail."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from aisle.nodes.ik_trajectory import Stage, StageStreamer

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "s1_driver_v2", REPO_ROOT / "skills" / "s1-driver-v2" / "s1_driver_v2.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
PickStreamGate = _mod.PickStreamGate
GRASP_CRITICAL = _mod.GRASP_CRITICAL


def _pick_stages():
    q = lambda v: np.full(7, v, dtype=np.float32)  # noqa: E731
    return [
        Stage("pregrasp", (q(0.5),), 1.0, 0.05),
        Stage("close", (q(0.5),), 1.0, 0.05, 1.0),
        Stage("lift", (q(0.1),), 1.0, 0.05),
        Stage("carry", (q(0.0),), 0.0, 0.05),
    ]


def test_critical_bail_stops_the_stream_that_tick():
    """The gate suppresses the bailed tick's own command and every tick
    after it; the underlying streamer never starts close/lift/carry."""
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
    assert gate.done is True  # the driver aborts on this, not on stream end
    # the bailed tick and everything after it emit NOTHING
    assert all(cmd is None and grip is None for cmd, grip in emitted_after_bail)
    # the streamer never marched into the grasp/carry stages
    later_logs = []
    for _ in range(50):
        _, _, logs = gate.step(stuck_qpos)
        later_logs += logs
    assert later_logs == []
    assert gate.streamer.stages[gate.streamer.stage_idx].name == "close"  # parked, never run


def test_noncritical_streams_pass_through():
    """A clean stream is untouched by the gate: same outputs, done only
    at the real end."""
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


def test_grasp_critical_set_matches_reviewed_stages():
    assert GRASP_CRITICAL == {"pregrasp", "advance", "close"}


def test_manifests_classify_command_producer_as_motion():
    """CAP-6 (PR #75 review follow-up): s1-driver-v2 emits joint_cmd and
    gripper_cmd, so BOTH its manifests must declare safety_class motion —
    decision-class would let discovery/hot-swap policy treat executable
    motion code as decision logic."""
    import yaml

    for path in (
        REPO_ROOT / "skills" / "s1-driver-v2" / "skill.yaml",
        REPO_ROOT / "registry" / "manifests" / "s1-driver-v2.yaml",
    ):
        manifest = yaml.safe_load(path.read_text())
        assert set(manifest["outputs"]) >= {"joint_cmd", "gripper_cmd"}
        assert manifest["safety_class"] == "motion", path
