"""MON-6/MON-12/MON-13: typed launch binds authority and retains process evidence."""

import json
from dataclasses import replace

import pytest
from test_turn_node import Raw
from test_typed_validation_launch import _inputs as validation_inputs
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _inputs(tmp_path, source=None):
    from test_treatment_confinement import _attestation

    from aisle.harness.treatment_confinement import compile_macos_profile
    from aisle.harness.typed_execution_bundle import build_execution_bundle
    from aisle.turn_node import Node

    inputs = validation_inputs(tmp_path)
    snapshot = inputs.pop("snapshot")
    receipt = inputs.pop("snapshot_record")
    inputs.pop("embodiment")
    if source is not None:
        path = snapshot / "src/aisle/nodes/segmented_pose.py"
        path.chmod(0o644)
        path.write_bytes(source.encode())
    inputs["bundle"] = tmp_path / "execution"
    inputs["bundle_manifest"] = build_execution_bundle(ROOT, snapshot, inputs["bundle"])
    inputs["source_roots"] = (ROOT, receipt["participant_root"], snapshot)
    inputs["policy"] = replace(
        inputs["policy"],
        visible_roots=(inputs["bundle"],),
        hidden_roots=(*inputs["policy"].hidden_roots, snapshot),
    )
    compiled = compile_macos_profile(inputs["policy"])
    inputs["profile_path"].write_text(compiled.text)
    inputs["attestation"] = _attestation(
        compiled, inputs["profile_path"], tmp_path / "synthetic-adapter"
    )
    inputs.update(node=Node(Raw([]), {}), module="aisle.nodes.segmented_pose", outputs=set())
    return inputs


def test_typed_launch_runs_baseline_through_host_supervisor(tmp_path):
    """MON-12: synthetic adapter tests real process wiring, not OS enforcement."""
    from aisle.harness.typed_worker_launch import launch_typed_worker

    inputs = _inputs(tmp_path)
    result = launch_typed_worker(**inputs)
    assert result["ok"], result
    assert result["worker"]["input_exhausted"]
    assert result["confirmatory_ready"] is False
    assert json.loads((inputs["output"] / "result.json").read_text()) == result
    assert (inputs["output"] / "rpc/worker.json").exists()


@pytest.mark.parametrize(
    "drift", ["profile", "runtime", "bundle", "interpreter", "visible", "write", "module"]
)
def test_typed_launch_refuses_invalid_bindings_before_spawn(tmp_path, monkeypatch, drift):
    """MON-6/MON-13: stale inputs and excess authority cannot launch authored code."""
    from aisle.harness import typed_worker_launch

    inputs = _inputs(tmp_path)
    if drift == "profile":
        inputs["profile_path"].write_text("(allow default)")
    elif drift == "runtime":
        (tmp_path / "runtime-packages/package.py").write_text("VALUE = 2")
    elif drift == "bundle":
        (inputs["bundle"] / "extra.py").write_text("pass")
    elif drift == "interpreter":
        inputs["python_sha256"] = "0" * 64
    elif drift == "module":
        inputs["module"] = "aisle.harness.matched_session"
    elif drift == "visible":
        inputs["policy"] = replace(inputs["policy"], visible_roots=(inputs["bundle"], ROOT))
    else:
        inputs["policy"] = replace(inputs["policy"], output_roots=(inputs["bundle"],))
    calls = []
    monkeypatch.setattr(
        typed_worker_launch, "spawn_isolated_process", lambda *a, **kw: calls.append(a)
    )
    result = typed_worker_launch.launch_typed_worker(**inputs)
    assert not calls
    assert not result["ok"]
    assert result["classification"] == "infrastructure_exclusion"
    assert result["error"]


def test_typed_launch_rejects_postflight_bundle_drift(tmp_path, monkeypatch):
    """MON-13: a successful child cannot promote altered launch inputs."""
    from aisle.harness import typed_worker_launch

    inputs = _inputs(tmp_path)
    supervise = typed_worker_launch.supervise_typed_worker

    def changed(*args, **kwargs):
        result = supervise(*args, **kwargs)
        assert result["ok"]
        (inputs["bundle"] / "added").write_text("drift")
        return result

    monkeypatch.setattr(typed_worker_launch, "supervise_typed_worker", changed)
    result = typed_worker_launch.launch_typed_worker(**inputs)
    assert not result["ok"]
    assert result["classification"] == "infrastructure_exclusion"
    assert result["worker"]["ok"]


def test_typed_launch_retains_authored_failure(tmp_path):
    """MON-12: ordinary module errors remain distinct from infrastructure exclusions."""
    from aisle.harness.typed_worker_launch import launch_typed_worker

    result = launch_typed_worker(**_inputs(tmp_path, "raise RuntimeError('candidate failed')\r\n"))
    assert result["classification"] == "module_result"
    assert not result["ok"]
    assert result["worker"]["rc"] == 1
    assert "candidate failed" in result["worker"]["error"]


def test_typed_launch_cancellation_reaps_child_and_retains_terminal_records(tmp_path, monkeypatch):
    """MON-12: host cancellation closes the child before propagating interruption."""
    from aisle.harness import typed_worker_launch
    from aisle.turn_node import Node

    def interrupted():
        raise KeyboardInterrupt("host cancellation fixture")
        yield  # Make the interruption occur on iteration, after worker startup.

    inputs = _inputs(tmp_path)
    inputs["node"] = Node(Raw(interrupted()), {})
    spawn = typed_worker_launch.spawn_isolated_process
    children = []

    def observed(*args, **kwargs):
        process = spawn(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(typed_worker_launch, "spawn_isolated_process", observed)
    with pytest.raises(KeyboardInterrupt, match="host cancellation fixture"):
        typed_worker_launch.launch_typed_worker(**inputs)
    assert len(children) == 1
    assert children[0].poll() is not None
    result = json.loads((inputs["output"] / "result.json").read_text())
    assert result["classification"] == "infrastructure_exclusion"
    assert not result["ok"]
    assert (inputs["output"] / "rpc/worker.json").exists()


def test_typed_launcher_retains_and_delivers_graph_configuration(tmp_path, monkeypatch):
    """MON-2/MON-13: graph settings cross the full launch path and enter its receipt."""
    import os

    from aisle.harness.typed_worker_launch import launch_typed_worker

    monkeypatch.setenv("AISLE_TASK_TIER", "host-sentinel")
    source = (
        "import os, sys\n"
        "assert os.environ['AISLE_TASK_TIER'] == 'T1'\n"
        "assert sys.argv[1:] == ['--name', 'literal $(value)']\n"
        "from aisle.turn_node import Node\n"
        "list(Node())\n"
    )
    inputs = _inputs(tmp_path, source)
    configuration = {
        "environment": {"AISLE_TASK_TIER": "T1"},
        "arguments": ["--name", "literal $(value)"],
    }
    result = launch_typed_worker(**inputs, configuration=configuration)
    assert result["ok"], result
    assert result["worker"]["configuration"] == configuration
    launch = json.loads((inputs["output"] / "launch.json").read_text())
    assert launch["configuration"] == configuration
    assert os.environ["AISLE_TASK_TIER"] == "host-sentinel"
