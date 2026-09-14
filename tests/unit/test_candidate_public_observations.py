"""BND-2/MON-4/MON-13: the admitted L2 pair hides private reset/goal fields."""

import copy
import json
from pathlib import Path

import pytest
import yaml

from aisle.harness.candidates import resolve_candidate

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_realistic_candidate_routes_policy_observations_through_projection(arm):
    """BND-2/MON-4: candidate selection binds public goal, calibration and reset inputs."""
    candidate = resolve_candidate(ROOT, "t1-l2-realistic")
    assert candidate["verifier"] == "realistic"
    path = candidate["typed_graph" if arm == "typed" else "monolithic_template"]
    graph = yaml.safe_load((ROOT / path).read_text())
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert "pilot-policy-surface" in nodes
    assert nodes["pilot-policy-surface"]["path"] == "../src/aisle/harness/pilot_policy_surface.py"
    policy = (
        ["detected-pose", "ik-trajectory", "task-state-machine"]
        if arm == "typed"
        else ["monolith-broker"]
    )
    for name in policy:
        for topic in ("bridge_info", "episode_goal", "reset_done"):
            edge = nodes[name].get("inputs", {}).get(topic)
            if edge is not None:
                assert edge["source"] == f"pilot-policy-surface/{topic}"
    # The trusted evaluator still receives the full controller task record.
    assert (
        nodes["verifier-oracle"]["inputs"]["episode_goal"]["source"]
        == "rollout-client/episode_goal"
    )


@pytest.mark.parametrize(
    "fault", [None, "raw_goal", "raw_reset", "oracle_alias", "oracle_scalar", "trusted_input"]
)
def test_projected_candidate_rejects_authored_boundary_bypass(fault):
    """BND-2/MON-13: topology edits cannot regain private inputs or alter instruments."""
    from aisle.harness.candidates import participant_python
    from aisle.harness.typed_graph_hosts import (
        GraphHostError,
        _source,
        node_configuration,
        replace_authored_nodes,
    )

    candidate = resolve_candidate(ROOT, "t1-l2-realistic")
    baseline = yaml.safe_load((ROOT / candidate["typed_graph"]).read_text())
    authored = copy.deepcopy(baseline)
    nodes = {node["id"]: node for node in authored["nodes"]}
    if fault == "trusted_input":
        nodes["pilot-policy-surface"]["inputs"]["episode_goal"]["source"] = (
            "task-state-machine/episode_feedback"
        )
        error = "trusted instrument inputs"
    elif fault == "oracle_scalar":
        nodes["task-state-machine"]["inputs"]["episode_result"] = "verifier-oracle/episode_result"
        error = "public observation surface"
    elif fault is not None:
        port, source = {
            "raw_goal": ("episode_goal", "rollout-client/episode_goal"),
            "raw_reset": ("reset_done", "reset/reset_done"),
            "oracle_alias": ("private_result", "verifier-oracle/episode_result"),
        }[fault]
        nodes["task-state-machine"]["inputs"][port] = {"source": source, "queue_size": 100}
        error = "public observation surface"
    allowlist = json.loads((ROOT / candidate["documents"]["allowlist"]).read_text())
    files = participant_python(allowlist)
    bindings = {}
    for node in baseline["nodes"]:
        source = _source(node, files)
        if source is not None:
            bindings[node["id"]] = {
                "config_path": "/private/controller/" + node["id"] + ".json",
                "config_sha256": "1" * 64,
                "module": source[4:-3].replace("/", "."),
                "outputs": [name for name in node["outputs"] if name != "turn_done"],
                "wall_outputs": [
                    name
                    for name in node.get("env", {}).get("AISLE_TURN_WALL_OUTPUTS", "").split(",")
                    if name
                ],
                "configuration": node_configuration(node),
            }
    if fault is None:
        staged = replace_authored_nodes(authored, baseline, bindings, ROOT, participant_files=files)
        assert {node["id"]: node["inputs"] for node in staged["nodes"]} == {
            node["id"]: node["inputs"] for node in baseline["nodes"]
        }
        return
    with pytest.raises(GraphHostError, match=error):
        replace_authored_nodes(authored, baseline, bindings, ROOT, participant_files=files)
