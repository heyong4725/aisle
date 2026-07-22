"""SPEC 210 acceptance (MOB-1, MOB-2), the modules named by the spec.

- test_mobile_schema_conformance (MOB-1, mirrors TC-A1): the mobile topics
  declare in-vocabulary CAP-2 schemas with the MOB-1 shapes and rates.
- test_nav_action_lifecycle (MOB-2): the RUNNING nav action, driven in a
  dataflow, emits the goal_id feedback -> success result lifecycle and the
  base_cmd that closes the loop. No genesis (a kinematic mock base).
"""

import json
import shutil
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.accept

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "registry"
NAV = REPO / "src" / "aisle" / "nodes" / "nav_action.py"
FIXTURES = REPO / "tests" / "fixtures" / "nodes"


def test_mobile_schema_conformance():
    """MOB-1: base_pose/base_cmd/base_scan are closed-vocabulary CAP-2
    schemas with the declared shapes, and the bridge + nav manifests wire
    them at the MOB-1 rates (base_cmd <= 50 Hz)."""
    schemas = tomllib.loads((REGISTRY / "schema" / "schemas.toml").read_text())
    assert schemas["base_pose3d_f32"] == {"arrow": "Float32", "shape": "3"}
    assert schemas["base_cmd2d_f32"] == {"arrow": "Float32", "shape": "2"}
    assert schemas["base_scan_f32"]["arrow"] == "Float32"

    bridge = yaml.safe_load((REGISTRY / "manifests" / "dora-genesis.yaml").read_text())
    assert bridge["outputs"]["base_pose"]["schema"] == "base_pose3d_f32"
    assert bridge["outputs"]["base_scan"]["schema"] == "base_scan_f32"
    assert bridge["inputs"]["base_cmd"]["schema"] == "base_cmd2d_f32"
    assert bridge["inputs"]["base_cmd"]["rate_hz"] <= 50  # MOB-1

    nav = yaml.safe_load((REGISTRY / "manifests" / "nav-action.yaml").read_text())
    assert nav["outputs"]["nav_feedback"]["schema"] == "json_utf8"
    assert nav["outputs"]["nav_result"]["schema"] == "json_utf8"
    assert nav["outputs"]["base_cmd"]["schema"] == "base_cmd2d_f32"
    assert nav["inputs"]["base_pose"]["schema"] == "base_pose3d_f32"


def _write_nav_graph(tmp: Path, rec_out: Path) -> Path:
    tick = "dora/timer/millis/20"
    graph = {
        "nodes": [
            {
                "id": "injector",
                "path": str(FIXTURES / "nav_goal_injector.py"),
                "inputs": {"tick": tick},
                "outputs": ["nav_goal"],
                "env": {"NAV_GOAL": '{"pose": [1.0, 0.0, 0.0]}'},
            },
            {
                "id": "nav",
                "path": str(NAV),
                "inputs": {
                    "nav_goal": {"source": "injector/nav_goal", "queue_size": 4},
                    "base_pose": {"source": "mock-base/base_pose", "queue_size": 400},
                    "tick": tick,
                },
                "outputs": ["nav_feedback", "nav_result", "base_cmd"],
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                "id": "mock-base",
                "path": str(FIXTURES / "mock_base.py"),
                "inputs": {
                    "base_cmd": {"source": "nav/base_cmd", "queue_size": 400},
                    "tick": tick,
                },
                "outputs": ["base_pose"],
                "env": {"MOCK_DT": "0.02"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {
                    "nav_feedback": {"source": "nav/nav_feedback", "queue_size": 4000},
                    "nav_result": {"source": "nav/nav_result", "queue_size": 8},
                    "base_cmd": {"source": "nav/base_cmd", "queue_size": 4000},
                },
                "env": {"REC_OUT": str(rec_out)},
            },
        ]
    }
    path = tmp / "nav.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


@pytest.mark.skipif(shutil.which("dora") is None, reason="dora CLI not installed")
def test_nav_action_lifecycle(tmp_path, dataflow):
    """MOB-2: a nav_goal opens the action; the node drives the (mock) base
    toward it and emits >= 2 Hz feedback with shrinking dist_remaining, then
    a success result — all under the same goal_id (TC-7). Pure nodes, so a
    short window suffices."""
    rec_out = tmp_path / "nav.jsonl"
    graph = _write_nav_graph(tmp_path, rec_out)
    dataflow.run(graph, timeout_s=45)
    rows = dataflow.read(rec_out)

    feedbacks = [(json.loads(r["value"][0]), r["meta"]) for r in rows if r["id"] == "nav_feedback"]
    results = [json.loads(r["value"][0]) for r in rows if r["id"] == "nav_result"]
    base_cmds = [r["value"] for r in rows if r["id"] == "base_cmd"]

    assert len(feedbacks) >= 2, f"nav emitted no ongoing feedback: {len(feedbacks)}"
    # progress: distance to the goal shrinks over the run (MOB-2)
    assert feedbacks[-1][0]["dist_remaining"] < feedbacks[0][0]["dist_remaining"]
    # goal_id lifecycle (TC-7): feedback carries the goal's id
    assert feedbacks[0][1].get("goal_id") == "g1"
    # the action terminates in success once the base arrives
    assert any(r["status"] == "success" for r in results), results
    # the controller actually commanded forward motion
    assert any(bc[0] > 0.0 for bc in base_cmds), "nav never commanded forward v"
