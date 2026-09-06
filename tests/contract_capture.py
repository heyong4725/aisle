"""Lockstep TC-A1–A3 graph and simulation-rate assertions (issue #497)."""

import json
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
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
WARMUP_S = 1.0


def assert_rates(topic, rows, rate, duration_s):
    """TC-2/TC-4: complete nominal-load coverage, sim band and wall floor."""
    required = {"sim_time_ns", "env_id", "seq", "turn_epoch", "turn_id"}
    assert rows, f"{topic}: missing messages"
    for row in rows:
        meta = row["metadata"]
        assert required <= meta.keys(), f"{topic}: missing metadata: {meta}"
        assert all(type(meta[key]) is int for key in required), (topic, meta)
        assert meta["env_id"] == 0
    selected = [r for r in rows if r["metadata"]["sim_time_ns"] >= WARMUP_S * 1e9]
    assert len(selected) >= 2, f"{topic}: insufficient coverage"
    start = selected[0]["metadata"]["sim_time_ns"]
    end = next(
        (
            i
            for i, r in enumerate(selected)
            if r["metadata"]["sim_time_ns"] - start >= duration_s * 1e9
        ),
        None,
    )
    assert end is not None, f"{topic}: insufficient sim coverage for {duration_s}s"
    selected = selected[: end + 1]
    seqs = [r["metadata"]["seq"] for r in selected]
    assert all(b == a + 1 for a, b in zip(seqs, seqs[1:], strict=False)), f"{topic}: lost samples"
    sim_span = (selected[-1]["metadata"]["sim_time_ns"] - start) / 1e9
    wall_span = selected[-1]["wall_t"] - selected[0]["wall_t"]
    sim_rate = (len(selected) - 1) / sim_span
    assert 0.8 * rate <= sim_rate <= 1.2 * rate, f"{topic}: sim rate {sim_rate:.2f}"
    assert wall_span > 0, f"{topic}: invalid wall timestamps"
    wall_rate = (len(selected) - 1) / wall_span
    assert wall_rate >= 0.5 * rate, f"{topic}: wall rate {wall_rate:.2f} < {0.5 * rate}"
    return {
        "sim_hz": sim_rate,
        "wall_hz": wall_rate,
        "sim_span_s": sim_span,
        "startup_wall_s": selected[0]["wall_t"] - rows[0]["wall_t"],
    }


