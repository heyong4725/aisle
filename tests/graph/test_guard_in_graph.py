"""SPEC 080 acceptance: adversarial commands through the guard in a LIVE
dataflow (BG-1, BG-2, BG-3, BG-5) — the sim robot never exceeds limits."""

import json

import pytest

from aisle.nodes.budget_guard import load_limits

pytestmark = pytest.mark.graph


def test_adversarial_commands(tmp_path, dataflow):
    """BG-1..3, BG-5: a scripted node emits out-of-range positions, velocity
    jumps, below-floor targets, NaN, and wrong-dof commands; every joint_state
    the sim reports stays within position limits, violations are published
    with the offending reasons, and guard_stats accumulates counts."""
    record_out = tmp_path / "records.jsonl"
    graph = dataflow.write(
        tmp_path,
        record_out,
        bridge_env={"AISLE_SEED": 3},
        driver_env={"DRIVER_MODE": "adversarial"},
        duration_s=12.0,
        with_guard=True,
    )
    run = dataflow.run(graph, timeout_s=420)
    records = dataflow.read(record_out)
    assert records, f"no records captured; stderr tail: {run.stderr[-2000:]}"

    limits = load_limits("franka")
    joint_states = [r for r in records if r["id"] == "joint_state" and "values" in r]
    assert joint_states, "no joint_state captured"
    # BG-1..3: the robot NEVER exceeds its per-joint position limits (small
    # tolerance for PD overshoot around targets clamped exactly to a bound)
    for r in joint_states:
        for i, q in enumerate(r["values"]):
            assert limits.q_min[i] - 0.05 <= q <= limits.q_max[i] + 0.05, (
                f"joint {i} at {q} outside limits in live run"
            )

    violations = [json.loads(r["text"]) for r in records if r["id"] == "violation"]
    reasons = {v["reason"] for v in violations}
    # every hostile pattern the driver cycles must be caught and classified
    assert {"position", "velocity", "workspace", "malformed"} <= reasons, reasons
    for v in violations:
        assert set(v) >= {"reason", "requested", "clamped", "seq"}  # BG-3 payload
        assert "joint" in v or "axis" in v

    # BG-5: cumulative counts every 5 s; a 12 s capture sees at least one
    stats = [json.loads(r["text"]) for r in records if r["id"] == "guard_stats"]
    assert stats, "no guard_stats captured in 12 s"
    assert sum(stats[-1]["violations"].values()) > 0
