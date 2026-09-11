"""MON-2/MON-6/MON-13: stage only the exact successfully validated typed snapshot."""

import copy
import json
from dataclasses import asdict

import pytest
import yaml
from test_typed_validation_launch import _inputs
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _validated(tmp_path, *, direct_python=True):
    from aisle.harness.typed_validation import run_validation

    inputs = _inputs(tmp_path, direct_python=direct_python, worker_packages=True)
    result = run_validation(**inputs)
    assert result["ok"]
    return inputs


def _declarations(inputs):
    # Stage construction accepts serialized launch declarations. Host preflight
    # remains independently mandatory before any Dora transport is attached.
    from aisle.harness.typed_execution_bundle import build_execution_bundle

    bundle = inputs["snapshot"].parent / "execution-bundle"
    manifest = build_execution_bundle(ROOT, inputs["snapshot"], bundle)
    graph = yaml.safe_load((inputs["snapshot"] / "graphs/expert_t1.yaml").read_text())
    names = {"segmented-pose", "grasp-planner-topdown", "ik-trajectory", "task-state-machine"}
    launch = {
        key: copy.deepcopy(inputs[key])
        for key in (
            "policy",
            "profile_path",
            "attestation",
            "python",
            "python_sha256",
            "environment",
            "environment_record",
            "runtime_record",
            "timeout_s",
        )
    }
    launch["policy"] = asdict(launch["policy"])
    launch.update(bundle=bundle, bundle_manifest=manifest, source_roots=[ROOT, inputs["snapshot"]])
    launch = json.loads(json.dumps(launch, default=str))
    return {n["id"]: copy.deepcopy(launch) for n in graph["nodes"] if n["id"] in names}


def test_stage_binds_validation_configs_and_transport_graph(tmp_path):
    """MON-2/MON-13: real validator evidence gates exact source/config/graph staging."""
    from aisle.harness.typed_graph_stage import stage_typed_graph
    from aisle.harness.typed_node_host import load_host_config

    inputs = _validated(tmp_path)
    declarations = _declarations(inputs)
    output = tmp_path / "private/staged"
    record = stage_typed_graph(
        ROOT,
        inputs["snapshot"],
        inputs["snapshot_record"],
        inputs["output"],
        declarations,
        output,
    )
    assert record["snapshot_id"] == inputs["snapshot_record"]["immutable_id"]
    graph = yaml.safe_load((output / "graph.yaml").read_text())
    for node_id, binding in record["hosts"].items():
        config = load_host_config(binding["config_path"], binding["config_sha256"])
        assert config["node_id"] == node_id
        node = next(n for n in graph["nodes"] if n["id"] == node_id)
        assert node["path"] == str(ROOT / "src/aisle/harness/typed_node_host.py")
        assert "env" not in node
    assert (output / "turn-plan.json").read_bytes() == (
        inputs["snapshot"] / "graphs/turn_plans/expert_t1.json"
    ).read_bytes()
    assert record["execution_authorized"] is False


@pytest.mark.parametrize(
    "mutation",
    ["failed", "identity", "snapshot", "source", "process_rc_type", "process_timeout_type"],
)
def test_stage_refuses_unvalidated_or_drifted_inputs(tmp_path, mutation):
    """MON-13: staging cannot pair successful validation with different execution bytes."""
    from aisle.harness.typed_graph_stage import StageError, stage_typed_graph

    inputs = _validated(tmp_path)
    declarations = _declarations(inputs)
    if mutation in {"failed", "identity"}:
        path = inputs["output"] / "result.json"
        result = json.loads(path.read_text())
        result["ok" if mutation == "failed" else "snapshot_id"] = (
            False if mutation == "failed" else "wrong"
        )
        path.write_text(json.dumps(result))
    elif mutation in {"process_rc_type", "process_timeout_type"}:
        path = inputs["output"] / "result.json"
        result = json.loads(path.read_text())
        result["process"]["rc" if mutation == "process_rc_type" else "timed_out"] = (
            False if mutation == "process_rc_type" else 0
        )
        path.write_text(json.dumps(result))
    elif mutation == "snapshot":
        (inputs["snapshot"] / "extra").write_text("drift")
    else:
        declarations["segmented-pose"]["bundle_manifest"]["files"][
            "src/aisle/nodes/segmented_pose.py"
        ]["sha256"] = "0" * 64
    output = tmp_path / "private/staged"
    with pytest.raises(StageError):
        stage_typed_graph(
            ROOT,
            inputs["snapshot"],
            inputs["snapshot_record"],
            inputs["output"],
            declarations,
            output,
        )
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["graph", "extra", "receipt", "authorization_type"])
def test_staged_artifact_verification_refuses_drift(tmp_path, mutation):
    """MON-13: launch preparation cannot consume altered graph/config staging artifacts."""
    from aisle.harness.typed_graph_stage import StageError, stage_typed_graph, verify_graph_stage

    inputs = _validated(tmp_path)
    output = tmp_path / "private/staged"
    record = stage_typed_graph(
        ROOT,
        inputs["snapshot"],
        inputs["snapshot_record"],
        inputs["output"],
        _declarations(inputs),
        output,
    )
    verify_graph_stage(output, record)
    if mutation == "graph":
        path = output / "graph.yaml"
        path.chmod(0o644)
        path.write_text("nodes: []")
    elif mutation == "extra":
        (output / "extra.py").write_text("pass")
    elif mutation == "authorization_type":
        altered = copy.deepcopy(record)
        altered["execution_authorized"] = 0
        (output / "stage.json").write_text(json.dumps(altered))
    else:
        (output / "stage.json").write_text("{}")
    with pytest.raises(StageError):
        verify_graph_stage(output, record)
