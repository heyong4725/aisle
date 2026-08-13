"""Fleet graph stamping (design doc §8.4.3; BRG-5) — pure, no dora."""

from pathlib import Path

import pytest
import yaml

from aisle.harness.fleet import SHARED_NODES, aggregate, fleet_graph

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def base_doc():
    return yaml.safe_load((REPO_ROOT / "graphs" / "expert_t0.yaml").read_text())


def test_stamps_policy_nodes_per_agent_and_shares_the_bridge(base_doc):
    doc = fleet_graph(base_doc, 3)
    ids = [n["id"] for n in doc["nodes"]]
    for shared in SHARED_NODES:
        assert ids.count(shared) == 1
    base_policy = [n["id"] for n in base_doc["nodes"] if n["id"] not in SHARED_NODES]
    for agent in range(3):
        for node_id in base_policy:
            assert f"{node_id}-a{agent}" in ids


def test_fleet_keeps_one_barrier_and_rewires_every_participant_closure(base_doc):
    """BRG-1/BRG-5: fleet expansion preserves one valid terminal barrier."""
    doc = fleet_graph(base_doc, 2)
    by_id = {node["id"]: node for node in doc["nodes"]}
    assert "turn-barrier" in by_id
    assert not any(node_id.startswith("turn-barrier-a") for node_id in by_id)
    assert by_id["dora-genesis"]["inputs"]["turn_commit"]["source"] == ("turn-barrier/turn_commit")
    done_sources = {
        spec["source"]
        for name, spec in by_id["turn-barrier"]["inputs"].items()
        if name.startswith("done_")
    }
    assert "rollout-client-a0/turn_done" in done_sources
    assert "rollout-client-a1/turn_done" in done_sources
    assert "budget-guard/turn_done" in done_sources


def test_bridge_declares_n_envs_and_agents_get_pins(base_doc):
    doc = fleet_graph(base_doc, 2)
    by_id = {n["id"]: n for n in doc["nodes"]}
    assert by_id["dora-genesis"]["env"]["AISLE_N_ENVS"] == "2"
    assert by_id["oracle-pose-a0"]["env"]["AISLE_ENV_PIN"] == "0"
    assert by_id["oracle-pose-a1"]["env"]["AISLE_ENV_PIN"] == "1"
    assert by_id["oracle-pose-a1"]["env"]["AISLE_TURN_NODE"] == "oracle-pose-a1"


def test_guard_fans_in_every_agents_executor(base_doc):
    doc = fleet_graph(base_doc, 2)
    guard = next(n for n in doc["nodes"] if n["id"] == "budget-guard")
    assert guard["inputs"]["joint_cmd_0"]["source"] == "ik-trajectory-a0/joint_cmd"
    assert guard["inputs"]["joint_cmd_1"]["source"] == "ik-trajectory-a1/joint_cmd"
    assert guard["inputs"]["gripper_cmd_1"]["source"] == "ik-trajectory-a1/gripper_cmd"
    assert "joint_cmd" not in guard["inputs"]  # base fan-in replaced


def test_policy_edges_rewire_within_the_agent(base_doc):
    """An agent's planner consumes ITS OWN perception, never a peer's;
    shared sources (bridge topics) stay untouched."""
    doc = fleet_graph(base_doc, 2)
    by_id = {n["id"]: n for n in doc["nodes"]}
    planner = by_id["grasp-planner-topdown-a1"]
    assert planner["inputs"]["target_pose"]["source"] == "oracle-pose-a1/target_pose"
    perception = by_id["oracle-pose-a1"]
    assert perception["inputs"]["poses"]["source"].startswith("dora-genesis/")


def test_reset_service_fans_in_client_requests(base_doc):
    doc = fleet_graph(base_doc, 2)
    reset = next(n for n in doc["nodes"] if n["id"] == "reset")
    assert reset["inputs"]["reset_0"]["source"] == "rollout-client-a0/reset"
    assert reset["inputs"]["reset_1"]["source"] == "rollout-client-a1/reset"


def test_single_agent_is_refused(base_doc):
    with pytest.raises(ValueError, match="agents"):
        fleet_graph(base_doc, 1)


def test_aggregate_reports_per_agent_and_fleet(tmp_path):
    a = tmp_path / "a.jsonl"
    a.write_text('{"status": "success", "retries": 0}\n{"status": "fail", "failure": "timeout"}\n')
    b = tmp_path / "b.jsonl"
    b.write_text('{"status": "success", "retries": 0}\n')
    report = aggregate([a, b])
    assert report["agents"] == 2
    assert report["episodes_total"] == 3
    assert report["per_agent"][0]["pass1"] == 0.5
    assert report["per_agent"][1]["pass1"] == 1.0
    assert report["fleet"]["pass1"] == pytest.approx(2 / 3)
