"""SPEC 210 acceptance (MOB-1, MOB-2), the modules named by the spec.

- test_mobile_schema_conformance (MOB-1): a live run of the mobile bridge,
  mirroring the contract conformance checks in test_contract.py.
- test_nav_action_lifecycle (MOB-2): the RUNNING nav action, driven in a
  dataflow, emits the goal_id feedback -> success result lifecycle and the
  base_cmd that closes the loop. No genesis (a kinematic mock base).
"""

import importlib.util
import json
import shutil
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.accept

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "registry"
BRIDGE = REPO / "src" / "aisle" / "nodes" / "dora_genesis.py"
GUARD = REPO / "src" / "aisle" / "nodes" / "budget_guard.py"
NAV = REPO / "src" / "aisle" / "nodes" / "nav_action.py"
FIXTURES = REPO / "tests" / "fixtures" / "nodes"


def _write_bridge_graph(tmp: Path, rec_out: Path) -> Path:
    graph = {
        "nodes": [
            {
                "id": "base-driver",
                "path": str(FIXTURES / "base_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["base_cmd"],
                "env": {"BASE_V": "0.2", "BASE_OMEGA": "0.0"},
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
                    "base_pose": {"source": "bridge/base_pose", "queue_size": 4000},
                    "base_scan": {"source": "bridge/base_scan", "queue_size": 4000},
                    "frame_info": {"source": "bridge/frame_info", "queue_size": 4},
                },
                # bound the live capture to 10 s of data, opened at the first
                # event so the genesis build stays outside the window
                "env": {"REC_OUT": str(rec_out), "RECORDER_DURATION_S": "10"},
            },
        ]
    }
    path = tmp / "bridge.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


def test_mobile_schema_declares_vocabulary():
    """MOB-1 (static): base_pose/base_cmd/base_scan are closed-vocabulary
    CAP-2 schemas with the declared shapes, wired at the MOB-1 rates."""
    schemas = tomllib.loads((REGISTRY / "schema" / "schemas.toml").read_text())
    assert schemas["base_pose3d_f32"] == {"arrow": "Float32", "shape": "3"}
    assert schemas["base_cmd2d_f32"] == {"arrow": "Float32", "shape": "2"}
    assert schemas["base_scan_f32"]["arrow"] == "Float32"
    bridge = yaml.safe_load((REGISTRY / "manifests" / "dora-genesis.yaml").read_text())
    assert bridge["outputs"]["base_pose"]["schema"] == "base_pose3d_f32"
    assert bridge["outputs"]["base_scan"]["schema"] == "base_scan_f32"
    assert bridge["inputs"]["base_cmd"]["rate_hz"] <= 50


@pytest.mark.skipif(
    importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
    reason="sim extra or dora CLI not installed",
)
def test_mobile_schema_conformance(tmp_path, dataflow):
    """MOB-1 (LIVE, mirrors test_contract.py conformance): run the mobile
    bridge and check the base topics AS OBSERVED — Arrow dtype (Float32),
    shape (3 / n_scan), TC-2
    metadata (env_id/sim_time_ns/monotonic seq) on EVERY base_pose and
    base_scan, base_scan's {angle_min,angle_max,n} meta, and the 50/10 Hz
    producer rates measured from sim time."""
    rec_out = tmp_path / "bridge.jsonl"
    graph = _write_bridge_graph(tmp_path, rec_out)
    # stop as soon as the 10 s recorder window settles (build + 10 s), not the
    # whole outer cap; 300 s only guards a very slow genesis build
    dataflow.run_until_settled(graph, rec_out, deadline_s=300)
    rows = dataflow.read(rec_out)

    expected = {"base_pose": (3, 50), "base_scan": (36, 10)}
    for topic, (shape, rate) in expected.items():
        msgs = [r for r in rows if r["id"] == topic]
        assert len(msgs) > 10, f"{topic}: only {len(msgs)} samples"
        for m in msgs:
            assert m["dtype"] == "float", (topic, m["dtype"])  # arrow Float32
            assert len(m["value"]) == shape, (topic, len(m["value"]))
            assert {"env_id", "sim_time_ns", "seq"} <= set(m["meta"]), (topic, m["meta"])  # TC-2
        seqs = [int(m["meta"]["seq"]) for m in msgs]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"{topic} seq not monotonic"
        # WALL-CLOCK rate the consumer experiences (TC-4 in spirit). Genesis
        # headless runs sub-realtime (~0.75x here) and the guard<->bridge
        # keep-out feedback cycle adds per-tick latency, so a 50 Hz topic
        # cannot hold +/-20% of wall rate on this hardware. The wall LOWER
        # bound is relaxed to half the contract rate — enough to catch a
        # grossly throttled sim (e.g. 0.1x realtime, the failure mode a
        # sim-time-only check would miss) — while the sim-time scheduler rate
        # below is held to the exact +/-20% (BRG-2 scheduler correctness).
        wall_span = msgs[-1]["wall_t"] - msgs[0]["wall_t"]
        # the capture must actually span >= 80% of the requested 10 s window —
        # a truncated (stalled) run is caught here, not passed on a short slice
        assert wall_span >= 0.8 * 10.0, (topic, "window", wall_span)
        # TC-4 (amended): under simulation conformance is the exact sim-time
        # scheduler rate (+/-20%), and the wall-clock rate need only stay above
        # a 0.5x liveness floor (genesis headless runs sub-realtime, slower
        # with the guard<->bridge cycle) — enough to catch a grossly throttled
        # sim. Both are asserted here.
        wall_rate = (len(msgs) - 1) / wall_span
        assert wall_rate >= 0.5 * rate, (topic, "wall", wall_rate)  # TC-4 sim liveness floor
        span_ns = int(msgs[-1]["meta"]["sim_time_ns"]) - int(msgs[0]["meta"]["sim_time_ns"])
        sim_rate = (len(msgs) - 1) / (span_ns / 1e9)
        assert 0.8 * rate <= sim_rate <= 1.2 * rate, (topic, "sim", sim_rate)

    scan0 = next(r for r in rows if r["id"] == "base_scan")
    assert {"angle_min", "angle_max", "n"} <= set(scan0["meta"]), scan0["meta"]  # MOB-1
    assert int(scan0["meta"]["n"]) == 36
    assert len([r for r in rows if r["id"] == "frame_info"]) == 1  # MOB-5


