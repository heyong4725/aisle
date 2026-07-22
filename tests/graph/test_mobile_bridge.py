"""Graph test for the mobile bridge's kinematic base topics (SPEC 210
MOB-1/MOB-5, ADR-13). Marker `graph`: launches a dora dataflow."""

import importlib.util
import shutil
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

REPO = Path(__file__).resolve().parents[2]
BRIDGE = REPO / "src" / "aisle" / "nodes" / "dora_genesis.py"
GUARD = REPO / "src" / "aisle" / "nodes" / "budget_guard.py"
FIXTURES = REPO / "tests" / "fixtures" / "nodes"


def _write_graph(tmp: Path, rec_out: Path) -> Path:
    graph = {
        "nodes": [
            {
                "id": "base-driver",
                "path": str(FIXTURES / "base_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["base_cmd"],
                "env": {"BASE_V": "0.3", "BASE_OMEGA": "0.0"},
            },
            {
                # base_cmd is a motion sink (MOB-3): it MUST reach the bridge
                # through the guard, so the e2e path mirrors a valid graph.
                "id": "guard",
                "path": str(GUARD),
                "inputs": {"base_cmd": {"source": "base-driver/base_cmd", "queue_size": 100}},
                "outputs": ["base_cmd_safe", "violation"],
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                "id": "bridge",
                "path": str(BRIDGE),
                "inputs": {
                    "tick": "dora/timer/millis/10",
                    "base_cmd": {"source": "guard/base_cmd_safe", "queue_size": 100},
                },
                "outputs": ["base_pose", "base_scan", "frame_info", "bridge_info"],
                "env": {"AISLE_EMBODIMENT": "mobile", "AISLE_SEED": "0"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {
                    "base_pose": {"source": "bridge/base_pose", "queue_size": 400},
                    "base_scan": {"source": "bridge/base_scan", "queue_size": 100},
                    "frame_info": {"source": "bridge/frame_info", "queue_size": 4},
                },
                "env": {"REC_OUT": str(rec_out)},
            },
        ]
    }
    import yaml

    path = tmp / "mobile.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


def test_mobile_bridge_emits_and_integrates_base_topics(tmp_path, dataflow):
    """MOB-1/MOB-5: the mobile bridge emits frame_info once, base_pose that
    integrates a forward base_cmd, and base_scan of the configured length —
    the kinematic base end to end (ADR-13)."""
    rec_out = tmp_path / "base.jsonl"
    graph = _write_graph(tmp_path, rec_out)
    # the bridge never exits: dataflow.run builds + streams for the window,
    # then kills the process group and reaps the node processes (base_driver
    # and base_recorder are in the reaper's NODE_PATTERNS — a bare
    # Popen.terminate leaks them and they spin at ~165% CPU, starving later
    # runs). 180 s absorbs a slow/cold genesis build and still leaves ample
    # base_pose streaming (50 Hz -> thousands of samples per spare second).
    dataflow.run(graph, timeout_s=180)
    rows = dataflow.read(rec_out)
    poses = [r["value"] for r in rows if r["id"] == "base_pose"]
    scans = [r for r in rows if r["id"] == "base_scan"]
    frames = [r for r in rows if r["id"] == "frame_info"]

    assert len(poses) > 20, f"few base_pose samples: {len(poses)}"
    # forward base_cmd -> x integrates upward (MOB-1, ADR-13)
    assert poses[-1][0] > poses[0][0] + 0.1
    assert poses[-1][1] == pytest.approx(0.0, abs=1e-6)  # no lateral drift, omega=0
    # base_scan: configured 36 planar ranges with metadata (MOB-1)
    assert scans and len(scans[0]["value"]) == 36
    assert scans[0]["meta"]["n"] == 36
    # frame_info published exactly once at startup (MOB-5)
    assert len(frames) == 1
    # TC-2: base topics carry the standard metadata (env_id, sim_time_ns,
    # monotonic seq) end to end
    pose_rows = [r for r in rows if r["id"] == "base_pose"]
    for key in ("env_id", "sim_time_ns", "seq"):
        assert key in pose_rows[0]["meta"], (key, pose_rows[0]["meta"])
    seqs = [r["meta"]["seq"] for r in pose_rows]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)  # monotonic, unique
