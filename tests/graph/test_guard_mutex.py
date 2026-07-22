"""SPEC 210 MOB-3 acceptance: the arm/base mutual exclusion through the
budget guard in a LIVE dataflow. With the arm in motion, a base_cmd above
creep MUST be clamped to creep and a `base_arm_exclusion` violation emitted.
No genesis here — the guard is a pure node, so this graph is fast."""

import shutil
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(shutil.which("dora") is None, reason="dora CLI not installed"),
]

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "src" / "aisle" / "nodes" / "budget_guard.py"
FIXTURES = REPO / "tests" / "fixtures" / "nodes"


def _write_graph(tmp: Path, rec_out: Path) -> Path:
    graph = {
        "nodes": [
            {
                "id": "driver",
                "path": str(FIXTURES / "guard_mutex_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["joint_cmd", "base_cmd"],
            },
            {
                "id": "guard",
                "path": str(GUARD),
                "inputs": {
                    "joint_cmd": {"source": "driver/joint_cmd", "queue_size": 100},
                    "base_cmd": {"source": "driver/base_cmd", "queue_size": 100},
                },
                "outputs": ["base_cmd_safe", "violation", "joint_cmd_safe", "gripper_cmd_safe"],
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {
                    "base_cmd_safe": {"source": "guard/base_cmd_safe", "queue_size": 400},
                    "violation": {"source": "guard/violation", "queue_size": 400},
                },
                "env": {"REC_OUT": str(rec_out)},
            },
        ]
    }
    import yaml

    path = tmp / "guard_mutex.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


def test_arm_base_exclusion(tmp_path, dataflow):
    """MOB-3: while the arm is in motion (the driver oscillates a joint every
    tick), a forward base_cmd of 0.5 m/s (> v_creep) is clamped to creep and
    a base_arm_exclusion violation is published — the mutex holds live."""
    import json

    from aisle.mobility.guard import load_base_limits

    creep = load_base_limits("mobile").v_creep
    rec_out = tmp_path / "guard.jsonl"
    graph = _write_graph(tmp_path, rec_out)
    # pure-python nodes: no genesis build, so a short window suffices
    dataflow.run(graph, timeout_s=45)
    rows = dataflow.read(rec_out)

    safes = [r["value"] for r in rows if r["id"] == "base_cmd_safe"]
    viols = [json.loads(r["value"][0]) for r in rows if r["id"] == "violation"]
    assert len(safes) > 5, f"few base_cmd_safe samples: {len(safes)}"

    # the mutex fired: base_arm_exclusion violations, each clamping to creep
    exclusion_v = [v for v in viols if v["reason"] == "base_arm_exclusion" and v.get("axis") == "v"]
    assert exclusion_v, f"no base_arm_exclusion violation; reasons={[v['reason'] for v in viols]}"
    for v in exclusion_v:
        assert v["clamped"] == pytest.approx(creep, abs=1e-6)

    # and the emitted safe command respects creep once the mutex engages
    assert any(abs(s[0]) <= creep + 1e-6 for s in safes), "base was never clamped to creep"
