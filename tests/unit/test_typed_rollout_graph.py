"""MON-2/MON-12/MON-13: instrumentation distinguishes authored and host transport graphs."""

import hashlib

import pytest
import yaml
from test_typed_graph_preflight import _stage
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("work_launch", [None, 0])
def test_instrumentation_uses_bound_hosts_and_keeps_authored_snapshot(tmp_path, work_launch):
    """MON-2/HAR-4: host transport gains normal trace capture without rewriting authored bytes."""
    from aisle.harness.rollout import instrumented_graph

    stage, record = _stage(tmp_path)
    snapshot = record["source_roots"][1]
    from pathlib import Path

    graph = Path(snapshot) / "graphs/expert_t1.yaml"
    authored = graph.read_bytes()
    output = tmp_path / "run"
    output.mkdir()
    executed = instrumented_graph(
        graph,
        ROOT,
        output,
        graph_snapshot=authored,
        typed_stage=(stage, record),
        work_launch=work_launch,
    )
    doc = yaml.safe_load(executed.read_text())
    if work_launch is not None:
        from aisle.harness.simulator_work import launch_binding

        bridge = next(n for n in doc["nodes"] if n["id"] == "dora-genesis")
        binding = launch_binding(output / "simulator-work", run_id=output.name, launch=work_launch)
        assert all(bridge["env"][name] == value for name, value in binding.items())
    assert graph.read_bytes() == authored
    assert hashlib.sha256(authored).hexdigest() == record["authored_graph_sha256"]
    recorder = next(n for n in doc["nodes"] if n["id"] == "trace-recorder")
    assert "segmented-pose__target_pose" in recorder["inputs"]
    for node in doc["nodes"]:
        if node["id"] in record["hosts"]:
            assert node["path"] == str(ROOT / "src/aisle/harness/typed_node_host.py")
            assert "env" not in node


def test_instrumentation_refuses_stage_for_different_authored_bytes(tmp_path):
    """MON-13: instrumentation cannot substitute a host stage validated for another graph."""
    from pathlib import Path

    from aisle.harness.rollout import instrumented_graph

    stage, record = _stage(tmp_path)
    graph = Path(record["source_roots"][1]) / "graphs/expert_t1.yaml"
    output = tmp_path / "run"
    output.mkdir()
    with pytest.raises(RuntimeError, match="authored"):
        instrumented_graph(
            graph,
            ROOT,
            output,
            graph_snapshot=graph.read_bytes() + b"\n",
            typed_stage=(stage, record),
        )
    assert not (output / "graph.yaml").exists()


def test_instrumentation_refuses_registry_drift_after_staging(tmp_path):
    """MON-13: the authored registry remains bound between validation and instrumentation."""
    from pathlib import Path

    from aisle.harness.rollout import instrumented_graph

    stage, record = _stage(tmp_path)
    snapshot = Path(record["snapshot_record"]["snapshot_root"])
    manifest = snapshot / "registry/manifests/segmented-pose.yaml"
    manifest.chmod(0o644)
    manifest.write_text("id: changed")
    output = tmp_path / "run"
    output.mkdir()
    with pytest.raises(RuntimeError, match="drift"):
        instrumented_graph(
            snapshot / "graphs/expert_t1.yaml", ROOT, output, typed_stage=(stage, record)
        )
    assert not (output / "graph.yaml").exists()
