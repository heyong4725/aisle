"""BRG-1/CON-5 live dora+Genesis lockstep acceptance (issue #175)."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

pytestmark = [
    pytest.mark.graph,
    pytest.mark.accept,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "src/aisle/nodes/dora_genesis.py"
BARRIER = ROOT / "src/aisle/nodes/turn_barrier.py"
POLICY = ROOT / "tests/fixtures/nodes/lockstep_policy.py"
RECORDER = ROOT / "tests/fixtures/nodes/recorder.py"
BRIDGE_OUTPUTS = [
    "bridge_info",
    "joint_state",
    "gripper_state",
    "oracle_state",
    "poses",
    "rgb_overhead",
    "rgb_wrist",
    "depth_overhead",
    "reset_done",
    "sim_turn",
]


def _q(source: str, size: int = 100) -> dict:
    return {"source": source, "queue_size": size, "queue_policy": "backpressure"}


def _write_graph(run_dir: Path, schedule: str) -> tuple[Path, Path]:
    run_dir.mkdir()
    records = run_dir / "records.jsonl"
    plan = {
        "bridge": "bridge",
        "bridge_outputs": sorted(BRIDGE_OUTPUTS),
        "bridge_inputs": {
            "joint_cmd": {"source": "policy", "output": "joint_cmd"},
            "reset": {"source": "policy", "output": "reset"},
        },
        "barrier": "turn-barrier",
        "participants": {
            "policy": {
                "inputs": {},
                "outputs": ["joint_cmd", "reset", "turn_done"],
                "verdict_bearing": False,
            }
        },
        "done_ports": {"done_0": "policy"},
    }
    plan_path = run_dir / "turn-plan.json"
    plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n")
    graph = {
        "nodes": [
            {
                "id": "bridge",
                "path": str(BRIDGE),
                "env": {
                    "AISLE_LOCKSTEP": "1",
                    "AISLE_TURN_EPOCH": "19",
                    "AISLE_TURN_OUTPUTS": ",".join(BRIDGE_OUTPUTS),
                    "AISLE_SEED": "100",
                },
                "inputs": {
                    "tick": "dora/timer/millis/10",
                    "joint_cmd": _q("policy/joint_cmd"),
                    "reset": _q("policy/reset"),
                    "turn_commit": _q("turn-barrier/turn_commit", 4),
                },
                "outputs": BRIDGE_OUTPUTS,
            },
            {
                "id": "policy",
                "path": str(POLICY),
                "env": {"LOCKSTEP_SCHEDULE": schedule},
                "inputs": {"turn": _q("turn-barrier/turn", 4)},
                "outputs": ["joint_cmd", "reset", "turn_done"],
            },
            {
                "id": "turn-barrier",
                "path": str(BARRIER),
                "env": {
                    "AISLE_TURN_PLAN": str(plan_path),
                    "AISLE_TURN_WATCHDOG_S": "2",
                    "AISLE_VERDICT_TURN_WATCHDOG_S": "15",
                },
                "inputs": {
                    "tick": "dora/timer/millis/100",
                    "sim_turn": _q("bridge/sim_turn", 4),
                    "done_0": _q("policy/turn_done", 4),
                },
                "outputs": ["turn", "turn_commit"],
            },
            {
                "id": "recorder",
                "path": str(RECORDER),
                "env": {
                    "RECORDER_OUT": str(records),
                    "RECORDER_DURATION_S": "0.1",
                    "RECORDER_AWAIT": "joint_state:100",
                    "RECORDER_AWAIT_TAIL_S": "0.1",
                },
                "inputs": {
                    "tick": "dora/timer/millis/50",
                    "joint_state": _q("bridge/joint_state"),
                    "sim_turn": _q("bridge/sim_turn"),
                    "reset_done": _q("bridge/reset_done"),
                    "joint_cmd": _q("policy/joint_cmd"),
                    "reset": _q("policy/reset"),
                },
            },
        ]
    }
    graph_path = run_dir / "graph.yaml"
    graph_path.write_text(yaml.safe_dump(graph, sort_keys=False))
    return graph_path, records


def _run(tmp_path: Path, dataflow, schedule: str) -> dict[str, list[dict]]:
    graph, output = _write_graph(tmp_path / schedule, schedule)
    dataflow.run_until_settled(graph, output, deadline_s=600)
    grouped: dict[str, list[dict]] = {}
    for row in dataflow.read(output):
        if row["id"] != "tick":
            grouped.setdefault(row["id"], []).append(row)
    return grouped


def test_two_delay_schedules_have_identical_assignments_and_one_second_physics(tmp_path, dataflow):
    """BRG-1/CON-5: cross-port order changes neither turns nor 1 s physics."""
    data_first = _run(tmp_path, dataflow, "data-first")
    watermark_first = _run(tmp_path, dataflow, "watermark-first")

    for topic in ("reset", "joint_cmd", "reset_done", "sim_turn"):
        left = [
            (row["metadata"].get("turn_id"), row["metadata"].get("sim_time_ns"))
            for row in data_first[topic]
        ]
        right = [
            (row["metadata"].get("turn_id"), row["metadata"].get("sim_time_ns"))
            for row in watermark_first[topic]
        ]
        assert left == right

    left_state = data_first["joint_state"]
    right_state = watermark_first["joint_state"]
    assert len(left_state) == len(right_state) >= 100
    assert int(left_state[-1]["metadata"]["sim_time_ns"]) >= 1_000_000_000
    for left, right in zip(left_state, right_state, strict=True):
        assert left["metadata"]["turn_id"] == right["metadata"]["turn_id"]
        assert left["metadata"]["sim_time_ns"] == right["metadata"]["sim_time_ns"]
        np.testing.assert_allclose(left["values"], right["values"], rtol=0.0, atol=1e-6)
