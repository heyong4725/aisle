"""The run records which engine build produced it (ADR-67, CON-5).

`env_hash` is engine neutral by construction: the frozen set does not contain
`src/aisle/sim`, so two runs on different engines, or on the same engine with
different solver settings, hash identically. The gate therefore asks the
trusted checker for the engine digest as well and carries it into the manifest.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aisle.harness import rollout as rollout_module

pytestmark = pytest.mark.unit

ENGINE_FACTS = {
    "engine": "nexus",
    "sim_engine_hash": "b" * 64,
    "n_files": 3,
    "build": {"version": "0.1.0", "sources": {"nexus": {"commit": "c" * 40}}},
}


@pytest.fixture
def captured_hash_cmd(monkeypatch):
    """Answer the trusted env_hash checker with a canned report, keeping the
    argv it was called with. Every other subprocess call is refused so the
    test cannot silently exercise something else. The engine wheels are
    outside the lock, so the unit tier has none installed (ADR-67)."""
    monkeypatch.setattr("aisle.sim.engine_available", lambda engine: True)
    seen: dict = {}
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd and str(cmd[-1]).endswith("env_hash.py"):
            raise AssertionError(f"unexpected env_hash invocation shape: {cmd}")
        if isinstance(cmd, list) and any(str(part).endswith("env_hash.py") for part in cmd):
            seen["cmd"] = [str(part) for part in cmd]
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps({"ok": True, "env_hash": "a" * 64, "sim": ENGINE_FACTS}),
                stderr="",
            )
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(rollout_module.subprocess, "run", fake_run)
    return seen


def _gates(root: Path, engine: str) -> dict:
    return rollout_module.run_gates(
        root=root,
        graph=root / "graphs" / "expert_t0.yaml",
        branch="feat/test",
        no_idea_gate=True,
        env_baseline="local",
        sim_engine=engine,
    )


def test_gate_asks_the_checker_for_the_engine_digest(captured_hash_cmd):
    """ADR-67: the engine name reaches the trusted checker, so the digest it
    reports is the one for the engine this run will actually launch. Without
    it the checker would report the default engine's digest for a Nexus run,
    which is the recorded-vs-actual divergence the digest exists to close."""
    root = Path(__file__).resolve().parents[2]
    gates = _gates(root, "nexus")
    assert gates["ok"] is True, gates
    cmd = captured_hash_cmd["cmd"]
    assert "--sim-engine" in cmd
    assert cmd[cmd.index("--sim-engine") + 1] == "nexus"


def test_gate_carries_the_engine_build_into_the_manifest_facts(captured_hash_cmd):
    """CON-5/ADR-67: the digest and the out-of-lock engine's build receipt are
    facts of the run, recorded verbatim. The receipt is the only trace of which
    engine sources produced a result: the wheel is not in the lock, so nothing
    else in the manifest can name them."""
    root = Path(__file__).resolve().parents[2]
    gates = _gates(root, "nexus")
    assert gates["sim_engine_build"] == ENGINE_FACTS
    assert gates["sim_engine_build"]["sim_engine_hash"] != gates["env_hash"]


def test_gate_refuses_an_installed_out_of_lock_engine_without_a_receipt(monkeypatch):
    """CON-5/ADR-67: importability does not identify an out-of-lock wheel.
    The trusted checker must stop the run when its only source receipt is
    absent, before validation or launch can turn it into evidence."""
    monkeypatch.setattr("aisle.sim.engine_available", lambda engine: True)
    root = Path(__file__).resolve().parents[2]
    refused = _gates(root, "nexus")
    assert refused["ok"] is False and refused["gate"] == "env_hash"
    assert "build receipt" in refused["detail"]
