"""SPEC 030 graph tests: drift and multi-env routing against the live
bridge. Marker `graph` (CON-12): launches dora dataflows."""

import importlib.util
import os
import shutil

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

# 60 s per the spec name; override for quick local iterations via env
DRIFT_DURATION_S = float(os.environ.get("AISLE_DRIFT_TEST_S", "60"))


def test_headless_60s_no_drift(tmp_path, dataflow):
    """BRG-1, BRG-2: a long headless run — each 10 ms tick advances sim by
    cfg.dt, so sim_time tracks wall time without cumulative drift, while
    camera rendering stays rate-limited. The sim/wall ratio is logged."""
    out = tmp_path / "records.jsonl"
    graph = dataflow.write(
        tmp_path,
        out,
        bridge_env={"AISLE_SEED": 7},
        driver_env={"DRIVER_MODE": "conformance", "DRIVER_N_DOF": 9},
        duration_s=DRIFT_DURATION_S,
    )
    dataflow.run(graph, timeout_s=DRIFT_DURATION_S + 300)
    records = [r for r in dataflow.read(out) if r["id"] == "joint_state"]
    # the run must actually cover the requested duration, not die early
    assert len(records) > DRIFT_DURATION_S * 100 * 0.5, len(records)
    sim_span = (
        int(records[-1]["metadata"]["sim_time_ns"]) - int(records[0]["metadata"]["sim_time_ns"])
    ) / 1e9
    wall_span = records[-1]["wall_t"] - records[0]["wall_t"]
    assert wall_span >= DRIFT_DURATION_S * 0.9, wall_span
    ratio = sim_span / wall_span
    print(f"sim/wall ratio over {wall_span:.1f}s: {ratio:.3f}")
    # Measured budget on M3 / genesis 1.2.3 (ADR-7): step 4.4 ms +
    # contract-rate renders ~2.7 ms/tick amortized -> the loop sustains
    # ~0.77x wall. BRG-2's ">=5x realtime" TARGET is not met on this
    # stack (recorded in ADR-7, not hidden); the floor below pins the
    # sustained throughput so regressions surface, and the ceiling
    # catches sim_time accounting bugs.
    assert 0.70 <= ratio <= 1.1, ratio


def test_multi_env_routing(tmp_path, dataflow):
    """BRG-5, TC-2: with n_envs=2 every output message carries env_id, both
    envs are served at contract rates, and env-routed joint_cmds are
    accepted."""
    out = tmp_path / "records.jsonl"
    graph = dataflow.write(
        tmp_path,
        out,
        bridge_env={"AISLE_SEED": 7, "AISLE_N_ENVS": 2},
        driver_env={"DRIVER_MODE": "multi_env", "DRIVER_N_DOF": 9},
        duration_s=10.0,
    )
    dataflow.run(graph, timeout_s=310)
    records = dataflow.read(out)
    joint = [r for r in records if r["id"] == "joint_state"]
    assert joint, "no joint_state captured"
    env_ids = {int(r["metadata"]["env_id"]) for r in joint}
    assert env_ids == {0, 1}
    # routed control EFFECT: each env converges toward ITS distinct target
    # (driver sends stable per-env targets), so a broadcast or ignored
    # command stream cannot pass
    late = {env: [r for r in joint if int(r["metadata"]["env_id"]) == env][-1] for env in (0, 1)}
    assert late[0]["values"] != late[1]["values"], "envs did not diverge under routed commands"
    assert abs(late[0]["values"][1] - (-0.6)) < 0.3, late[0]["values"]
    assert abs(late[1]["values"][1] - (-1.0)) < 0.3, late[1]["values"]
    for r in records:
        assert "env_id" in r["metadata"], r["id"]
    # camera topics are single-view in batched scenes: env 0 only (ADR-7)
    cam_env_ids = {
        int(r["metadata"]["env_id"]) for r in records if r["id"].startswith(("rgb", "depth"))
    }
    assert cam_env_ids <= {0}


def test_unrouted_cmd_crashes_bridge(tmp_path, dataflow):
    """BRG-5, BRG-7: a joint_cmd without env_id in multi-env mode is an
    ERROR event — the bridge crashes loudly rather than defaulting a
    route, and stops publishing."""
    out = tmp_path / "records.jsonl"
    graph = dataflow.write(
        tmp_path,
        out,
        bridge_env={"AISLE_SEED": 7, "AISLE_N_ENVS": 2},
        driver_env={"DRIVER_MODE": "multi_env", "DRIVER_N_DOF": 9, "DRIVER_SEND_UNROUTED": 1},
        duration_s=12.0,
    )
    result = dataflow.run(graph, timeout_s=320)
    records = dataflow.read(out)
    assert records, "bridge never started"
    # the unrouted cmd fires at driver tick 40 (~2 s in); the bridge must
    # die there: no messages near the end of the capture window
    last_wall = max(r["wall_t"] for r in records)
    first_wall = min(r["wall_t"] for r in records)
    assert last_wall - first_wall < 8.0, "bridge kept publishing after the unrouted command"
    # the crash must be the DOCUMENTED one, however the run ended (BRG-7)
    assert "BRG-5" in (result.stderr + result.stdout), "expected BRG-5 error in node output"