def write_contract_dataflow(tmp_path, record_out, *, bridge_env, driver_env, duration_s=10.0):
    """BRG-1: turn-driven client, real reset dispatcher and live oracle stub."""
    mode = driver_env["DRIVER_MODE"]

    def q(source):
        return {"source": source, "queue_size": 100, "queue_policy": "backpressure"}

    def edge(source, output, **kw):
        return {"source": source, "output": output, **kw}

    participants = {}
    nodes = []

    def participant(name, path, inputs, outputs, env=None):
        outputs = sorted([*outputs, "turn_done"])
        participants[name] = {
            "inputs": inputs,
            "outputs": outputs,
            "verdict_bearing": name == "verifier",
        }
        nodes.append(
            {
                "id": name,
                "path": str(ROOT / path),
                "env": {
                    **(env or {}),
                    "AISLE_LOCKSTEP": "1",
                    "AISLE_TURN_NODE": name,
                    "AISLE_TURN_OUTPUTS": ",".join(outputs),
                },
                "inputs": {
                    "turn": q("turn-barrier/turn"),
                    **{key: q(f"{e['source']}/{e['output']}") for key, e in inputs.items()},
                },
                "outputs": outputs,
            }
        )

    driver_inputs = {}
    if mode == "reset":
        driver_inputs = {
            port: edge("reset-service", port, edge="episodic")
            for port in ("reset_done", "reset_refused")
        }
    elif mode == "episode":
        driver_inputs = {
            port: edge("verifier", port, edge="episodic")
            for port in ("episode_feedback", "episode_result")
        }
    participant(
        "driver",
        "tests/fixtures/nodes/contract_driver.py",
        driver_inputs,
        ["joint_cmd", "gripper_cmd", "reset", "episode_goal"],
        {**driver_env, "DRIVER_SEED": bridge_env.get("AISLE_SEED", 7)},
    )
    if mode == "reset":
        participant(
            "reset-service",
            "src/aisle/reset/service.py",
            {"reset": edge("driver", "reset"), "reset_done": edge("bridge", "reset_done")},
            ["bridge_reset", "reset_done", "reset_refused"],
        )
    if mode == "episode":
        participant(
            "verifier",
            "tests/fixtures/nodes/verifier_stub.py",
            {
                "episode_goal": edge("driver", "episode_goal"),
                "oracle_state": edge("bridge", "oracle_state"),
            },
            ["episode_feedback", "episode_result"],
        )
    bridge_inputs = {port: edge("driver", port) for port in ("joint_cmd", "gripper_cmd", "reset")}
    if mode == "reset":
        bridge_inputs["reset"] = edge("reset-service", "bridge_reset")
    plan = {
        "bridge": "bridge",
        "bridge_outputs": sorted(BRIDGE_OUTPUTS),
        "bridge_inputs": bridge_inputs,
        "barrier": "turn-barrier",
        "participants": participants,
        "done_ports": {f"done_{i}": name for i, name in enumerate(participants)},
    }
    plan_path = tmp_path / "turn-plan.json"
    plan_path.write_text(json.dumps(plan, sort_keys=True) + "\n")
    nodes.append(
        {
            "id": "bridge",
            "path": str(ROOT / "src/aisle/nodes/dora_genesis.py"),
            "env": {
                **bridge_env,
                "AISLE_LOCKSTEP": "1",
                "AISLE_TURN_EPOCH": "19",
                "AISLE_TURN_OUTPUTS": ",".join(BRIDGE_OUTPUTS),
            },
            "inputs": {
                **{p: q(f"{e['source']}/{e['output']}") for p, e in bridge_inputs.items()},
                "turn_commit": q("turn-barrier/turn_commit"),
            },
            "outputs": BRIDGE_OUTPUTS,
        }
    )
    nodes.append(
        {
            "id": "turn-barrier",
            "path": str(ROOT / "src/aisle/nodes/turn_barrier.py"),
            "env": {"AISLE_TURN_PLAN": str(plan_path)},
            "inputs": {
                "tick": "dora/timer/millis/100",
                "sim_turn": q("bridge/sim_turn"),
                **{port: q(f"{name}/turn_done") for port, name in plan["done_ports"].items()},
            },
            "outputs": ["turn", "turn_commit"],
        }
    )
    recorder_inputs = {p: q(f"bridge/{p}") for p in BRIDGE_OUTPUTS}
    recorder_inputs["reset"] = q("driver/reset")
    await_sim_ns = 0
    if mode == "reset":
        recorder_inputs["bridge_reset_done"] = recorder_inputs["reset_done"]
        recorder_inputs.update(
            {p: q(f"reset-service/{p}") for p in ("reset_done", "reset_refused")}
        )
        awaited = f"reset_done:{len(driver_env.get('DRIVER_RESET_SEEDS', '1').split(','))}"
    elif mode == "episode":
        recorder_inputs.update(
            {
                "episode_goal": q("driver/episode_goal"),
                "episode_feedback": q("verifier/episode_feedback"),
                "episode_result": q("verifier/episode_result"),
            }
        )
        awaited = "episode_result:1"
    else:
        # Extra 0.2 sim seconds lets every slower sensor cover the entire window.
        awaited = "joint_state:1"
        await_sim_ns = math.ceil((WARMUP_S + duration_s + 0.2) * 1e9)
    nodes.append(
        {
            "id": "recorder",
            "path": str(ROOT / "tests/fixtures/nodes/recorder.py"),
            "env": {
                "RECORDER_OUT": str(record_out),
                "RECORDER_DURATION_S": "0.1",
                "RECORDER_AWAIT": awaited,
                "RECORDER_AWAIT_SIM_NS": str(await_sim_ns),
                "RECORDER_AWAIT_TAIL_S": "0.2",
            },
            "inputs": {"tick": "dora/timer/millis/50", **recorder_inputs},
        }
    )
    graph = tmp_path / "dataflow.yaml"
    graph.write_text(yaml.safe_dump({"nodes": nodes}, sort_keys=False))
    return graph
