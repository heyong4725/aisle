"""Regression coverage for issue #497's acceptance fixture."""

import json

import pytest
import yaml
from contract_capture import assert_rates, write_contract_dataflow

pytestmark = pytest.mark.unit


def trace(speed=1.0, sim_rate=100, seconds=12):
    return [
        {
            "wall_t": i / sim_rate / speed,
            "metadata": {
                "sim_time_ns": round(i / sim_rate * 1e9),
                "env_id": 0,
                "seq": i,
                "turn_epoch": 19,
                "turn_id": i,
            },
        }
        for i in range(int(seconds * sim_rate) + 1)
    ]


@pytest.mark.parametrize("speed", [0.6, 1.0, 1.5])
def test_sim_rate_and_wall_liveness_are_separate(speed):
    """TC-4: simulation permits 0.6x and faster-than-real-time delivery."""
    assert_rates("joint_state", trace(speed), 100, 10)


@pytest.mark.parametrize(
    "rows,reason",
    [
        (trace(0.49), "wall"),
        (trace(sim_rate=70), "sim rate"),
        (trace(seconds=3), "coverage"),
    ],
)
def test_bad_rates_and_short_windows_fail(rows, reason):
    """TC-4 (acceptance A1): throttling, wrong cadence and incomplete captures fail."""
    with pytest.raises(AssertionError, match=reason):
        assert_rates("joint_state", rows, 100, 10)


def test_fixed_startup_interval_does_not_hide_later_stalls():
    """TC-4: startup is separate; a stall within nominal load still fails."""
    rows = trace()
    for row in rows[4:]:
        row["wall_t"] += 30
    assert_rates("joint_state", rows, 100, 10)
    for row in rows[200:]:
        row["wall_t"] += 30
    with pytest.raises(AssertionError, match="wall"):
        assert_rates("joint_state", rows, 100, 10)


def test_missing_turn_metadata_fails_even_during_startup():
    """TC-2/BRG-1: warmup does not excuse missing lockstep metadata."""
    rows = trace()
    del rows[0]["metadata"]["turn_id"]
    with pytest.raises(AssertionError, match="metadata"):
        assert_rates("joint_state", rows, 100, 10)


@pytest.mark.parametrize("mode", ["conformance", "reset", "episode"])
def test_contract_graph_uses_complete_lockstep_plan(tmp_path, mode):
    """BRG-1/TC-6/TC-7: all protocol participants close explicit turns."""
    graph = write_contract_dataflow(
        tmp_path, tmp_path / "records.jsonl", bridge_env={}, driver_env={"DRIVER_MODE": mode}
    )
    nodes = {n["id"]: n for n in yaml.safe_load(graph.read_text())["nodes"]}
    plan = json.loads((tmp_path / "turn-plan.json").read_text())
    assert nodes["bridge"]["env"]["AISLE_LOCKSTEP"] == "1"
    assert "turn_commit" in nodes["bridge"]["inputs"]
    for name, participant in plan["participants"].items():
        assert nodes[name]["env"]["AISLE_LOCKSTEP"] == "1"
        assert "turn_done" in participant["outputs"]
        assert "tick" not in nodes[name]["inputs"]
        assert nodes[name]["inputs"]["turn"]["source"] == "turn-barrier/turn"
    await_spec = nodes["recorder"]["env"]["RECORDER_AWAIT"]
    if mode == "reset":
        assert await_spec == "reset_done:1"
        assert plan["participants"]["driver"]["inputs"]["reset_refused"]["edge"] == "episodic"
        assert plan["bridge_inputs"]["reset"]["source"] == "reset-service"
    elif mode == "episode":
        assert await_spec == "episode_result:1"
        assert plan["participants"]["verifier"]["inputs"]["oracle_state"]["source"] == "bridge"
    else:
        assert await_spec == "joint_state:1"
        assert int(nodes["recorder"]["env"]["RECORDER_AWAIT_SIM_NS"]) >= 11_200_000_000


def test_contract_client_ignores_wall_ticks_and_resets_at_boot(monkeypatch):
    """BRG-1/TC-6: bootstrap reset is turn zero; wall ticks never emit commands."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / "fixtures/nodes/contract_driver.py"
    spec = importlib.util.spec_from_file_location("contract_driver", path)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    sent = []

    class FakeNode:
        def __iter__(self):
            yield {"type": "INPUT", "id": "tick", "metadata": {}}
            for turn in range(21):
                yield {"type": "INPUT", "id": "turn", "metadata": {"turn_id": turn}}

        def send_output(self, topic, value, metadata):
            sent.append((topic, value.to_pylist(), metadata))

    monkeypatch.setattr(driver, "Node", FakeNode)
    monkeypatch.setenv("DRIVER_MODE", "reset")
    monkeypatch.setenv("DRIVER_RESET_SEEDS", "1,1,2")
    monkeypatch.setenv("DRIVER_RESET_SPACING", "10")
    driver.main()
    assert [(topic, value) for topic, value, _ in sent] == [
        ("reset", [1, 0]),
        ("reset", [1, 0]),
        ("reset", [2, 0]),
    ]
    assert [meta["request_id"] for _, _, meta in sent] == ["req-1-1", "req-2-1", "req-3-2"]
