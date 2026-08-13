"""ADR-30 validator topology acceptance (issue #175)."""

from __future__ import annotations

import copy

import pytest

from aisle.harness.validate import _clock_errors, compile_turn_plan

pytestmark = pytest.mark.unit


def _manifest(*, inputs=None, outputs=None, provides=None):
    return {
        "inputs": inputs or {},
        "outputs": outputs or {},
        "provides": provides or [],
    }


def _clock_input(*, episodic=False):
    spec = {"schema": "sim_turn_u64", "rate_hz": 100, "is_clock": True}
    if episodic:
        spec["turn_edge"] = "episodic"
    return spec


def _complete_graph():
    """Client/verifier and guard/state cycles, both broken episodically."""
    nodes = [
        {
            "id": "bridge",
            "inputs": {
                "joint_cmd": {"source": "guard/joint_safe", "queue_size": 10},
                "reset": {"source": "client/reset", "queue_size": 10},
                "turn_commit": {
                    "source": "turn-barrier/turn_commit",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
            },
            "outputs": ["state", "sim_turn"],
        },
        {
            "id": "turn-barrier",
            "inputs": {
                "sim_turn": {
                    "source": "bridge/sim_turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_0": {
                    "source": "client/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_1": {
                    "source": "guard/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_2": {
                    "source": "state/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_3": {
                    "source": "verifier/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
            },
            "outputs": ["turn", "turn_commit"],
        },
        {
            "id": "client",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "result": {"source": "verifier/result", "queue_size": 10},
            },
            "outputs": ["goal", "reset", "turn_done"],
        },
        {
            "id": "state",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "state": {"source": "bridge/state", "queue_size": 1},
                "goal": {"source": "client/goal", "queue_size": 10},
                "violation": {"source": "guard/violation", "queue_size": 10},
            },
            "outputs": ["command", "turn_done"],
        },
        {
            "id": "guard",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "command": {"source": "state/command", "queue_size": 10},
            },
            "outputs": ["joint_safe", "violation", "turn_done"],
        },
        {
            "id": "verifier",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "state": {"source": "bridge/state", "queue_size": 1},
                "goal": {"source": "client/goal", "queue_size": 10},
            },
            "outputs": ["result", "turn_done"],
        },
    ]
    for node in nodes:
        if node["id"] == "bridge":
            node["env"] = {
                "AISLE_LOCKSTEP": "1",
                "AISLE_TURN_OUTPUTS": ",".join(node["outputs"]),
            }
        elif node["id"] != "turn-barrier":
            node["env"] = {
                "AISLE_LOCKSTEP": "1",
                "AISLE_TURN_NODE": node["id"],
                "AISLE_TURN_OUTPUTS": ",".join(node["outputs"]),
            }
    manifests = {
        "bridge": _manifest(
            provides=["sim_bridge"],
            inputs={"joint_cmd": {}, "reset": {}, "turn_commit": _clock_input()},
            outputs={"state": {}, "sim_turn": {}},
        ),
        "turn-barrier": _manifest(
            provides=["turn_barrier"],
            inputs={"sim_turn": _clock_input(), "done": _clock_input()},
            outputs={"turn": {}, "turn_commit": {}},
        ),
        "client": _manifest(
            inputs={"turn": _clock_input(), "result": {"turn_edge": "episodic"}},
            outputs={"goal": {}, "reset": {}, "turn_done": {}},
        ),
        "state": _manifest(
            inputs={
                "turn": _clock_input(),
                "state": {},
                "goal": {},
                "violation": {"turn_edge": "episodic"},
            },
            outputs={"command": {}, "turn_done": {}},
        ),
        "guard": _manifest(
            inputs={"turn": _clock_input(), "command": {}},
            outputs={"joint_safe": {}, "violation": {}, "turn_done": {}},
        ),
        "verifier": _manifest(
            inputs={"turn": _clock_input(), "state": {}, "goal": {}},
            outputs={"result": {}, "turn_done": {}},
        ),
    }
    return nodes, manifests


def _codes(nodes, manifests):
    return {entry["code"] for entry in _clock_errors(nodes, manifests)}


def test_good_graph_with_service_and_guard_cycles_passes_clock_validation():
    """VAL-2/BRG-1: cycles containing episodic back-edges are valid."""
    nodes, manifests = _complete_graph()
    assert _clock_errors(nodes, manifests) == []


def test_compiled_runtime_plan_matches_validated_topology():
    """VAL-2/BRG-1: runtime scheduling consumes the topology the validator proved."""
    nodes, manifests = _complete_graph()
    plan = compile_turn_plan(nodes, manifests)
    assert plan["bridge"] == "bridge"
    assert set(plan["participants"]) == {"client", "state", "guard", "verifier"}
    assert plan["participants"]["client"]["inputs"]["result"] == {
        "source": "verifier",
        "output": "result",
        "edge": "episodic",
    }
    assert set(plan["done_ports"].values()) == set(plan["participants"])
    assert plan["bridge_outputs"] == ["sim_turn", "state"]
    assert plan["participants"]["client"]["outputs"] == ["goal", "reset", "turn_done"]


def test_participant_output_config_must_match_the_graph_exactly():
    """CAP-1: runtime watermarks cannot omit a graph output by configuration."""
    nodes, manifests = _complete_graph()
    client = next(node for node in nodes if node["id"] == "client")
    client["env"] = {
        "AISLE_LOCKSTEP": "1",
        "AISLE_TURN_NODE": "client",
        "AISLE_TURN_OUTPUTS": "goal,turn_done",
    }
    assert "CLOCK_PATH_INCOMPLETE" in _codes(nodes, manifests)


def test_latest_wins_clock_is_rejected():
    """VAL-2/CAP-1: structural clocks require explicit positive backpressure queues."""
    nodes, manifests = _complete_graph()
    client = next(node for node in nodes if node["id"] == "client")
    client["inputs"]["turn"] = {"source": "turn-barrier/turn", "queue_size": 1}
    assert "CLOCK_DROPPED" in _codes(nodes, manifests)


def test_clock_from_non_barrier_source_is_rejected():
    """VAL-2: a participant clock must come from the validated terminal barrier."""
    nodes, manifests = _complete_graph()
    client = next(node for node in nodes if node["id"] == "client")
    client["inputs"]["turn"]["source"] = "bridge/state"
    assert "CLOCK_SOURCE_INVALID" in _codes(nodes, manifests)


def test_forward_path_node_without_clock_participation_is_rejected():
    """VAL-2: every node on a path to reset or motion participates."""
    nodes, manifests = _complete_graph()
    state = next(node for node in nodes if node["id"] == "state")
    state["inputs"].pop("turn")
    assert "CLOCK_PATH_INCOMPLETE" in _codes(nodes, manifests)


def test_forward_cycle_without_episodic_edge_is_rejected():
    """VAL-2: every causal cycle must contain an episodic edge."""
    nodes, manifests = _complete_graph()
    manifests = copy.deepcopy(manifests)
    manifests["state"]["inputs"]["violation"].pop("turn_edge")
    assert "CLOCK_CYCLE" in _codes(nodes, manifests)


@pytest.mark.parametrize("mutation", ["missing", "second", "wrong_source"])
def test_bridge_requires_exactly_one_terminal_commit(mutation):
    """VAL-2/BRG-1: exactly one validated terminal commit returns to the bridge."""
    nodes, manifests = _complete_graph()
    bridge = next(node for node in nodes if node["id"] == "bridge")
    if mutation == "missing":
        bridge["inputs"].pop("turn_commit")
    elif mutation == "second":
        bridge["inputs"]["turn_commit_1"] = {
            "source": "turn-barrier/turn_commit",
            "queue_size": 4,
            "queue_policy": "backpressure",
        }
    else:
        bridge["inputs"]["turn_commit"]["source"] = "client/reset"
    assert "CLOCK_COMMIT_COUNT" in _codes(nodes, manifests)
