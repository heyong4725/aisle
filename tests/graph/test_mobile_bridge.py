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
                "inputs": {
                    "base_cmd": {"source": "base-driver/base_cmd", "queue_size": 100},
                    # keep-out pose feedback + dedicated watchdog tick (MOB-3)
                    "base_pose": {"source": "bridge/base_pose", "queue_size": 100},
                    "base_watchdog": "dora/timer/millis/50",
                },
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


def _write_reset_graph(tmp: Path, rec_out: Path) -> Path:
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
                "id": "guard",
                "path": str(GUARD),
                "inputs": {
                    "base_cmd": {"source": "base-driver/base_cmd", "queue_size": 100},
                    "base_pose": {"source": "bridge/base_pose", "queue_size": 100},
                    "base_watchdog": "dora/timer/millis/50",
                },
                "outputs": ["base_cmd_safe", "violation"],
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                # seeded teleport resets while the base is driving: the
                # multi-episode shape of a rollout (driver.py reset mode)
                "id": "resetter",
                "path": str(FIXTURES / "driver.py"),
                # ticks on LIVE base_pose (50 Hz), not a wall timer: a timer
                # fires through the genesis build and both resets would queue
                # before the base ever drives
                "inputs": {"tick": {"source": "bridge/base_pose", "queue_size": 1000}},
                "outputs": ["reset"],
                "env": {
                    "DRIVER_MODE": "reset",
                    "DRIVER_RESET_SEEDS": "2,3",
                    "DRIVER_RESET_SPACING": "250",
                },
            },
            {
                "id": "bridge",
                "path": str(BRIDGE),
                "inputs": {
                    "tick": "dora/timer/millis/10",
                    "base_cmd": {"source": "guard/base_cmd_safe", "queue_size": 100},
                    "reset": {"source": "resetter/reset", "queue_size": 100},
                },
                "outputs": ["base_pose", "reset_done", "frame_info"],
                "env": {"AISLE_EMBODIMENT": "mobile", "AISLE_SEED": "0"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {
                    "base_pose": {"source": "bridge/base_pose", "queue_size": 1000},
                    "reset_done": {"source": "bridge/reset_done", "queue_size": 100},
                },
                "env": {"REC_OUT": str(rec_out), "RECORDER_DURATION_S": "16"},
            },
        ]
    }
    import yaml

    path = tmp / "mobile_reset.yaml"
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


def test_mobile_reset_rehomes_reported_and_physical_base(tmp_path, dataflow):
    """PR #21 regression (MOB-1/ADR-13, TC-6): across MULTIPLE episodes a
    teleport reset re-homes the base to base_start both REPORTED and
    PHYSICALLY. base_pose is published from the robot's physical root
    (get_pos/get_quat readback), so a reset that re-homed the integrator
    without moving the robot would keep reporting the pre-reset pose and
    fail the snap-back assertion here."""
    rec_out = tmp_path / "reset.jsonl"
    graph = _write_reset_graph(tmp_path, rec_out)
    dataflow.run(graph, timeout_s=240)
    rows = dataflow.read(rec_out)
    resets = [r for r in rows if r["id"] == "reset_done"]
    poses = [r for r in rows if r["id"] == "base_pose"]
    assert len(resets) == 2, f"expected 2 resets, saw {len(resets)}"
    for done in resets:
        t = done["wall_t"]
        before = [r["value"] for r in poses if r["wall_t"] < t]
        after = [r["value"] for r in poses if r["wall_t"] > t]
        # the base had driven well away from base_start before the reset...
        assert before and before[-1][0] > 0.5, before[-1:]
        # ...and snaps back to the start right after (min over a short
        # window absorbs cross-topic arrival reordering at the recorder;
        # REPORTED == PHYSICAL by construction of the publish path)
        assert after, "no base_pose after reset_done"
        assert min(v[0] for v in after[:10]) < 0.05, after[:10]
    # after the LAST reset the pose integrates away from the start again:
    # the physical root really moved (an unmoved root plus the
    # change-conditional re-base could not resume driving from x ~ 0)
    tail = [r["value"] for r in poses if r["wall_t"] > resets[-1]["wall_t"]]
    assert tail and tail[-1][0] > min(v[0] for v in tail[:10]) + 0.1