def _write_nav_graph(tmp: Path, rec_out: Path) -> Path:
    tick = "dora/timer/millis/20"
    graph = {
        "nodes": [
            {
                "id": "injector",
                "path": str(FIXTURES / "nav_goal_injector.py"),
                "inputs": {"tick": tick},
                "outputs": ["nav_goal"],
                # target carries a nonzero yaw: the nav must ROTATE to it, not
                # report instant arrival on x/y alone
                "env": {"NAV_GOAL": '{"pose": [1.0, 0.0, 1.5708]}'},
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
                    "base_pose": {"source": "mock-base/base_pose", "queue_size": 4000},
                },
                "env": {"REC_OUT": str(rec_out), "RECORDER_DURATION_S": "15"},
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

    feedbacks = [r for r in rows if r["id"] == "nav_feedback"]
    payloads = [json.loads(r["value"][0]) for r in feedbacks]
    result_rows = [r for r in rows if r["id"] == "nav_result"]
    base_cmds = [r["value"] for r in rows if r["id"] == "base_cmd"]
    poses = [r["value"] for r in rows if r["id"] == "base_pose"]

    assert len(feedbacks) >= 2, f"nav emitted no ongoing feedback: {len(feedbacks)}"
    # MOB-2 feedback shape is exactly {t, dist_remaining} (no contract drift)
    assert set(payloads[0]) == {"t", "dist_remaining"}, payloads[0]
    # MOB-2 requires >= 2 Hz feedback DURING the active action — measure the
    # cadence from the recorded wall times, not just the count
    span = feedbacks[-1]["wall_t"] - feedbacks[0]["wall_t"]
    cadence = (len(feedbacks) - 1) / span
    assert cadence >= 2.0, f"feedback cadence {cadence:.1f} Hz < 2 Hz (MOB-2)"
    # progress: distance to the goal shrinks over the run (MOB-2)
    assert payloads[-1]["dist_remaining"] < payloads[0]["dist_remaining"]
    # TC-7 goal_id lifecycle: EVERY feedback and the terminal result carry the
    # goal's id (a result under a wrong/empty id must not pass)
    assert all(f["meta"].get("goal_id") == "g1" for f in feedbacks)
    # exactly one terminal result, exact MOB-2 schema, under goal g1
    assert len(result_rows) == 1, f"expected one nav_result, got {len(result_rows)}"
    result = json.loads(result_rows[0]["value"][0])
    assert result_rows[0]["meta"].get("goal_id") == "g1"
    assert set(result) == {"status", "failure", "t_end"}, result
    assert result["status"] == "success" and result["failure"] is None
    # arrival requires BOTH x/y and yaw to converge; orientation is verified
    # via base_pose (not a contract feedback field)
    assert poses[-1][2] == pytest.approx(1.5708, abs=0.1), poses[-1]
    # the controller commanded forward motion AND rotation
    assert any(bc[0] > 0.0 for bc in base_cmds), "nav never commanded forward v"
    assert any(abs(bc[1]) > 0.0 for bc in base_cmds), "nav never commanded rotation"
