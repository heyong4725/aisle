"""so101-driver contract behavior over the loopback bus (Phase 6 prep,
ADR-phase6-prep; CON-12 — no hardware, no lerobot import)."""

import sys

import numpy as np
import pytest

from aisle.embodiment import SO101_JOINTS
from aisle.nodes.so101_driver import (
    MAX_STEP_RAD,
    LoopbackBus,
    clamp_step,
    make_bus,
)

pytestmark = pytest.mark.unit


def test_clamp_step_bounds_every_joint_per_tick():
    current = np.zeros(6, dtype=np.float32)
    wild = np.array([2.0, -2.0, 0.01, 0.0, 3.0, -0.02], dtype=np.float32)
    stepped = clamp_step(current, wild)
    assert float(np.abs(stepped).max()) <= MAX_STEP_RAD + 1e-7
    assert stepped[2] == pytest.approx(0.01)


def test_loopback_bus_tracks_commands_with_lag():
    bus = LoopbackBus()
    assert bus.read_positions().shape == (len(SO101_JOINTS),)
    bus.write_positions(np.full(len(SO101_JOINTS), 0.3, dtype=np.float32))
    first = bus.read_positions()
    assert float(np.abs(first).max()) <= MAX_STEP_RAD + 1e-7  # one tick, one step
    for _ in range(20):
        q = bus.read_positions()
    assert np.allclose(q, 0.3, atol=1e-6)


def test_make_bus_refuses_empty_and_never_imports_lerobot_for_loopback():
    with pytest.raises(ValueError, match="AISLE_SO101_PORT"):
        make_bus("")
    before = set(sys.modules)
    bus = make_bus("loopback")
    assert isinstance(bus, LoopbackBus)
    assert not any(m.startswith("lerobot") for m in set(sys.modules) - before), (
        "loopback must never import lerobot (CI has no vla extra)"
    )


def test_disconnect_is_idempotent_state():
    bus = LoopbackBus()
    bus.disconnect()
    assert not bus.connected


def test_hw_calibration_template_builds_and_self_checks(tmp_path):
    """ADR-phase6-prep: the documented template must survive its own
    stage-0 parity check — a fresh station starts from a self-consistent
    artifact and drift is measured against it, never against sim."""
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    tool = root / "tools" / "hw_calibration.py"
    template = subprocess.run(
        [_sys.executable, str(tool), "--template"], capture_output=True, text=True
    )
    assert template.returncode == 0
    artifact = tmp_path / "calibration.toml"
    artifact.write_text(template.stdout)
    check = subprocess.run(
        [_sys.executable, str(tool), "--check", "--artifact", str(artifact)],
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    import json as _json

    assert _json.loads(check.stdout) == {"ok": True, "problems": []}
